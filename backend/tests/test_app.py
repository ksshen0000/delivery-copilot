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

from app import documents
from app.config import Settings
from app.db import Database
from app.domains import DEFAULT_DOMAIN, load_domains
from app.main import create_app

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
    domains = load_domains()
    assert {'automation_delivery', 'software_impl'} <= set(domains)
    automation, software = domains['automation_delivery'], domains['software_impl']
    prompt = automation.system_prompt('2026-01-01')
    assert '“结论：范围内”“结论：范围外”或“结论：存在歧义”（三选一）' in prompt
    assert '本领域的判断规则' not in prompt  # no extra rules: same prompt the eval baseline used
    assert '需求变更' in software.system_prompt('2026-01-01') and '本领域的判断规则' in software.system_prompt('x')
    assert software.conclusion.search('**结论：** 需求变更').group(1) == '需求变更'
    assert automation.conclusion.search('结论：范围外') and not automation.conclusion.search('结论：需求变更')
    for domain in domains.values():  # every eval set only uses its own pack's labels
        if domain.eval_cases:
            labels = {case['label'] for case in yaml.safe_load(domain.eval_cases.read_text(encoding='utf-8'))}
            assert labels <= set(domain.labels), domain.id
            assert domain.eval_documents.is_dir()


def test_projects_carry_their_domain(client):
    domains = client.get('/api/domains').json()
    software = next(d for d in domains if d['id'] == 'software_impl')
    assert software['examples'] and list(software['labels']) == ['范围内', '需求变更', '待澄清']
    assert client.post('/api/projects', json={'name': 'a'}).json()['domain'] == DEFAULT_DOMAIN
    assert client.post('/api/projects', json={'name': 'b', 'domain': 'nope'}).status_code == 400

    project = client.post('/api/projects', json={'name': 'ERP 二期', 'domain': 'software_impl'}).json()
    assert project['domain'] == 'software_impl'
    conversation = client.post(f"/api/projects/{project['id']}/conversations").json()
    SYSTEM_PROMPTS.clear()
    client.post(f"/api/conversations/{conversation['id']}/chat", json={'message': '报表算变更吗'})
    assert SYSTEM_PROMPTS and all('结论：需求变更' in p for p in SYSTEM_PROMPTS)


def test_database_without_domain_column_is_migrated(tmp_path):
    path = tmp_path / 'app.db'
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                     "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("INSERT INTO projects (name) VALUES ('old')")
    assert Database(path).one('SELECT domain FROM projects')['domain'] == DEFAULT_DOMAIN
