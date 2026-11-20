"""Domain packs: the industry-specific part of the agent, kept in YAML files under backend/domains/.

A pack sets the assistant's role, the verdict labels and their definitions, extra judgment rules,
example questions for the UI, and (optionally) the eval set it is measured on. Retrieval,
citation and output-format rules are shared by every pack and stay in TEMPLATES.

Languages are kept apart on purpose:
- the prompt is written in the UI language (`zh` top-level fields, or the optional `en:` block;
  fields the block leaves out fall back to Chinese, except `eval`, which belongs to one language);
- the answer is in whatever language the user asks in, or asks for; quotes in another language
  come with a translation. The latest question's language is detected and stated at the end of the
  prompt: without it, English questions over Chinese documents were answered in Chinese 79% of the
  time (the retrieved passages pull the answer toward their language);
- verdicts are language-neutral codes, [[verdict:out_of_scope]], shown as a label in the UI language;
- search keywords follow the documents' language, which the prompt states (see system_prompt).
"""
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .i18n import DEFAULT_LANGUAGE, LANGUAGES

BACKEND = Path(__file__).resolve().parents[1]
DOMAINS_DIR = BACKEND / 'domains'
DEFAULT_DOMAIN = 'automation_delivery'
VERDICT = re.compile(r'\[\[\s*verdict\s*:\s*([a-z_]+)\s*\]\]', re.IGNORECASE)

TEMPLATES = {
    'zh': """你是{role}。今天是 {today}。

工作方式：
1. 涉及项目事实（范围、条款、进度、问题、参数、人员）时，先用 search_documents 检索，必要时换关键词多次检索；不要凭记忆或常识编造项目事实。
2. 只依据检索到的片段作答。每条来自文档的事实后紧跟引用标记，格式为 [c片段编号]，例如 [c12]；多个来源写成 [c12][c15]。
3. 检索不到就明确说明在项目文档中未找到，并建议需要补充的资料。
4. {judgment}时，回答第一行只写结论代码 [[verdict:代码]]（界面会把它显示成结论标签），代码从下面{count}个中选一个；从第二行起，引用{basis}等具体条款说明依据。
{definitions}
5. 文档之间有冲突时指出冲突，并说明哪份文件更新或效力更高。
6. 用用户提问所用的语言回答；用户指定了回答语言时，按用户的要求。引用的文档原文与回答语言不同时，先给原文，再在括号里给出译文。先给结论，再给依据；条理清楚，不要冗长。""",
    'en': """You are {role}. Today is {today}.

How to work:
1. For project facts (scope, clauses, schedule, issues, parameters, people), search with search_documents first, several times with different keywords if needed. Never make up project facts from memory or general knowledge.
2. Answer only from the retrieved passages. Put a citation right after every fact taken from the documents, in the form [c<passage number>], e.g. [c12]; several sources as [c12][c15].
3. If nothing relevant is found, say clearly that it is not in the project documents, and suggest which documents to add.
4. When {judgment}, the first line of the answer is only the verdict code [[verdict:<code>]] (the UI shows it as a label), choosing one of the {count} codes below; from the second line on, cite the specific clauses of {basis} that support it.
{definitions}
5. If documents conflict, point out the conflict and say which document is newer or takes precedence.
6. Answer in the language of the user's question; if the user asks for a particular language, use it. When you quote a document written in a different language from your answer, give the original first and a translation in parentheses. Conclusion first, then evidence; be clear and concise.""",
}
COUNTS = {'zh': '二三四五', 'en': ('two', 'three', 'four', 'five')}
RULES_HEADING = {'zh': '本领域的判断规则：', 'en': 'Rules for this domain:'}
LANGUAGE_NAMES = {'zh': {'zh': '中文', 'en': '英文'}, 'en': {'zh': 'Chinese', 'en': 'English'}}
QUESTION_LINE = {
    'zh': '8. 用户最新的提问是用{language}写的：用{language}回答，除非用户要求用其他语言。',
    'en': "8. The user's latest question is written in {language}: answer in {language}, unless the user asked "
          'for another language.',
}
DOCUMENTS_LINE = {
    'zh': ('7. 本项目文档的语言：{languages}。检索词要用文档的语言（例如英文文档用英文关键词），与提问的语言无关。',
           '{n} 份{name}', '、'),
    'en': ("7. Language of this project's documents: {languages}. Search with keywords in the documents' language "
           '(e.g. Chinese keywords for Chinese documents), whatever language the question is in.', '{name} ({n})', ', '),
}


@dataclass(frozen=True)
class Label:
    name: str
    definition: str


