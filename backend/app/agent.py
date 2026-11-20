"""LangGraph agent: an LLM node that can call document tools, looping until it answers.

The system prompt comes from the domain pack named in config['configurable']['domain'], written in the
UI language (config['configurable']['lang']), and tells the model which languages the project's documents
are in so it searches with keywords in those languages. The answer itself is in whatever language the
user asks in; see domains.py. Tool descriptions and tool messages follow the UI language.
"""
from datetime import date

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from .db import Database
from .domains import DEFAULT_DOMAIN, DomainPack
from .i18n import DEFAULT_LANGUAGE, LANGUAGES, detect_language
from .retrieval import Retriever



def build_tools(db: Database, retriever: Retriever, top_k: int):
    @tool(response_format='content_and_artifact')
    def search_documents(query: str, config: RunnableConfig):
        """在当前项目的文档库中检索相关片段。query 用具体的关键词或短句，例如“验收 连续运行 时长”。"""
        project_id = config['configurable']['project_id']
        hits = retriever.search(project_id, query, top_k=top_k)
        if not hits:
            if config['configurable'].get('lang') == 'en':
                return 'No matching passages. Try other keywords.', []
            return '没有检索到相关片段。可以换关键词再试。', []
        content = '\n\n'.join(f'[c{hit.chunk_id}] 《{hit.filename}》 {hit.location}\n{hit.text}'
                              for hit in hits)
        return content, [hit.to_dict() for hit in hits]

    @tool
    def list_documents(config: RunnableConfig) -> str:
        """列出当前项目已上传的全部文档及上传时间。"""
        rows = db.query('SELECT filename, created_at FROM documents WHERE project_id = ? ORDER BY id',
                        (config['configurable']['project_id'],))
        if config['configurable'].get('lang') == 'en':
            return '\n'.join(f"- {row['filename']} (uploaded {row['created_at']})" for row in rows) or 'No documents yet.'
        return '\n'.join(f"- {row['filename']}（上传于 {row['created_at']}）" for row in rows) or '项目中还没有文档。'

    return [search_documents, list_documents]


# Tool descriptions are part of the prompt, so they follow the prompt's language. Chinese ones are the docstrings.
TOOL_DESCRIPTIONS = {'en': {
    'search_documents': "Search the current project's documents for relevant passages. query: specific keywords or "
                        'a short phrase in the language of the documents, e.g. "acceptance continuous run hours".',
    'list_documents': 'List every document in the current project with its upload time.',
}}


def localized_tools(tools, lang: str):
    descriptions = TOOL_DESCRIPTIONS.get(lang, {})
    return [t.model_copy(update={'description': descriptions[t.name]}) if t.name in descriptions else t for t in tools]


def build_graph(llm, db: Database, retriever: Retriever, checkpointer, domains: dict[str, DomainPack], top_k: int = 6):
    tools = build_tools(db, retriever, top_k)
    models = {lang: llm.bind_tools(localized_tools(tools, lang)) for lang in LANGUAGES}

    async def agent(state: MessagesState, config: RunnableConfig):
        configurable = config['configurable']
        domain = domains[configurable.get('domain', DEFAULT_DOMAIN)].get(configurable.get('lang', DEFAULT_LANGUAGE))
        languages = db.document_languages(configurable['project_id'])
        question = next((m.content for m in reversed(state['messages']) if isinstance(m, HumanMessage)), '')
        system = SystemMessage(domain.system_prompt(date.today().isoformat(), languages,
                                                    detect_language(question) if isinstance(question, str) else None))
        return {'messages': [await models[domain.lang].ainvoke([system, *state['messages']])]}

    graph = StateGraph(MessagesState)
    graph.add_node('agent', agent)
    graph.add_node('tools', ToolNode(tools))
    graph.add_edge(START, 'agent')
    graph.add_conditional_edges('agent', tools_condition)
    graph.add_edge('tools', 'agent')
    return graph.compile(checkpointer=checkpointer)
