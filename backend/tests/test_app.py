import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from openpyxl import Workbook

from app import documents
from app.agent import build_tools, localized_tools
from app.config import Settings
from app.db import Database
from app.domains import DEFAULT_DOMAIN, load_domains
from app.i18n import detect_language
from app.main import create_app
from app.retrieval import tokenize

SAMPLE = Path(__file__).resolve().parents[2] / 'sample_data'


@pytest.fixture(scope='session')
def sample_dir():
    project = SAMPLE / 'project_a'
    if not project.exists():
        subprocess.run([sys.executable, str(SAMPLE / 'generate.py')], check=True)
    return project


SYSTEM_PROMPTS = []  # every system prompt the scripted model was called with


class ScriptedModel(BaseChatModel):
    """Searches once with the user's question, then answers citing the first hit."""

    @property
    def _llm_type(self):
        return 'scripted'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        SYSTEM_PROMPTS.append(messages[0].content)
        last = messages[-1]
        if isinstance(last, ToolMessage):
            chunk_id = last.artifact[0]['chunk_id'] if last.artifact else None
            reply = AIMessage(f'节拍要求为 ≤ 45 s/件 [c{chunk_id}]')
        else:
            reply = AIMessage('', tool_calls=[{'id': 'call_1', 'name': 'search_documents',
                                                'args': {'query': last.content}}])
        return ChatResult(generations=[ChatGeneration(message=reply)])


def parse_sse(text):
    events, event = [], None
    for line in text.splitlines():
        if line.startswith('event:'):
            event = line.split(':', 1)[1].strip()
        elif line.startswith('data:') and event:
            events.append((event, json.loads(line.split(':', 1)[1])))
    return events


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, llm_api_key='')
    with TestClient(create_app(settings, llm=ScriptedModel())) as test_client:
        yield test_client


def test_parse_all_sample_formats(sample_dir):
    for path in sample_dir.iterdir():
        chunks = documents.chunk(documents.parse(path))
        assert chunks, path.name
        assert all(len(c.text) <= 600 for c in chunks)
    agreement = documents.parse(sample_dir / '技术协议_V1.2.docx')
    performance = next(s for s in agreement if s.location == '第四章 性能指标')
    assert '≤ 45 s/件' in performance.text
    sheet = documents.parse(sample_dir / '问题清单.xlsx')
    assert sheet[0].location.startswith('工作表「问题清单」第2-')
    assert '编号：P-001' in sheet[0].text


def test_upload_search_and_chat(client, sample_dir):
    project = client.post('/api/projects', json={'name': '华衡转向节产线'}).json()
    files = [('files', (p.name, p.read_bytes())) for p in sorted(sample_dir.iterdir())]
    files.append(('files', ('notes.exe', b'x')))
    results = client.post(f"/api/projects/{project['id']}/documents", files=files).json()
    assert sum('id' in r for r in results) == 9
    assert [r for r in results if 'error' in r][0]['filename'] == 'notes.exe'

    hits = client.get(f"/api/projects/{project['id']}/search", params={'q': '单件节拍要求'}).json()
    assert any('45 s/件' in hit['text'] for hit in hits[:3])
    hits = client.get(f"/api/projects/{project['id']}/search", params={'q': '读码 追溯'}).json()
    assert any(hit['filename'] == '技术协议_V1.2.docx' for hit in hits)  # top_k=6

    conversation = client.post(f"/api/projects/{project['id']}/conversations").json()
    response = client.post(f"/api/conversations/{conversation['id']}/chat", json={'message': '单件节拍要求是多少'})
    events = parse_sse(response.text)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == 'tool_call' and kinds[-1] == 'done'
    result = next(data for kind, data in events if kind == 'tool_result')
    assert result['sources'] and result['id'] == 'call_1'
    text = ''.join(data['text'] for kind, data in events if kind == 'text')
    assert f"[c{result['sources'][0]['chunk_id']}]" in text

    history = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert [m['role'] for m in history] == ['user', 'assistant']
    assert [p['type'] for p in history[1]['parts']] == ['tool_call', 'tool_result', 'text']
    titles = client.get(f"/api/projects/{project['id']}/conversations").json()
    assert titles[0]['title'] == '单件节拍要求是多少'


def test_delete_document_removes_it_from_search(client, sample_dir):
    project = client.post('/api/projects', json={'name': 'p'}).json()
    path = sample_dir / '技术协议_V1.2.docx'
    document = client.post(f"/api/projects/{project['id']}/documents",
                           files=[('files', (path.name, path.read_bytes()))]).json()[0]
    assert client.get(f"/api/projects/{project['id']}/search", params={'q': '节拍'}).json()
    assert client.delete(f"/api/documents/{document['id']}").json() == {'ok': True}
    assert client.get(f"/api/projects/{project['id']}/search", params={'q': '节拍'}).json() == []