@dataclass(frozen=True)
class Domain:
    id: str
    lang: str
    name: str
    description: str
    role: str
    judgment: str
    basis: str
    labels: dict[str, Label]  # verdict code → label in this language
    rules: tuple[str, ...]
    examples: tuple[str, ...]
    eval_cases: Path | None
    eval_documents: Path | None

    def system_prompt(self, today: str, document_languages: dict[str, int] | None = None,
                      question_language: str | None = None) -> str:
        """`document_languages` counts the project's documents by language, e.g. {'en': 9};
        `question_language` is the language the latest question is written in."""
        open_, close, colon = ('（', '）', '：') if self.lang == 'zh' else (' (', ')', ': ')
        prompt = TEMPLATES[self.lang].format(
            role=self.role, today=today, judgment=self.judgment, basis=self.basis,
            count=COUNTS[self.lang][len(self.labels) - 2],
            definitions='\n'.join(f'   - {code}{open_}{label.name}{close}{colon}{label.definition}'
                                  for code, label in self.labels.items()))
        if document_languages:
            line, item, separator = DOCUMENTS_LINE[self.lang]
            names = LANGUAGE_NAMES[self.lang]
            prompt += '\n' + line.format(languages=separator.join(
                item.format(n=n, name=names.get(lang, lang)) for lang, n in sorted(document_languages.items())))
        if question_language:
            prompt += '\n' + QUESTION_LINE[self.lang].format(
                language=LANGUAGE_NAMES[self.lang].get(question_language, question_language))
        if self.rules:
            prompt += f'\n\n{RULES_HEADING[self.lang]}\n' + '\n'.join(f'- {rule}' for rule in self.rules)
        return prompt

    def parse_verdict(self, answer: str) -> str | None:
        """The verdict code the answer starts with, if it is one of this domain's codes."""
        match = VERDICT.search(answer)
        code = match.group(1).lower() if match else None
        return code if code in self.labels else None

    def code_for(self, name: str) -> str:
        """Verdict code for a label name in this language (eval sets are labelled by name)."""
        return next(code for code, label in self.labels.items() if label.name == name)

    def public(self) -> dict:
        return {'id': self.id, 'name': self.name, 'description': self.description, 'examples': list(self.examples),
                'labels': [{'code': code, 'name': label.name, 'definition': label.definition}
                           for code, label in self.labels.items()]}


@dataclass(frozen=True)
class DomainPack:
    id: str
    variants: dict[str, Domain]

    def get(self, lang: str) -> Domain:
        return self.variants.get(lang) or self.variants[DEFAULT_LANGUAGE]


def _domain(domain_id: str, lang: str, data: dict, source: str) -> Domain:
    labels = {str(code): Label(str(value['name']), str(value['definition']).strip())
              for code, value in data['labels'].items()}
    if not 2 <= len(labels) <= 5:
        raise ValueError(f'{source}: labels needs 2–5 entries, got {len(labels)}')
    if bad := [code for code in labels if not re.fullmatch(r'[a-z_]+', code)]:
        raise ValueError(f'{source}: verdict codes must be lowercase letters and underscores: {bad}')
    evaluation = data.get('eval') or {}
    resolve = lambda key: (BACKEND / evaluation[key]).resolve() if evaluation.get(key) else None  # noqa: E731
    return Domain(id=domain_id, lang=lang, name=data['name'], description=data.get('description', ''),
                  role=data['role'], judgment=data['judgment'], basis=data['basis'], labels=labels,
                  rules=tuple(data.get('rules') or ()), examples=tuple(data.get('examples') or ()),
                  eval_cases=resolve('cases'), eval_documents=resolve('documents'))


def load_pack(path: Path) -> DomainPack:
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    base = {key: value for key, value in data.items() if key not in LANGUAGES}
    variants = {DEFAULT_LANGUAGE: _domain(path.stem, DEFAULT_LANGUAGE, base, path.name)}
    for lang in LANGUAGES:
        if lang != DEFAULT_LANGUAGE and data.get(lang):
            merged = {**{k: v for k, v in base.items() if k != 'eval'}, **data[lang]}
            variants[lang] = _domain(path.stem, lang, merged, f'{path.name} ({lang})')
            if variants[lang].labels.keys() != variants[DEFAULT_LANGUAGE].labels.keys():
                raise ValueError(f'{path.name}: the {lang} block must use the same verdict codes')
    return DomainPack(path.stem, variants)


def load_domains(directory: Path = DOMAINS_DIR) -> dict[str, DomainPack]:
    packs = {path.stem: load_pack(path) for path in sorted(directory.glob('*.yaml'))}
    if DEFAULT_DOMAIN not in packs:
        raise ValueError(f'{directory} must contain {DEFAULT_DOMAIN}.yaml')
    return {DEFAULT_DOMAIN: packs.pop(DEFAULT_DOMAIN), **packs}  # default first: the UI preselects it
