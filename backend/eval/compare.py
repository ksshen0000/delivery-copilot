"""Print a markdown table comparing eval runs.

    uv run python -m eval.compare                 # every run under eval/results
    uv run python -m eval.compare deepseek-hybrid deepseek-bm25
"""
import json
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / 'results'
COLUMNS = [('name', '运行'), ('domain', '领域'), ('lang', '提问'), ('docs_lang', '文档'), ('model', '模型'), ('retrieval', '检索'), ('chunk_size', '分块'), ('top_k', 'top_k'),
           ('repeats', '重复'), ('accuracy', '准确率'), ('macro_f1', 'Macro-F1'), ('ambiguous_recall', '歧义召回'),
           ('evidence_retrieved', '证据检索率'), ('evidence_cited', '证据引用率'),
           ('invalid_citation_rate', '无效引用率'), ('answer_language_match', '回答语言一致'), ('avg_tool_calls', '工具调用'), ('avg_latency_s', '耗时(s)'),
           ('avg_tokens_in', '输入token')]


def main(names):
    paths = [RESULTS / n / 'summary.json' for n in names] if names else sorted(RESULTS.glob('*/summary.json'))
    print('| ' + ' | '.join(title for _, title in COLUMNS) + ' |')
    print('|' + '---|' * len(COLUMNS))
    for path in paths:
        summary = json.loads(path.read_text(encoding='utf-8'))
        summary.setdefault('domain', 'automation_delivery')  # runs made before domain packs
        summary.setdefault('lang', 'zh')
        summary.setdefault('docs_lang', summary['lang'])
        ambiguous = summary['per_label'].get('存在歧义') or summary['per_label'].get('Ambiguous') or {}
        summary['ambiguous_recall'] = ambiguous.get('recall', '')
        print('| ' + ' | '.join(str(summary.get(key, '')) for key, _ in COLUMNS) + ' |')


if __name__ == '__main__':
    main(sys.argv[1:])