def test_chat_without_llm_reports_configuration_error(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, llm_api_key='')
    with TestClient(create_app(settings)) as client:
        project = client.post('/api/projects', json={'name': 'p'}).json()
        conversation = client.post(f"/api/projects/{project['id']}/conversations").json()
        events = parse_sse(client.post(f"/api/conversations/{conversation['id']}/chat",
                                       json={'message': 'hi'}).text)
        assert events[0][0] == 'error' and 'LLM_API_KEY' in events[0][1]['message']


def test_domain_packs():
    packs = load_domains()
    assert {'automation_delivery', 'software_impl'} <= set(packs)
    automation, software = packs['automation_delivery'].get('zh'), packs['software_impl'].get('zh')
    prompt = automation.system_prompt('2026-01-01')
    assert '[[verdict:代码]]' in prompt and '   - out_of_scope（范围外）：文档明确排除' in prompt
    assert '用用户提问所用的语言回答' in prompt and '括号里给出译文' in prompt
    assert '本项目文档的语言' not in prompt and '本领域的判断规则' not in prompt
    assert '7. 本项目文档的语言：9 份英文。' in automation.system_prompt('x', {'en': 9})
    assert '8. 用户最新的提问是用英文写的：用英文回答，除非用户要求用其他语言。' in automation.system_prompt('x', None, 'en')
    assert '需求变更' in software.system_prompt('x') and '本领域的判断规则' in software.system_prompt('x')

    english = packs['automation_delivery'].get('en')
    assert english.lang == 'en' and '   - ambiguous (Ambiguous): Not mentioned' in english.system_prompt('x')
    assert 'English (2), Chinese (7)' in english.system_prompt('x', {'zh': 7, 'en': 2})
    for domain in (automation, english):  # the verdict is a code, whatever language the answer is in
        assert domain.parse_verdict('[[verdict:out_of_scope]]\nThe contract…') == 'out_of_scope'
        assert domain.parse_verdict('**[[ Verdict: AMBIGUOUS ]]**') == 'ambiguous'
        assert domain.parse_verdict('[[verdict:change_request]]') is None  # another domain's code
        assert domain.parse_verdict('结论：范围外') is None
    assert english.code_for('Out of scope') == automation.code_for('范围外') == 'out_of_scope'
    assert packs['software_impl'].get('en').rules and packs['software_impl'].get('fr').lang == 'zh'  # fallback

    for pack in packs.values():  # every eval set only uses its own variant's label names
        for domain in pack.variants.values():
            if domain.eval_cases:
                labels = {case['label'] for case in yaml.safe_load(domain.eval_cases.read_text(encoding='utf-8'))}
                assert labels <= {label.name for label in domain.labels.values()}, (domain.id, domain.lang)
                assert domain.eval_documents.is_dir()


def test_language_detection():
    assert detect_language('甲方要求在 HMI 上增加按班次的产量报表，在合同范围内吗？') == 'zh'
    assert detect_language('Is a per-shift output report on the HMI in scope?') == 'en'
    assert detect_language('Ask 周明远 when the FAT is scheduled') == 'en'
    assert detect_language('VMC-03 的信号') == 'zh' and detect_language('') == 'zh'
    assert tokenize('Open issues reported') == tokenize('open issue report')
    english_tools = localized_tools(build_tools(None, None, 6), 'en')
    assert english_tools[0].name == 'search_documents' and english_tools[0].description.startswith('Search')
    assert build_tools(None, None, 6)[0].description.startswith('在当前项目')  # Chinese stays the original


def test_english_documents_get_english_locations(tmp_path):
    md = tmp_path / 'minutes.md'
    md.write_text('# Kickoff\n\nMilestones were confirmed.\n', encoding='utf-8')
    sheet = tmp_path / 'issues.xlsx'
    workbook = Workbook()
    workbook.active.title = 'Issues'
    workbook.active.append(['ID', 'Status'])
    workbook.active.append(['P-001', 'Open'])
    workbook.save(sheet)
    assert documents.parse_document(md) == ('en', [documents.Section('Kickoff', 'Milestones were confirmed.')])
    rows = documents.parse_document(sheet)[1][0]
    assert rows.location == "Sheet 'Issues' rows 2-2" and rows.text == 'ID: P-001; Status: Open'
    chinese = tmp_path / '纪要.md'
    chinese.write_text('会议确认了里程碑。', encoding='utf-8')
    assert documents.parse_document(chinese)[1][0].location == '正文'


