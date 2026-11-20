"""Acceptance readiness: list the project's acceptance criteria, then check each one against the evidence.

    collect ──▶ check × N (in parallel, LangGraph Send) ──▶ END

collect   searches the documents for acceptance criteria and asks the model to list them (structured output);
check     for one criterion, searches for test results, measurements and open issues, and asks the model
          whether it is met, at risk, or has no evidence yet, citing passage ids.

Citations are kept only if they point at passages the model was actually shown.
"""
import operator
import re
from typing import Annotated, Literal, TypedDict

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

from .db import Database
from .retrieval import Hit, Retriever

Status = Literal['met', 'at_risk', 'no_evidence']

CRITERIA_QUERIES = {
    'zh': ['验收 标准 要求', '终验收 SAT 条件', '性能指标 要求', '验收 遗留问题', '参数 要求 不大于 不小于',
           '培训 资料 交付'],
    'en': ['acceptance criteria requirements', 'site acceptance test SAT conditions', 'performance indicators requirement',
           'acceptance open issues', 'parameter requirement no more than at least', 'training documentation deliverables'],
}
EVIDENCE_HINT = {'zh': '实测 结果 问题', 'en': 'measured result issue'}
PROMPTS = {
    'zh': {
        'collect': '下面是项目文档中与验收相关的片段。列出终验收（SAT）时设备和交付必须满足、可以核查的标准，每条一项，最多 12 条：'
                   '性能和技术参数（如节拍、成功率、换型时间）、功能、问题关闭要求、培训和资料交付。'
                   '不要列付款、工期、违约、验收程序等合同条款；合并重复的，忽略只属于出厂预验收（FAT）且已经通过的要求。'
                   '每条写明出处片段编号（chunk_id）。'
                   '标准沿用文档原文的措辞和语言，保留数值和单位。\n\n{passages}',
        'check': '验收标准：{criterion}\n\n下面是项目文档中检索到的片段。判断这条标准目前的状态：\n'
                 '- met：有实测、记录或关闭的问题表明已经满足；\n'
                 '- at_risk：有证据表明尚未满足、实测不达标，或存在未关闭的相关问题；\n'
                 '- no_evidence：还没有实测记录或其他能判断的证据。\n'
                 '用{answer}写一句话的发现（包含关键数值和日期），并列出支持判断的片段编号。只依据片段，不要推测。\n\n{passages}',
        'answer': '中文',
    },
    'en': {
        'collect': 'Below are passages about acceptance from the project documents. List the criteria the equipment '
                   'and delivery must meet, and that can be checked, at the final site acceptance test (SAT): one per '
                   'item, at most 12: performance and technical parameters (e.g. cycle time, success rate, changeover '
                   'time), functions, issue-closure requirements, training and documentation deliverables. Leave out '
                   'contract terms such as payment, schedule, penalties and acceptance procedure; merge duplicates and '
                   'skip requirements that belong only to the factory acceptance test (FAT) and were already passed. '
                   'Give the source passage id (chunk_id) of each. Keep each criterion in the documents\' own wording '
                   'and language, with the numbers and units of the original.\n\n{passages}',
        'check': 'Acceptance criterion: {criterion}\n\nBelow are passages retrieved from the project documents. Judge '
                 'the criterion\'s current status:\n'
                 '- met: measurements, records or closed issues show it is met;\n'
                 '- at_risk: evidence shows it is not met yet, a measurement falls short, or a related issue is open;\n'
                 '- no_evidence: no measurement or other evidence to judge it yet.\n'
                 'Write a one-sentence finding in {answer} (with the key numbers and dates) and list the passage ids '
                 'that support it. Rely on the passages only; do not speculate.\n\n{passages}',
        'answer': 'English',
    },
}


class Criterion(BaseModel):
    text: str = Field(description='The criterion, with numbers and units as in the source')
    chunk_id: int = Field(description='Id of the passage the criterion comes from, as an integer: 12, not c12')


class Criteria(BaseModel):
    criteria: list[Criterion]


class Check(BaseModel):
    status: Status
    finding: str = Field(description='One sentence: what the evidence shows')
    citations: list[int] = Field(description='Ids of the passages that support the finding, as integers: [26, 12], '
                                             'not [c26, c12]')


class Item(TypedDict):
    criterion: str
    source: int | None
    status: Status
    finding: str
    citations: list[int]


class State(TypedDict, total=False):
    project_id: int
    lang: str
    criteria: list[Criterion]
    items: Annotated[list[Item], operator.add]
    sources: Annotated[dict[int, dict], lambda a, b: {**a, **b}]


class CheckInput(TypedDict):
    project_id: int
    lang: str
    criterion: Criterion


BARE_ID = re.compile(r'(?<=[\[,:\s])c(\d+)(?=\s*[,\]}])')


