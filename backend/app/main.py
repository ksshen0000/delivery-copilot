import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import documents
from .agent import build_graph
from .config import Settings, get_settings
from .db import Database
from .domains import DEFAULT_DOMAIN, load_domains
from .retrieval import Embedder, Retriever

log = logging.getLogger(__name__)
FRONTEND_DIST = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'


class ProjectIn(BaseModel):
    name: str
    domain: str = DEFAULT_DOMAIN


class ChatIn(BaseModel):
    message: str


def default_llm(settings: Settings):
    if not settings.llm_api_key:
        return None
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=settings.llm_model, base_url=settings.llm_base_url,
                      api_key=settings.llm_api_key, temperature=settings.llm_temperature,
                      streaming=True)


def create_app(settings: Settings | None = None, llm=None) -> FastAPI:
    settings = settings or get_settings()
    db = Database(settings.data_dir / 'app.db')
    domains = load_domains()
    embedder = Embedder(settings) if settings.embeddings_enabled else None
    retriever = Retriever(db, embedder)
    uploads = settings.data_dir / 'uploads'

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncSqliteSaver.from_conn_string(str(settings.data_dir / 'checkpoints.db')) as saver:
            model = llm if llm is not None else default_llm(settings)
            app.state.graph = build_graph(model, db, retriever, saver, domains, settings.top_k) if model else None
            yield

    app = FastAPI(title='交付实施助手', lifespan=lifespan)

    def require_project(project_id: int):
        if not db.one('SELECT id FROM projects WHERE id = ?', (project_id,)):
            raise HTTPException(404, '项目不存在')

    def require_conversation(conversation_id: str):
        row = db.one('SELECT * FROM conversations WHERE id = ?', (conversation_id,))
        if not row:
            raise HTTPException(404, '会话不存在')
        return row

    # ---- domains & projects ---------------------------------------------
    @app.get('/api/domains')
    def list_domains():
        return [domain.public() for domain in domains.values()]

    @app.get('/api/projects')
    def list_projects():
        return db.query('SELECT * FROM projects ORDER BY id DESC')

    @app.post('/api/projects')
    def create_project(body: ProjectIn):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, '项目名称不能为空')
        if body.domain not in domains:
            raise HTTPException(400, f'未知领域：{body.domain}')
        with db.connect() as conn:
            project_id = conn.execute('INSERT INTO projects (name, domain) VALUES (?, ?)',
                                      (name, body.domain)).lastrowid
        return db.one('SELECT * FROM projects WHERE id = ?', (project_id,))

    # ---- documents ------------------------------------------------------
    @app.get('/api/projects/{project_id}/documents')
    def list_documents(project_id: int):
        require_project(project_id)
        return db.query('SELECT * FROM documents WHERE project_id = ? ORDER BY id DESC', (project_id,))

    @app.post('/api/projects/{project_id}/documents')
    def upload_documents(project_id: int, files: list[UploadFile]):
        require_project(project_id)
        results = []
        for upload in files:
            filename = Path(upload.filename or 'unnamed').name
            if Path(filename).suffix.lower() not in documents.SUPPORTED:
                results.append({'filename': filename, 'error': '不支持的文件类型'})
                continue
            directory = uploads / str(project_id)
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f'{uuid.uuid4().hex[:8]}_{filename}'
            path.write_bytes(upload.file.read())
            try:
                chunks = documents.chunk(documents.parse(path))
                if not chunks:
                    raise ValueError('没有解析出文本（扫描版 PDF 需要先 OCR）')
                vectors = embedder.embed([f'{c.location}\n{c.text}' for c in chunks]) if embedder else None
            except Exception as error:  # Report per file; keep processing the rest.
                log.exception('Failed to ingest %s', filename)
                path.unlink(missing_ok=True)
                results.append({'filename': filename, 'error': str(error)})
                continue
            with db.connect() as conn:
                document_id = conn.execute(
                    'INSERT INTO documents (project_id, filename, n_chunks) VALUES (?, ?, ?)',
                    (project_id, filename, len(chunks))).lastrowid
                conn.executemany(
                    'INSERT INTO chunks (document_id, project_id, ordinal, location, text, embedding) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    [(document_id, project_id, i, c.location, c.text,
                      vectors[i].tobytes() if vectors is not None else None)
                     for i, c in enumerate(chunks)])
            results.append({'filename': filename, 'id': document_id, 'n_chunks': len(chunks)})
        retriever.invalidate(project_id)
        return results

    @app.delete('/api/documents/{document_id}')
    def delete_document(document_id: int):
        row = db.one('SELECT project_id FROM documents WHERE id = ?', (document_id,))
        if not row:
            raise HTTPException(404, '文档不存在')
        with db.connect() as conn:
            conn.execute('DELETE FROM documents WHERE id = ?', (document_id,))
        retriever.invalidate(row['project_id'])
        return {'ok': True}

    @app.get('/api/projects/{project_id}/search')
    def search(project_id: int, q: str):
        require_project(project_id)
        return [hit.to_dict() for hit in retriever.search(project_id, q, settings.top_k)]

    # ---- conversations --------------------------------------------------
    @app.get('/api/projects/{project_id}/conversations')
    def list_conversations(project_id: int):
        require_project(project_id)
        return db.query('SELECT * FROM conversations WHERE project_id = ? ORDER BY created_at DESC, rowid DESC',
                        (project_id,))

    @app.post('/api/projects/{project_id}/conversations')
    def create_conversation(project_id: int):
        require_project(project_id)
        conversation_id = uuid.uuid4().hex
        with db.connect() as conn:
            conn.execute('INSERT INTO conversations (id, project_id, title) VALUES (?, ?, ?)',
                         (conversation_id, project_id, '新会话'))
        return db.one('SELECT * FROM conversations WHERE id = ?', (conversation_id,))

    @app.get('/api/conversations/{conversation_id}/messages')
    async def get_messages(conversation_id: str):
        require_conversation(conversation_id)
        graph = app.state.graph
        if graph is None:
            return []
        state = await graph.aget_state({'configurable': {'thread_id': conversation_id}})
        return to_ui_messages(state.values.get('messages', []))

    @app.post('/api/conversations/{conversation_id}/chat')
    async def chat(conversation_id: str, body: ChatIn):
        conversation = require_conversation(conversation_id)
        project = db.one('SELECT domain FROM projects WHERE id = ?', (conversation['project_id'],))
        domain = project['domain'] if project['domain'] in domains else DEFAULT_DOMAIN  # pack removed since
        graph = app.state.graph
        message = body.message.strip()
        if not message:
            raise HTTPException(400, '消息不能为空')
        if conversation['title'] == '新会话':
            with db.connect() as conn:
                conn.execute('UPDATE conversations SET title = ? WHERE id = ?', (message[:30], conversation_id))

        async def events():
            if graph is None:
                yield sse('error', {'message': '未配置大模型：在 backend/.env 中设置 LLM_API_KEY 后重启'})
                return
            config = {'configurable': {'thread_id': conversation_id, 'project_id': conversation['project_id'],
                                       'domain': domain},
                      'recursion_limit': 16}
            try:
                async for mode, payload in graph.astream({'messages': [HumanMessage(message)]}, config,
                                                         stream_mode=['messages', 'updates']):
                    if mode == 'messages':
                        chunk, metadata = payload
                        if (metadata.get('langgraph_node') == 'agent'
                                and isinstance(chunk, (AIMessageChunk, AIMessage))
                                and isinstance(chunk.content, str) and chunk.content):
                            yield sse('text', {'text': chunk.content})
                    else:
                        for part in update_parts(payload):
                            yield sse(part.pop('type'), part)
                yield sse('done', {})
            except Exception as error:
                log.exception('Chat failed')
                yield sse('error', {'message': f'{type(error).__name__}: {error}'})

        return EventSourceResponse(events())

    if FRONTEND_DIST.exists():
        app.mount('/assets', StaticFiles(directory=FRONTEND_DIST / 'assets'), name='assets')

        @app.get('/{path:path}', include_in_schema=False)
        def spa(path: str):
            if path.startswith('api/'):
                raise HTTPException(404)
            return FileResponse(FRONTEND_DIST / 'index.html')

    return app