def test_prompt_follows_ui_language_and_names_document_languages(client, sample_dir):
    assert client.get('/api/domains', headers={'Accept-Language': 'en-US,en;q=0.9'}).json()[0]['name'] == \
        'Automation Delivery'
    assert client.get('/api/projects/999/documents', headers={'Accept-Language': 'en'}).json()['detail'] == \
        'Project not found'
    assert client.get('/api/projects/999/documents').json()['detail'] == '项目不存在'

    project = client.post('/api/projects', json={'name': 'p'}).json()
    path = sample_dir / '技术协议_V1.2.docx'
    client.post(f"/api/projects/{project['id']}/documents", files=[('files', (path.name, path.read_bytes()))])
    conversation = client.post(f"/api/projects/{project['id']}/conversations").json()
    assert conversation['title'] == ''
    # The UI language picks the prompt's language; the question's language is stated in it as the default answer language.
    for header, expected in (('en', ['[[verdict:<code>]]', 'Chinese (1)', 'written in English: answer in English']),
                             ('zh-CN', ['[[verdict:代码]]', '1 份中文', '提问是用英文写的'])):
        SYSTEM_PROMPTS.clear()
        client.post(f"/api/conversations/{conversation['id']}/chat", json={'message': 'What is the cycle time?'},
                    headers={'Accept-Language': header})
        assert SYSTEM_PROMPTS and all(e in p for p in SYSTEM_PROMPTS for e in expected), header


def test_projects_carry_their_domain(client):
    domains = client.get('/api/domains').json()
    software = next(d for d in domains if d['id'] == 'software_impl')
    assert software['examples'] and [l['name'] for l in software['labels']] == ['范围内', '需求变更', '待澄清']
    assert client.post('/api/projects', json={'name': 'a'}).json()['domain'] == DEFAULT_DOMAIN
    assert client.post('/api/projects', json={'name': 'b', 'domain': 'nope'}).status_code == 400

    project = client.post('/api/projects', json={'name': 'ERP 二期', 'domain': 'software_impl'}).json()
    assert project['domain'] == 'software_impl'
    conversation = client.post(f"/api/projects/{project['id']}/conversations").json()
    SYSTEM_PROMPTS.clear()
    client.post(f"/api/conversations/{conversation['id']}/chat", json={'message': '报表算变更吗'})
    assert SYSTEM_PROMPTS and all('change_request（需求变更）' in p for p in SYSTEM_PROMPTS)


def test_old_database_is_migrated(tmp_path):
    path = tmp_path / 'app.db'
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                     "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("INSERT INTO projects (name) VALUES ('old')")
        conn.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, project_id INTEGER, filename TEXT, "
                     "n_chunks INTEGER, created_at TEXT)")
        conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, document_id INTEGER, project_id INTEGER, "
                     "ordinal INTEGER, location TEXT, text TEXT, embedding BLOB)")
        conn.execute("INSERT INTO documents (project_id, filename, n_chunks) VALUES (1, 'a.md', 1), (1, 'b.md', 1)")
        conn.execute("INSERT INTO chunks (document_id, project_id, ordinal, location, text) VALUES "
                     "(1, 1, 0, 'x', 'The warranty is twelve months.'), (2, 1, 0, 'x', '质保期十二个月。')")
    db = Database(path)
    assert db.one('SELECT domain FROM projects')['domain'] == DEFAULT_DOMAIN
    assert db.document_languages(1) == {'en': 1, 'zh': 1}


@pytest.mark.asyncio
async def test_mcp_server_tools(tmp_path, sample_dir):
    from mcp import Client
    from app.mcp_server import create_server

    settings = Settings(_env_file=None, data_dir=tmp_path, llm_api_key='')
    with TestClient(create_app(settings, llm=ScriptedModel())) as web:  # upload through the web API
        project = web.post('/api/projects', json={'name': 'p'}).json()
        path = sample_dir / '技术协议_V1.2.docx'
        web.post(f"/api/projects/{project['id']}/documents", files=[('files', (path.name, path.read_bytes()))])

    async with Client(create_server(settings, llm=ScriptedModel())) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(tools) == {'list_projects', 'list_documents', 'search_documents', 'ask'}
        assert all(tool.annotations.read_only_hint for tool in tools.values())

        projects = (await client.call_tool('list_projects', {})).structured_content['result']
        assert projects == [{'id': project['id'], 'name': 'p', 'domain': DEFAULT_DOMAIN, 'documents': 1}]
        hits = (await client.call_tool('search_documents', {'project_id': project['id'], 'query': '单件节拍'}))
        assert any('45 s/件' in hit['text'] for hit in hits.structured_content['result'])

        answer = (await client.call_tool('ask', {'project_id': project['id'], 'question': '节拍要求'})).structured_content
        assert answer['citations'] and f"[c{answer['citations'][0]['chunk_id']}]" in answer['answer']

        missing = await client.call_tool('list_documents', {'project_id': 999})
        assert missing.is_error and 'No project with id 999' in missing.content[0].text