async def _structured(runnable, schema: type[BaseModel], prompt: str, tries: int = 3):
    """Structured output with two repairs. The model copies the [c26] passage labels into the JSON as bare
    `c26`, which isn't valid JSON: strip the c and parse again. Anything else that fails: retry."""
    for _ in range(tries):
        result = await runnable.ainvoke(prompt)
        if result['parsed'] is not None:
            return result['parsed']
        for call in result['raw'].invalid_tool_calls:
            try:
                return schema.model_validate_json(BARE_ID.sub(r'\1', call['args'] or ''))
            except ValueError:
                pass
    return None


def _passages(hits: list[Hit]) -> str:
    return '\n\n'.join(f'[c{h.chunk_id}] {h.filename} · {h.location}\n{h.text}' for h in hits)


def _search(retriever: Retriever, project_id: int, queries: list[str], top_k: int) -> list[Hit]:
    seen: dict[int, Hit] = {}
    for query in queries:
        for hit in retriever.search(project_id, query, top_k=top_k):
            seen.setdefault(hit.chunk_id, hit)
    return list(seen.values())


def build_readiness_graph(llm, db: Database, retriever: Retriever, top_k: int = 6):
    # Streamed responses came back with no function call for DeepSeek structured output: use a non-streaming copy.
    if getattr(llm, 'streaming', False):
        llm = llm.model_copy(update={'streaming': False})
    list_criteria = llm.with_structured_output(Criteria, method='function_calling', include_raw=True)
    judge = llm.with_structured_output(Check, method='function_calling', include_raw=True)

    def queries_for(project_id: int, base: dict[str, list[str]]) -> list[str]:
        languages = db.document_languages(project_id) or {'zh': 1}
        return [q for lang in languages for q in base.get(lang, base['zh'])]

    async def collect(state: State):
        prompts = PROMPTS[state['lang']]
        hits = _search(retriever, state['project_id'], queries_for(state['project_id'], CRITERIA_QUERIES), top_k)
        if not hits:
            return {'criteria': [], 'items': [], 'sources': {}}
        result = await _structured(list_criteria, Criteria, prompts['collect'].format(answer=prompts['answer'],
                                                                             passages=_passages(hits)))
        known = {h.chunk_id for h in hits}
        criteria = [c for c in (result.criteria if result else []) if c.text.strip()][:12]
        for criterion in criteria:
            if criterion.chunk_id not in known:
                criterion.chunk_id = 0  # unknown source: don't cite it
        return {'criteria': criteria, 'sources': {h.chunk_id: h.to_dict() for h in hits if
                                                  h.chunk_id in {c.chunk_id for c in criteria}}}

    def fan_out(state: State):
        return [Send('check', {'project_id': state['project_id'], 'lang': state['lang'], 'criterion': c})
                for c in state['criteria']] or END

    async def check(task: CheckInput):
        prompts, criterion = PROMPTS[task['lang']], task['criterion']
        hint = ' '.join(EVIDENCE_HINT[lang] for lang in db.document_languages(task['project_id']) or {'zh': 1})
        hits = _search(retriever, task['project_id'], [criterion.text, f'{criterion.text} {hint}'], top_k)
        result = await _structured(judge, Check, prompts['check'].format(
            criterion=criterion.text, answer=prompts['answer'], passages=_passages(hits)))
        if result is None:
            result = Check(status='no_evidence', finding=UNJUDGED[task['lang']], citations=[])
        shown = {h.chunk_id: h for h in hits}
        citations = [c for c in dict.fromkeys(result.citations) if c in shown]
        item = Item(criterion=criterion.text, source=criterion.chunk_id or None, status=result.status,
                    finding=result.finding, citations=citations)
        return {'items': [item], 'sources': {c: shown[c].to_dict() for c in citations}}

    graph = StateGraph(State)
    graph.add_node('collect', collect)
    graph.add_node('check', check)
    graph.add_edge(START, 'collect')
    graph.add_conditional_edges('collect', fan_out, ['check', END])
    graph.add_edge('check', END)
    return graph.compile()


UNJUDGED = {'zh': '模型未能给出判断，请人工核查。', 'en': 'The model could not judge this one; check it by hand.'}
STATUS_ORDER = {'at_risk': 0, 'no_evidence': 1, 'met': 2}


async def run_readiness(graph, project_id: int, lang: str) -> dict:
    usage = UsageMetadataCallbackHandler()  # token totals across the parallel model calls, for tracing
    state = await graph.ainvoke({'project_id': project_id, 'lang': lang, 'items': [], 'sources': {}},
                                {'run_name': 'readiness', 'metadata': {'project_id': project_id, 'lang': lang},
                                 'callbacks': [usage]})
    order = {c.text: i for i, c in enumerate(state.get('criteria', []))}
    items = sorted(state.get('items', []), key=lambda i: (STATUS_ORDER[i['status']], order.get(i['criterion'], 99)))
    counts = {status: sum(i['status'] == status for i in items) for status in STATUS_ORDER}
    tokens = {key: sum(u.get(key, 0) for u in usage.usage_metadata.values()) for key in ('input_tokens', 'output_tokens')}
    return {'items': items, 'counts': counts, 'sources': state.get('sources', {}), 'tokens': tokens,
            'model_calls': 1 + len(items)}
