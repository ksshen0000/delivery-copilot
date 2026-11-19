"""Domain packs: the industry-specific part of the agent, kept in YAML files under backend/domains/.

A pack sets the assistant's role, the verdict labels and their definitions, extra judgment rules,
example questions for the UI, and (optionally) the eval set it is measured on. Retrieval,
citation and output-format rules are shared by every pack and stay in SYSTEM_TEMPLATE.
"""
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import yaml

BACKEND = Path(__file__).resolve().parents[1]
DOMAINS_DIR = BACKEND / 'domains'
DEFAULT_DOMAIN = 'automation_delivery'

SYSTEM_TEMPLATE = """你是{role}。今天是 {today}。

工作方式：
1. 涉及项目事实（范围、条款、进度、问题、参数、人员）时，先用 search_documents 检索，必要时换关键词多次检索；不要凭记忆或常识编造项目事实。
2. 只依据检索到的片段作答。每条来自文档的事实后紧跟引用标记，格式为 [c片段编号]，例如 [c12]；多个来源写成 [c12][c15]。
3. 检索不到就明确说明“项目文档中未找到”，并建议需要补充的资料。
4. {judgment}时，回答开头单独写一行{options}（{count}选一），再引用{basis}等具体条款说明依据。
{definitions}
5. 文档之间有冲突时指出冲突，并说明哪份文件更新或效力更高。
6. 用中文回答，先给结论，再给依据；条理清楚，不要冗长。"""


@dataclass(frozen=True)
class Domain:
    id: str
    name: str
    description: str
    role: str
    judgment: str
    basis: str
    labels: dict[str, str]
    rules: tuple[str, ...]
    examples: tuple[str, ...]
    eval_cases: Path | None
    eval_documents: Path | None

    def system_prompt(self, today: str) -> str:
        options = [f'“结论：{label}”' for label in self.labels]
        prompt = SYSTEM_TEMPLATE.format(
            role=self.role, today=today, judgment=self.judgment, basis=self.basis, count='二三四五'[len(self.labels) - 2],
            options=''.join(options[:-1]) + '或' + options[-1],
            definitions='\n'.join(f'   - {label}：{text}' for label, text in self.labels.items()))
        if self.rules:
            prompt += '\n\n本领域的判断规则：\n' + '\n'.join(f'- {rule}' for rule in self.rules)
        return prompt

    @cached_property
    def conclusion(self) -> re.Pattern:
        """Matches the "结论：<label>" line; longer labels first so one label's prefix can't shadow another."""
        options = '|'.join(re.escape(label) for label in sorted(self.labels, key=len, reverse=True))
        return re.compile(rf'结论[^：:\n]{{0,8}}[：:][\s*#]*({options})')

    def public(self) -> dict:
        return {'id': self.id, 'name': self.name, 'description': self.description,
                'labels': self.labels, 'examples': list(self.examples)}


def load_domain(path: Path) -> Domain:
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    labels = data['labels']
    if not 2 <= len(labels) <= 5:
        raise ValueError(f'{path.name}: labels needs 2–5 entries, got {len(labels)}')
    evaluation = data.get('eval') or {}
    resolve = lambda key: (BACKEND / evaluation[key]).resolve() if evaluation.get(key) else None  # noqa: E731
    return Domain(id=path.stem, name=data['name'], description=data.get('description', ''),
                  role=data['role'], judgment=data['judgment'], basis=data['basis'],
                  labels={str(k): str(v).strip() for k, v in labels.items()},
                  rules=tuple(data.get('rules') or ()), examples=tuple(data.get('examples') or ()),
                  eval_cases=resolve('cases'), eval_documents=resolve('documents'))


def load_domains(directory: Path = DOMAINS_DIR) -> dict[str, Domain]:
    domains = {path.stem: load_domain(path) for path in sorted(directory.glob('*.yaml'))}
    if DEFAULT_DOMAIN not in domains:
        raise ValueError(f'{directory} must contain {DEFAULT_DOMAIN}.yaml')
    return domains
