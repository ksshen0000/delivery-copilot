import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
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
from .i18n import parse_accept_language, t
from .retrieval import Embedder, Retriever
from .readiness import build_readiness_graph, run_readiness
from .tracing import Run, enable_langsmith, summarize_runs

log = logging.getLogger(__name__)
FRONTEND_DIST = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'


class ProjectIn(BaseModel):
    name: str
    domain: str = DEFAULT_DOMAIN


class ChatIn(BaseModel):
    message: str


def ui_language(accept_language: str | None = Header(None)) -> str:
    """Language for API messages, from the Accept-Language header the frontend sends."""
    return parse_accept_language(accept_language)


Lang = Depends(ui_language)


def default_llm(settings: Settings):
    if not settings.llm_api_key:
        return None
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=settings.llm_model, base_url=settings.llm_base_url,
                      api_key=settings.llm_api_key, temperature=settings.llm_temperature,
                      streaming=True, stream_usage=True)  # token usage on streamed answers, for tracing


def create_app(settings: Settings | None = None, llm=None) -> FastAPI:
    settings = settings or get_settings()
    db = Database(settings.data_dir / 'app.db')
    domains = load_domains()
    if enable_langsmith(settings):
        log.info('LangSmith tracing on, project %s', settings.langsmith_project)
    embedder = Embedder(settings) if settings.embeddings_enabled else None
    retriever = Retriever(db, embedder)
    uploads = settings.data_dir / 'uploads'

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncSqliteSaver.from_conn_string(str(settings.data_dir / 'checkpoints.db')) as saver:
            model = llm if llm is not None else default_llm(settings)
            app.state.graph = build_graph(model, db, retriever, saver, domains, settings.top_k) if model else None
            app.state.readiness = build_readiness_graph(model, db, retriever, settings.top_k) if model else None
            yield

    app = FastAPI(title='交付实施助手', lifespan=lifespan)

    def require_project(project_id: int, lang: str):
        if not db.one('SELECT id FROM projects WHERE id = ?', (project_id,)):
            raise HTTPException(404, t('project_not_found', lang))

    def require_conversation(conversation_id: str, lang: str):
        row = db.one('SELECT * FROM conversations WHERE id = ?', (conversation_id,))
        if not row:
            raise HTTPException(404, t('conversation_not_found', lang))
        return row

    # ---- domains & projects ---------------------------------------------
    @app.get('/api/domains')
    def list_domains(lang: str = Lang):
        return [pack.get(lang).public() for pack in domains.values()]

    @app.get('/api/projects')
    def list_projects():
        return db.query('SELECT * FROM projects ORDER BY id DESC')

    @app.post('/api/projects')
    def create_project(body: ProjectIn, lang: str = Lang):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, t('project_name_required', lang))
        if body.domain not in domains:
            raise HTTPException(400, t('unknown_domain', lang, domain=body.domain))
        with db.connect() as conn:
            project_id = conn.execute('INSERT INTO projects (name, domain) VALUES (?, ?)',
                                      (name, body.domain)).lastrowid
        return db.one('SELECT * FROM projects WHERE id = ?', (project_id,))

    # ---- documents ------------------------------------------------------
    @app.get('/api/projects/{project_id}/documents')
    def list_documents(project_id: int, lang: str = Lang):
        require_project(project_id, lang)
        return db.query('SELECT * FROM documents WHERE project_id = ? ORDER BY id DESC', (project_id,))

    @app.post('/api/projects/{project_id}/documents')
    def upload_documents(project_id: int, files: list[UploadFile], lang: str = Lang):
        require_project(project_id, lang)
        results = []
        for upload in files:
            filename = Path(upload.filename or 'unnamed').name
            if Path(filename).suffix.lower() not in documents.SUPPORTED:
                results.append({'filename': filename, 'error': t('unsupported_type', lang)})
                continue
            directory = uploads / str(project_id)
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f'{uuid.uuid4().hex[:8]}_{filename}'
            path.write_bytes(upload.file.read())
            try:
                lang, sections = documents.parse_document(path)
                chunks = documents.chunk(sections)
                if not chunks:
                    raise ValueError(t('no_text', lang))
                vectors = embedder.embed([f'{c.location}\n{c.text}' for c in chunks]) if embedder else None
            except Exception as error:  # Report per file; keep processing the rest.
                log.exception('Failed to ingest %s', filename)
                path.unlink(missing_ok=True)
                results.append({'filename': filename, 'error': str(error)})
                continue
            with db.connect() as conn:
                document_id = conn.execute(
                    'INSERT INTO documents (project_id, filename, n_chunks, lang) VALUES (?, ?, ?, ?)',
                    (project_id, filename, len(chunks), lang)).lastrowid
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
    def delete_document(document_id: int, lang: str = Lang):
        row = db.one('SELECT project_id FROM documents WHERE id = ?', (document_id,))
        if not row:
            raise HTTPException(404, t('document_not_found', lang))
        with db.connect() as conn:
            conn.execute('DELETE FROM documents WHERE id = ?', (document_id,))
        retriever.invalidate(row['project_id'])
        return {'ok': True}

    @app.get('/api/projects/{project_id}/runs')
    def list_runs(project_id: int, limit: int = 50, lang: str = Lang):
        """Recent agent runs and their totals: latency, tool calls, tokens, verdicts, errors."""
        require_project(project_id, lang)
        return summarize_runs(db, project_id, max(1, min(limit, 500)))

    @app.post('/api/projects/{project_id}/readiness')
    async def readiness(project_id: int, lang: str = Lang):
        """Acceptance readiness: every acceptance criterion with its status (met / at_risk / no_evidence),
        a one-line finding and the passages cited, plus those passages. Takes about as long as one answer."""
        require_project(project_id, lang)
        if app.state.readiness is None:
            raise HTTPException(503, t('llm_not_configured', lang))
        run = Run(project_id, 'readiness', 'acceptance readiness', '', lang)
        try:
            result = await run_readiness(app.state.readiness, project_id, lang)
        except Exception as error:
            log.exception('Readiness check failed')
            run.save(db, error=f'{type(error).__name__}: {error}')
            raise HTTPException(502, f'{type(error).__name__}: {error}') from error
        run.tool_calls, run.tokens_in, run.tokens_out = (result['model_calls'], result['tokens']['input_tokens'],
                                                         result['tokens']['output_tokens'])
        run.save(db)
        return result

    @app.get('/api/projects/{project_id}/search')
    def search(project_id: int, q: str, lang: str = Lang):
        require_project(project_id, lang)
        return [hit.to_dict() for hit in retriever.search(project_id, q, settings.top_k)]

    # ---- conversations --------------------------------------------------
    @app.get('/api/projects/{project_id}/conversations')
    def list_conversations(project_id: int, lang: str = Lang):
        require_project(project_id, lang)
        return db.query('SELECT * FROM conversations WHERE project_id = ? ORDER BY created_at DESC, rowid DESC',
                        (project_id,))

    @app.post('/api/projects/{project_id}/conversations')
    def create_conversation(project_id: int, lang: str = Lang):
        require_project(project_id, lang)
        conversation_id = uuid.uuid4().hex
        with db.connect() as conn:
            conn.execute('INSERT INTO conversations (id, project_id, title) VALUES (?, ?, ?)',
                         (conversation_id, project_id, ''))
        return db.one('SELECT * FROM conversations WHERE id = ?', (conversation_id,))

    @app.get('/api/conversations/{conversation_id}/messages')
    async def get_messages(conversation_id: str, lang: str = Lang):
        require_conversation(conversation_id, lang)
        graph = app.state.graph
        if graph is None:
            return []
        state = await graph.aget_state({'configurable': {'thread_id': conversation_id}})
        return to_ui_messages(state.values.get('messages', []))

    @app.post('/api/conversations/{conversation_id}/chat')
    async def chat(conversation_id: str, body: ChatIn, lang: str = Lang):
        conversation = require_conversation(conversation_id, lang)
        project = db.one('SELECT domain FROM projects WHERE id = ?', (conversation['project_id'],))
        domain = project['domain'] if project['domain'] in domains else DEFAULT_DOMAIN  # pack removed since
        graph = app.state.graph
        message = body.message.strip()
        if not message:
            raise HTTPException(400, t('empty_message', lang))
        if conversation['title'] in ('', '新会话'):  # '新会话' was the placeholder before i18n
            with db.connect() as conn:
                conn.execute('UPDATE conversations SET title = ? WHERE id = ?', (message[:30], conversation_id))

        async def events():
            if graph is None:
                yield sse('error', {'message': t('llm_not_configured', lang)})
                return
            run = Run(conversation['project_id'], 'web', message, domain, lang, conversation_id)
            config = {'configurable': {'thread_id': conversation_id, 'project_id': conversation['project_id'],
                                       'domain': domain, 'lang': lang},
                      'recursion_limit': 16, 'metadata': run.metadata(), 'run_name': 'chat'}
            answer = []
            try:
                async for mode, payload in graph.astream({'messages': [HumanMessage(message)]}, config,
                                                         stream_mode=['messages', 'updates']):
                    if mode == 'messages':
                        chunk, metadata = payload
                        if (metadata.get('langgraph_node') == 'agent'
                                and isinstance(chunk, (AIMessageChunk, AIMessage))
                                and isinstance(chunk.content, str) and chunk.content):
                            answer.append(chunk.content)
                            yield sse('text', {'text': chunk.content})
                    else:
                        for ai_message in (payload or {}).get('agent', {}).get('messages', []):
                            run.observe(ai_message)
                        for part in update_parts(payload):
                            yield sse(part.pop('type'), part)
                run.save(db, verdict=domains[domain].get(lang).parse_verdict(''.join(answer)))
                yield sse('done', {})
            except Exception as error:
                log.exception('Chat failed')
                run.save(db, error=f'{type(error).__name__}: {error}')
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

