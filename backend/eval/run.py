"""Scope-judgment eval: run every case of a domain pack's eval set through the agent and score it.

The cases, the project documents and the verdict labels come from the domain pack (backend/domains/).
From backend/:
    uv run python -m eval.run --name deepseek-hybrid                # default domain: automation_delivery
    uv run python -m eval.run --name deepseek-hybrid-en --lang en   # the domain's English eval set
    uv run python -m eval.run --name cross-zh-en --docs-lang en      # Chinese questions, English documents
    uv run python -m eval.run --name gpt54mini-bm25 --provider openai --retrieval bm25
    uv run python -m eval.run --name smoke --ids S01,S07 --repeats 1

Each run writes eval/results/<name>/cases.jsonl (one line per case and repeat) and summary.json.
"""
import argparse
import asyncio
import json
import re
import statistics
import tempfile
import time
from collections import Counter
from pathlib import Path

import yaml
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from app import documents
from app.agent import build_graph
from app.config import get_settings
from app.db import Database
from app.domains import DEFAULT_DOMAIN, load_domains
from app.i18n import detect_language
from app.retrieval import Embedder, Retriever

ROOT = Path(__file__).resolve().parent
CITATION = re.compile(r'\[c(\d+)\]')
# Quoted document text may be in another language than the answer; leave it out when judging the answer's language.
QUOTED = re.compile(r'“[^”]*”|"[^"\n]*"|「[^」]*」|\[\[[^\]]*\]\]|\[c\d+\]')


def answer_language(answer: str) -> str:
    return detect_language(QUOTED.sub(' ', answer))
PROVIDERS = {
    'deepseek': {'base_url': 'https://api.deepseek.com', 'key': 'llm_api_key', 'model': 'deepseek-chat'},
    'openai': {'base_url': 'https://api.openai.com/v1', 'key': 'openai_api_key', 'model': 'gpt-5.4-mini'},
}


def build_llm(provider, model, settings):
    spec = PROVIDERS[provider]
    kwargs = {'model': model or spec['model'], 'base_url': spec['base_url'],
              'api_key': getattr(settings, spec['key']), 'timeout': 180, 'max_retries': 3}
    if not kwargs['model'].startswith(('gpt-5', 'o3', 'o4')):  # reasoning models reject temperature
        kwargs['temperature'] = settings.llm_temperature
    if not kwargs['api_key']:
        raise SystemExit(f'Missing {spec["key"].upper()} in backend/.env')
    return ChatOpenAI(**kwargs)


def ingest(db: Database, embedder, chunk_size: int, documents_dir: Path) -> int:
    with db.connect() as conn:
        project_id = conn.execute("INSERT INTO projects (name) VALUES ('eval')").lastrowid
    for path in sorted(documents_dir.iterdir()):
        lang, sections = documents.parse_document(path)
        chunks = documents.chunk(sections, max_chars=chunk_size,
                                 overlap=min(120, chunk_size // 4))
        vectors = embedder.embed([f'{c.location}\n{c.text}' for c in chunks]) if embedder else None
        with db.connect() as conn:
            document_id = conn.execute('INSERT INTO documents (project_id, filename, n_chunks, lang) VALUES (?, ?, ?, ?)',
                                       (project_id, path.name, len(chunks), lang)).lastrowid
            conn.executemany(
                'INSERT INTO chunks (document_id, project_id, ordinal, location, text, embedding) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                [(document_id, project_id, i, c.location, c.text,
                  vectors[i].tobytes() if vectors is not None else None) for i, c in enumerate(chunks)])
    return project_id


def matches(chunk, evidence):
    return evidence['doc'] in chunk['filename'] and evidence['contains'] in chunk['text']


