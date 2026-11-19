"""Review labels in Excel, then write them back to cases.yaml.

    uv run python -m eval.review export            # → eval/review.xlsx
    uv run python -m eval.review import            # applies the 复核标签 / 复核依据 columns
    uv run python -m eval.review export --domain software_impl   # → eval/review_software_impl.xlsx

On import, a filled 复核标签 replaces the label and marks the case reviewed; 复核依据, when
filled, replaces the rationale. Rows with an empty 复核标签 are left unchanged. Pass
--results <run name> to export to include that run's predictions as a reference column.
"""
import argparse
import json
from collections import Counter
from pathlib import Path

import yaml
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from app.domains import DEFAULT_DOMAIN, load_domains

ROOT = Path(__file__).resolve().parent
HEADER = ['编号', '类别', '客户需求', '草稿标签', '草稿依据', '证据（文档：原文）', '模型预测', '复核标签', '复核依据']


def export(results_name, cases_path, sheet_path, labels):
    cases = yaml.safe_load(cases_path.read_text(encoding='utf-8'))
    predictions = {}
    if results_name:
        for line in (ROOT / 'results' / results_name / 'cases.jsonl').read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            predictions.setdefault(row['id'], []).append(row.get('pred') or '未解析')
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '复核'
    sheet.append(HEADER)
    for case in cases:
        votes = Counter(predictions.get(case['id'], []))
        sheet.append([case['id'], case['category'], case['request'], case['label'], case['rationale'],
                      '\n'.join(f"{e['doc']}：{e['contains']}" for e in case['evidence']),
                      ' / '.join(f'{label}×{n}' for label, n in votes.most_common()),
                      case['label'] if case.get('reviewed') else '', ''])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for column, width in zip('ABCDEFGHI', (7, 9, 48, 10, 42, 40, 18, 11, 30)):
        sheet.column_dimensions[column].width = width
    fill = PatternFill('solid', fgColor='FFF4CC')
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical='top')
        row[7].fill = row[8].fill = fill
    validation = DataValidation(type='list', formula1=f'"{",".join(labels)}"', allow_blank=True)
    sheet.add_data_validation(validation)
    validation.add(f'H2:H{sheet.max_row}')
    sheet.freeze_panes = 'D2'
    workbook.save(sheet_path)
    print(f'Wrote {sheet_path}')


def import_(cases_path, sheet_path, labels):
    cases = yaml.safe_load(cases_path.read_text(encoding='utf-8'))
    by_id = {case['id']: case for case in cases}
    rows = list(load_workbook(sheet_path, read_only=True).active.iter_rows(min_row=2, values_only=True))
    changed = reviewed = 0
    for row in rows:
        case_id, label, rationale = row[0], (row[7] or '').strip(), (row[8] or '').strip()
        if not case_id or not label:
            continue
        if label not in labels:
            raise SystemExit(f'{case_id}: 复核标签必须是 {"/".join(labels)}，得到 {label!r}')
        case = by_id[case_id]
        changed += case['label'] != label
        case['label'], case['reviewed'] = label, True
        if rationale:
            case['rationale'] = rationale
        reviewed += 1
    header = cases_path.read_text(encoding='utf-8').split('\n- id:')[0].rstrip() + '\n\n'
    body = '\n'.join(yaml.safe_dump([case], allow_unicode=True, sort_keys=False, width=1000) for case in cases)
    cases_path.write_text(header + body, encoding='utf-8')
    print(f'{reviewed} cases reviewed, {changed} labels changed; '
          f'{sum(c.get("reviewed", False) for c in cases)}/{len(cases)} reviewed in total')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=('export', 'import'))
    parser.add_argument('--results', help='run name whose predictions to show on export')
    parser.add_argument('--domain', default=DEFAULT_DOMAIN, help='domain pack whose eval set to review')
    args = parser.parse_args()
    domain = load_domains()[args.domain]
    if not domain.eval_cases:
        raise SystemExit(f'Domain {domain.id} has no eval set')
    sheet = ROOT / ('review.xlsx' if domain.id == DEFAULT_DOMAIN else f'review_{domain.id}.xlsx')
    labels = tuple(domain.labels)
    if args.action == 'export':
        export(args.results, domain.eval_cases, sheet, labels)
    else:
        import_(domain.eval_cases, sheet, labels)
