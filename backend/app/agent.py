"""LangGraph agent: an LLM node that can call document tools, looping until it answers.

The system prompt comes from the domain pack named in config['configurable']['domain'].
"""
from datetime import date

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from .db import Database
from .domains import DEFAULT_DOMAIN, Domain
from .retrieval import Retriever



def build_tools(db: Database, retriever: Retriever, top_k: int):
    @tool(response_format='content_and_artifact')
    def search_documents(query: str, config: RunnableConfig):
        """在当前项目的文档库中检索相关片段。query 用具体的关键词或短句，例如“验收 连续运行 时长”。"""
        project_id = config['configurable']['project_id']
        hits = retriever.search(project_id, query, top_k=top_k)
        if not hits:
            return '没有检索到相关片段。可以换关键词再试。', []
        content = '\n\n'.join(f'[c{hit.chunk_id}] 《{hit.filename}》 {hit.location}\n{hit.text}'
                              for hit in hits)
        return content, [hit.to_dict() for hit in hits]

    @tool
    def list_documents(config: RunnableConfig) -> str:
        """列出当前项目已上传的全部文档及上传时间。"""
        rows = db.query('SELECT filename, created_at FROM documents WHERE project_id = ? ORDER BY id',
                        (config['configurable']['project_id'],))
        return '\n'.join(f"- {row['filename']}（上传于 {row['created_at']}）" for row in rows) or '项目中还没有文档。'

    return [search_documents, list_documents]


def build_graph(llm, db: Database, retriever: Retriever, checkpointer, domains: dict[str, Domain], top_k: int = 6):
    tools = build_tools(db, retriever, top_k)
    model = llm.bind_tools(tools)

    async def agent(state: MessagesState, config: RunnableConfig):
        domain = domains[config['configurable'].get('domain', DEFAULT_DOMAIN)]
        system = SystemMessage(domain.system_prompt(date.today().isoformat()))
        return {'messages': [await model.ainvoke([system, *state['messages']])]}

    graph = StateGraph(MessagesState)
    graph.add_node('agent', agent)
    graph.add_node('tools', ToolNode(tools))
    graph.add_edge(START, 'agent')
    graph.add_conditional_edges('agent', tools_condition)
    graph.add_edge('tools', 'agent')
    return graph.compile(checkpointer=checkpointer)