async def run_case(graph, domain, chunks_by_id, project_id, case, repeat):
    # The UI language is taken to be the question's language: it picks the prompt and tool descriptions.
    config = {'configurable': {'thread_id': f"{case['id']}-{repeat}", 'project_id': project_id,
                               'domain': domain.id, 'lang': domain.lang},
              'recursion_limit': 16}
    started = time.monotonic()
    try:
        state = await graph.ainvoke({'messages': [HumanMessage(case['request'])]}, config)
    except Exception as error:
        return {'id': case['id'], 'repeat': repeat, 'gold': case['label'], 'pred': None,
                'error': f'{type(error).__name__}: {error}'}
    messages = state['messages'][1:]
    answer = messages[-1].content if messages and isinstance(messages[-1], AIMessage) else ''
    queries = [call['args'].get('query', '') for m in messages if isinstance(m, AIMessage)
               for call in m.tool_calls]
    retrieved = {source['chunk_id'] for m in messages if isinstance(m, ToolMessage)
                 and isinstance(m.artifact, list) for source in m.artifact}
    cited = [int(value) for value in dict.fromkeys(CITATION.findall(answer))]
    usage = Counter()
    for m in messages:
        if isinstance(m, AIMessage) and m.usage_metadata:
            usage.update({'input': m.usage_metadata['input_tokens'], 'output': m.usage_metadata['output_tokens']})
    evidence = case['evidence']
    return {
        'id': case['id'], 'repeat': repeat, 'category': case['category'],
        'gold': case['label'], 'pred': domain.labels[code].name if (code := domain.parse_verdict(answer)) else None,
        'evidence_retrieved': sum(any(matches(chunks_by_id[i], e) for i in retrieved) for e in evidence) / len(evidence),
        'evidence_cited': sum(any(matches(chunks_by_id[i], e) for i in cited if i in chunks_by_id)
                              for e in evidence) / len(evidence),
        'citations': len(cited),
        'invalid_citations': [i for i in cited if i not in retrieved],
        'tool_calls': len(queries), 'queries': queries,
        'latency_s': round(time.monotonic() - started, 2),
        'tokens_in': usage['input'], 'tokens_out': usage['output'],
        'answer_lang': answer_language(answer), 'question_lang': detect_language(case['request']),
        'answer': answer,
    }


