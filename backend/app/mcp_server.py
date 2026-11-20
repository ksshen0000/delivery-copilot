"""MCP server: the project workspace as tools for any MCP client (Claude Code, Claude Desktop, Cursor…).

    cd backend && uv run python -m app.mcp_server          # stdio transport

It reads the same database as the web app (DATA_DIR), so projects and documents uploaded in the UI
are available here. All tools are read-only; `ask` runs the full agent and returns the answer, the
verdict and the passages it cites.
"""
import re
import uuid

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from .agent import build_graph
from .config import get_settings
from .db import Database
from .domains import DEFAULT_DOMAIN, load_domains
from .main import default_llm
from .retrieval import Embedder, Retriever
from .tracing import Run, enable_langsmith

INSTRUCTIONS = """Delivery Copilot: project documents (contracts, technical agreements, meeting minutes, site reports,
issue logs) organised by project. Call list_projects first to get a project id. Use search_documents for raw
passages, or ask for a full answer: the agent searches several times, cites passages as [c<id>] and, for scope
questions, starts with a verdict code such as [[verdict:out_of_scope]]."""
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
CITATION = re.compile(r'\[c(\d+)\]')


class Verdict(BaseModel):
    code: str
    name: str


class Citation(BaseModel):
    chunk_id: int
    filename: str
    location: str
    text: str


class Answer(BaseModel):
    answer: str
    verdict: Verdict | None
    citations: list[Citation]


def create_server(settings=None, llm=None) -> MCPServer:
    settings = settings or get_settings()
    db = Database(settings.data_dir / 'app.db')
    retriever = Retriever(db, Embedder(settings) if settings.embeddings_enabled else None)
    domains = load_domains()
    enable_langsmith(settings)
    model = llm if llm is not None else default_llm(settings)
    graph = build_graph(model, db, retriever, InMemorySaver(), domains, settings.top_k) if model else None
    server = MCPServer('delivery-copilot', instructions=INSTRUCTIONS)

    def project(project_id: int) -> dict:
        row = db.one('SELECT * FROM projects WHERE id = ?', (project_id,))
        if not row:
            raise ToolError(f'No project with id {project_id}; call list_projects')
        return row

    @server.tool(annotations=READ_ONLY)
    def list_projects() -> list[dict]:
        """List projects with their domain and number of documents."""
        return db.query('SELECT p.id, p.name, p.domain, COUNT(d.id) AS documents FROM projects p '
                        'LEFT JOIN documents d ON d.project_id = p.id GROUP BY p.id ORDER BY p.id DESC')

    @server.tool(annotations=READ_ONLY)
    def list_documents(project_id: int) -> list[dict]:
        """List a project's documents: filename, language, number of chunks, upload time."""
        project(project_id)
        return db.query('SELECT id, filename, lang, n_chunks, created_at FROM documents WHERE project_id = ? '
                        'ORDER BY id', (project_id,))

    @server.tool(annotations=READ_ONLY)
    def search_documents(project_id: int, query: str, top_k: int = 6) -> list[dict]:
        """Hybrid keyword + vector search over a project's documents. Write the query in the documents'
        language (see list_documents). Each hit has chunk_id, filename, location (section, page or rows) and text."""
        project(project_id)
        return [{k: v for k, v in hit.to_dict().items() if k != 'document_id'}
                for hit in retriever.search(project_id, query, top_k=max(1, min(top_k, 20)))]

    @server.tool(annotations=READ_ONLY)
    async def ask(project_id: int, question: str, lang: str = 'zh') -> Answer:
        """Ask the project agent a question, e.g. whether a customer request is within the contract scope.
        It answers in the question's language. `lang` ('zh' or 'en') sets the language of its instructions and
        of the verdict name. Returns the answer, the verdict (code and name, if any) and the cited passages."""
        if graph is None:
            raise ToolError('No LLM configured: set LLM_API_KEY in backend/.env')
        row = project(project_id)
        domain = domains.get(row['domain'], domains[DEFAULT_DOMAIN]).get(lang)
        run = Run(project_id, 'mcp', question, domain.id, domain.lang)
        config = {'configurable': {'thread_id': uuid.uuid4().hex, 'project_id': project_id,
                                   'domain': domain.id, 'lang': domain.lang},
                  'recursion_limit': 16, 'metadata': run.metadata(), 'run_name': 'mcp_ask'}
        try:
            state = await graph.ainvoke({'messages': [HumanMessage(question)]}, config)
        except Exception as error:
            run.save(db, error=f'{type(error).__name__}: {error}')
            raise ToolError(f'The agent failed: {type(error).__name__}: {error}') from error
        messages = state['messages']
        for message in messages:
            run.observe(message)
        answer = messages[-1].content if isinstance(messages[-1], AIMessage) else ''
        retrieved = {s['chunk_id']: s for m in messages if isinstance(m, ToolMessage) and isinstance(m.artifact, list)
                     for s in m.artifact}
        cited = [retrieved[int(i)] for i in dict.fromkeys(CITATION.findall(answer)) if int(i) in retrieved]
        code = domain.parse_verdict(answer)
        run.save(db, verdict=code)
        return Answer(answer=answer, verdict=Verdict(code=code, name=domain.labels[code].name) if code else None,
                      citations=[Citation(**{k: c[k] for k in Citation.model_fields}) for c in cited])

    return server


def main():
    create_server().run('stdio')


if __name__ == '__main__':
    main()