def test_runs_are_traced(client, sample_dir, monkeypatch):
    project = client.post('/api/projects', json={'name': 'p'}).json()
    path = sample_dir / '技术协议_V1.2.docx'
    client.post(f"/api/projects/{project['id']}/documents", files=[('files', (path.name, path.read_bytes()))])
    conversation = client.post(f"/api/projects/{project['id']}/conversations").json()
    client.post(f"/api/conversations/{conversation['id']}/chat", json={'message': '节拍要求'})
    traced = client.get(f"/api/projects/{project['id']}/runs").json()
    assert traced['summary']['runs'] == 1 and traced['summary']['errors'] == 0
    run = traced['runs'][0]
    assert (run['source'], run['question'], run['tool_calls'], run['lang']) == ('web', '节拍要求', 1, 'zh')
    assert run['latency_ms'] is not None and run['conversation_id'] == conversation['id']

    from app.tracing import enable_langsmith
    for name in ('LANGSMITH_TRACING', 'LANGSMITH_API_KEY', 'LANGSMITH_PROJECT'):
        monkeypatch.delenv(name, raising=False)
    assert not enable_langsmith(Settings(_env_file=None))
    assert enable_langsmith(Settings(_env_file=None, langsmith_api_key='lsv2-test'))
    import os
    assert os.environ['LANGSMITH_TRACING'] == 'true' and os.environ['LANGSMITH_PROJECT'] == 'delivery-copilot'


class ReadinessModel(ScriptedModel):
    """Answers the readiness graph's structured-output calls: one criterion, then a 'met' check."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        import re
        prompt = messages[-1].content
        first = int(re.search(r'\[c(\d+)\]', prompt).group(1))
        if '列出终验收' in prompt:
            call = {'name': 'Criteria', 'args': {'criteria': [{'text': '单件节拍 ≤ 45 s/件', 'chunk_id': first}]}}
        else:  # 99999 was never shown to the model, so it must be dropped
            call = {'name': 'Check', 'args': {'status': 'met', 'finding': '实测 43.6 s', 'citations': [first, 99999]}}
        return ChatResult(generations=[ChatGeneration(message=AIMessage('', tool_calls=[{**call, 'id': 'c1'}]))])


def test_readiness_check(tmp_path, sample_dir):
    settings = Settings(_env_file=None, data_dir=tmp_path, llm_api_key='')
    with TestClient(create_app(settings, llm=ReadinessModel())) as client:
        project = client.post('/api/projects', json={'name': 'p'}).json()
        files = [('files', (p.name, p.read_bytes())) for p in sorted(sample_dir.iterdir())]
        client.post(f"/api/projects/{project['id']}/documents", files=files)
        result = client.post(f"/api/projects/{project['id']}/readiness").json()
        assert result['counts'] == {'at_risk': 0, 'no_evidence': 0, 'met': 1}
        item = result['items'][0]
        assert item['criterion'] == '单件节拍 ≤ 45 s/件' and item['status'] == 'met'
        assert len(item['citations']) == 1 and str(item['citations'][0]) in result['sources']
        traced = client.get(f"/api/projects/{project['id']}/runs").json()
        assert traced['runs'][0]['source'] == 'readiness' and traced['runs'][0]['tool_calls'] == 2  # model calls
        assert traced['summary']['runs'] == 0 and traced['by_source']['readiness']['runs'] == 1  # not a question


@pytest.mark.asyncio
async def test_structured_output_repairs_bare_passage_ids():
    from types import SimpleNamespace
    from app.readiness import Check, _structured

    class Stub:  # what with_structured_output(include_raw=True) returns when the JSON is invalid
        async def ainvoke(self, prompt):
            bad = {'args': '{"status": "met", "finding": "99.6%", "citations": [c26, c12]}'}
            return {'parsed': None, 'raw': SimpleNamespace(invalid_tool_calls=[bad])}

    assert await _structured(Stub(), Check, 'x') == Check(status='met', finding='99.6%', citations=[26, 12])