def summarize(results, args, model, labels):
    scored = [r for r in results if not r.get('error')]
    n = len(scored)
    confusion = {gold: {pred: 0 for pred in (*labels, None)} for gold in labels}
    for r in scored:
        confusion[r['gold']][r['pred']] += 1
    per_label = {}
    for label in labels:
        tp = confusion[label][label]
        predicted = sum(confusion[g][label] for g in labels)
        actual = sum(confusion[label].values())
        precision = tp / predicted if predicted else 0.
        recall = tp / actual if actual else 0.
        per_label[label] = {'precision': round(precision, 3), 'recall': round(recall, 3), 'support': actual,
                            'f1': round(2 * precision * recall / (precision + recall), 3) if tp else 0.}
    by_repeat = [statistics.mean(r['pred'] == r['gold'] for r in scored if r['repeat'] == k)
                 for k in sorted({r['repeat'] for r in scored})]
    total_citations = sum(r['citations'] for r in scored)
    mean = lambda key: round(statistics.mean(r[key] for r in scored), 3) if scored else None  # noqa: E731
    return {
        'name': args.name, 'domain': args.domain, 'lang': args.lang, 'docs_lang': args.docs_lang, 'provider': args.provider, 'model': model, 'retrieval': args.retrieval,
        'top_k': args.top_k, 'chunk_size': args.chunk_size, 'repeats': args.repeats,
        'cases': n, 'errors': len(results) - n,
        'accuracy': round(statistics.mean(by_repeat), 3) if by_repeat else None,
        'accuracy_by_repeat': [round(a, 3) for a in by_repeat],
        'macro_f1': round(statistics.mean(v['f1'] for v in per_label.values()), 3),
        'per_label': per_label,
        'confusion': {g: {str(p): c for p, c in row.items()} for g, row in confusion.items()},
        'unparsed': sum(r['pred'] is None for r in scored),
        'evidence_retrieved': mean('evidence_retrieved'),
        'evidence_cited': mean('evidence_cited'),
        'invalid_citation_rate': round(sum(len(r['invalid_citations']) for r in scored) / total_citations, 3)
        if total_citations else 0.,
        'answer_language_match': round(statistics.mean(r['answer_lang'] == r['question_lang'] for r in scored), 3)
        if scored else None,
        'avg_tool_calls': mean('tool_calls'), 'avg_latency_s': mean('latency_s'),
        'avg_tokens_in': mean('tokens_in'), 'avg_tokens_out': mean('tokens_out'),
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--name', required=True)
    parser.add_argument('--domain', default=DEFAULT_DOMAIN, help='domain pack whose eval set to run')
    parser.add_argument('--lang', choices=('zh', 'en'), default='zh',
                        help='language of the questions (and of the UI, which sets the prompt language)')
    parser.add_argument('--docs-lang', choices=('zh', 'en'),
                        help='language of the documents, default same as --lang; a different one tests cross-language '
                             'questions (requests and labels from --lang, documents and evidence from --docs-lang)')
    parser.add_argument('--provider', choices=PROVIDERS, default='deepseek')
    parser.add_argument('--model', help='override the provider default model')
    parser.add_argument('--retrieval', choices=('bm25', 'hybrid'), default='hybrid')
    parser.add_argument('--top-k', type=int, default=6)
    parser.add_argument('--chunk-size', type=int, default=600)
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--concurrency', type=int, default=8)
    parser.add_argument('--ids', help='comma-separated case ids to run')
    args = parser.parse_args()

    settings = get_settings()
    domains = load_domains()
    if args.domain not in domains:
        raise SystemExit(f'Unknown domain {args.domain}; available: {", ".join(domains)}')
    domain = domains[args.domain].variants.get(args.lang)
    if not (domain and domain.eval_cases and domain.eval_documents):
        raise SystemExit(f'Domain {args.domain} has no {args.lang} eval set (eval.cases / eval.documents in its YAML)')
    cases = yaml.safe_load(domain.eval_cases.read_text(encoding='utf-8'))
    args.docs_lang = args.docs_lang or args.lang
    docs_domain = domains[args.domain].variants.get(args.docs_lang)
    if not (docs_domain and docs_domain.eval_cases and docs_domain.eval_documents):
        raise SystemExit(f'Domain {args.domain} has no {args.docs_lang} eval set')
    if docs_domain is not domain:  # same cases (by id), evidence quoted from the other language's documents
        evidence = {c['id']: c['evidence'] for c in yaml.safe_load(docs_domain.eval_cases.read_text(encoding='utf-8'))}
        cases = [{**c, 'evidence': evidence[c['id']]} for c in cases]
    unknown = {c['label'] for c in cases} - {label.name for label in domain.labels.values()}
    if unknown:
        raise SystemExit(f'{domain.eval_cases.name}: labels {unknown} are not in domain {domain.id} ({args.lang})')
    if args.ids:
        wanted = set(args.ids.split(','))
        cases = [c for c in cases if c['id'] in wanted]
    llm = build_llm(args.provider, args.model, settings)
    embedder = None
    if args.retrieval == 'hybrid':
        if not settings.embeddings_enabled:
            raise SystemExit('hybrid retrieval needs EMBEDDING_* in backend/.env; use --retrieval bm25')
        embedder = Embedder(settings)

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / 'eval.db')
        project_id = ingest(db, embedder, args.chunk_size, docs_domain.eval_documents)
        chunks_by_id = {row['id']: row for row in db.query(
            'SELECT c.id, c.text, d.filename FROM chunks c JOIN documents d ON d.id = c.document_id')}
        graph = build_graph(llm, db, Retriever(db, embedder), InMemorySaver(), domains, args.top_k)
        semaphore = asyncio.Semaphore(args.concurrency)
        done = 0

        async def bounded(case, repeat):
            nonlocal done
            async with semaphore:
                result = await run_case(graph, domain, chunks_by_id, project_id, case, repeat)
            done += 1
            mark = 'ERR' if result.get('error') else ('✓' if result['pred'] == result['gold'] else '✗')
            print(f"[{done}/{len(cases) * args.repeats}] {case['id']} {mark} gold={case['label']} pred={result.get('pred')}",
                  flush=True)
            return result

        results = await asyncio.gather(*(bounded(c, k) for k in range(args.repeats) for c in cases))

    results.sort(key=lambda r: (r['id'], r['repeat']))
    out = ROOT / 'results' / args.name
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'cases.jsonl').open('w', encoding='utf-8') as file:
        for r in results:
            file.write(json.dumps(r, ensure_ascii=False) + '\n')
    summary = summarize(results, args, llm.model_name, tuple(label.name for label in domain.labels.values()))
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"\naccuracy={summary['accuracy']} {summary['accuracy_by_repeat']} macro_f1={summary['macro_f1']} "
          f"unparsed={summary['unparsed']} errors={summary['errors']}")
    print(f"evidence retrieved={summary['evidence_retrieved']} cited={summary['evidence_cited']} "
          f"invalid_citation_rate={summary['invalid_citation_rate']}")
    print(f"answer_language_match={summary['answer_language_match']}")
    print(f"tool_calls={summary['avg_tool_calls']} latency={summary['avg_latency_s']}s "
          f"tokens in/out={summary['avg_tokens_in']}/{summary['avg_tokens_out']}")
    for r in results:
        if not r.get('error') and r['pred'] != r['gold']:
            print(f"  ✗ {r['id']}#{r['repeat']} gold={r['gold']} pred={r['pred']}")
    print(f'Wrote {out}')


if __name__ == '__main__':
    asyncio.run(main())