def sse(event, data):
    return {'event': event, 'data': json.dumps(data, ensure_ascii=False)}


def tool_call_part(call):
    return {'type': 'tool_call', 'id': call['id'], 'name': call['name'], 'args': call['args']}


def tool_result_part(message: ToolMessage):
    sources = message.artifact if isinstance(message.artifact, list) else []
    return {'type': 'tool_result', 'id': message.tool_call_id, 'name': message.name,
            'sources': sources, 'content': None if sources else str(message.content)}


def update_parts(update):
    """Turn one graph `updates` event into UI parts (tool calls and results; text streams separately)."""
    parts = []
    for node, value in (update or {}).items():
        for message in (value or {}).get('messages', []):
            if node == 'agent' and isinstance(message, AIMessage):
                parts.extend(tool_call_part(call) for call in message.tool_calls)
            elif node == 'tools' and isinstance(message, ToolMessage):
                parts.append(tool_result_part(message))
    return parts


def to_ui_messages(messages):
    """Group checkpointed messages into user turns and assistant turns of ordered parts."""
    result = []
    for message in messages:
        if isinstance(message, HumanMessage):
            result.append({'role': 'user', 'parts': [{'type': 'text', 'text': message.content}]})
            continue
        if not result or result[-1]['role'] != 'assistant':
            result.append({'role': 'assistant', 'parts': []})
        parts = result[-1]['parts']
        if isinstance(message, AIMessage):
            if isinstance(message.content, str) and message.content:
                parts.append({'type': 'text', 'text': message.content})
            parts.extend(tool_call_part(call) for call in message.tool_calls)
        elif isinstance(message, ToolMessage):
            parts.append(tool_result_part(message))
    return result

