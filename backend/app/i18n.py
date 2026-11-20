"""Languages: detect what a text is written in, and localize the few user-facing API messages.

Supported languages are Chinese ('zh', the default) and English ('en'). The agent answers in the
language of each question; the UI language is sent by the frontend as Accept-Language.
"""
import re

LANGUAGES = ('zh', 'en')
DEFAULT_LANGUAGE = 'zh'
CJK = re.compile(r'[㐀-鿿豈-﫿]')
LATIN = re.compile(r'[A-Za-z]')


def detect_language(text: str) -> str:
    """'zh' unless the text is mostly Latin letters. A Chinese character carries roughly as much
    as a short word, so one CJK character counts as three letters: "HMI 加班次产量报表" is Chinese,
    "Ask 周明远 about the FAT date" is English."""
    cjk, latin = len(CJK.findall(text)), len(LATIN.findall(text))
    if not cjk and not latin:
        return DEFAULT_LANGUAGE
    return 'zh' if cjk * 3 >= latin else 'en'


def parse_accept_language(header: str | None) -> str:
    for part in (header or '').split(','):
        code = part.split(';')[0].strip().lower()[:2]
        if code in LANGUAGES:
            return code
    return DEFAULT_LANGUAGE


MESSAGES = {
    'project_not_found': ('项目不存在', 'Project not found'),
    'conversation_not_found': ('会话不存在', 'Conversation not found'),
    'document_not_found': ('文档不存在', 'Document not found'),
    'project_name_required': ('项目名称不能为空', 'Project name is required'),
    'unknown_domain': ('未知领域：{domain}', 'Unknown domain: {domain}'),
    'unsupported_type': ('不支持的文件类型', 'Unsupported file type'),
    'no_text': ('没有解析出文本（扫描版 PDF 需要先 OCR）', 'No text found (scanned PDFs need OCR first)'),
    'empty_message': ('消息不能为空', 'Message is empty'),
    'llm_not_configured': ('未配置大模型：在 backend/.env 中设置 LLM_API_KEY 后重启',
                           'No LLM configured: set LLM_API_KEY in backend/.env and restart'),
}


def t(key: str, lang: str, **values) -> str:
    zh, en = MESSAGES[key]
    return (en if lang == 'en' else zh).format(**values)
