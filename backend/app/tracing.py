"""Run tracing: a local record of every agent run, plus optional LangSmith export.

Local: each question answered through the web UI or MCP adds a row to the `runs` table — latency, tool
calls, tokens, verdict, error — and GET /api/projects/{id}/runs summarises them. Nothing leaves the machine.

LangSmith: set LANGSMITH_API_KEY in backend/.env and LangChain sends full traces (every LLM call and tool
call) there. Runs carry metadata (project, domain, language, source) so they can be filtered.
"""
import os
import time
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage

from .config import Settings
from .db import Database


def enable_langsmith(settings: Settings) -> bool:
    """LangChain reads LangSmith settings from the environment, not from our Settings: copy them over."""
    if not settings.langsmith_api_key:
        return False
    os.environ.setdefault('LANGSMITH_TRACING', 'true')
    os.environ.setdefault('LANGSMITH_API_KEY', settings.langsmith_api_key)
    os.environ.setdefault('LANGSMITH_PROJECT', settings.langsmith_project)
    if settings.langsmith_endpoint:
        os.environ.setdefault('LANGSMITH_ENDPOINT', settings.langsmith_endpoint)
    return True


@dataclass
class Run:
    """Accumulates one run's numbers from the agent's AI messages, then writes them to `runs`."""
    project_id: int
    source: str
    question: str
    domain: str
    lang: str
    conversation_id: str | None = None
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    started: float = field(default_factory=time.monotonic)

    def metadata(self) -> dict:
        """For the LangGraph config: shows up on LangSmith traces."""
        return {'project_id': self.project_id, 'domain': self.domain, 'lang': self.lang, 'source': self.source,
                'conversation_id': self.conversation_id}

    def observe(self, message):
        if isinstance(message, AIMessage):
            self.tool_calls += len(message.tool_calls)
            if usage := message.usage_metadata:
                self.tokens_in += usage.get('input_tokens', 0)
                self.tokens_out += usage.get('output_tokens', 0)

    def save(self, db: Database, verdict: str | None = None, error: str | None = None):
        with db.connect() as conn:
            conn.execute(
                'INSERT INTO runs (project_id, conversation_id, source, domain, lang, question, verdict, tool_calls, '
                'tokens_in, tokens_out, latency_ms, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (self.project_id, self.conversation_id, self.source, self.domain, self.lang, self.question[:500],
                 verdict, self.tool_calls, self.tokens_in, self.tokens_out,
                 round((time.monotonic() - self.started) * 1000), error))


QUESTION_SOURCES = ('web', 'mcp')  # agent answers; other sources (readiness) are reported separately


def summarize_runs(db: Database, project_id: int, limit: int = 50) -> dict:
    """`summary` covers questions answered by the agent (web UI and MCP); `by_source` covers every kind of run
    (for a readiness check, tool_calls counts its model calls)."""
    marks = ', '.join('?' * len(QUESTION_SOURCES))
    totals = db.one(
        'SELECT COUNT(*) AS runs, SUM(error IS NOT NULL) AS errors, ROUND(AVG(latency_ms)) AS avg_latency_ms, '
        'ROUND(AVG(tool_calls), 2) AS avg_tool_calls, ROUND(AVG(tokens_in)) AS avg_tokens_in, '
        'ROUND(AVG(tokens_out)) AS avg_tokens_out, SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out '
        f'FROM runs WHERE project_id = ? AND source IN ({marks})', (project_id, *QUESTION_SOURCES))
    verdicts = {row['verdict']: row['n'] for row in db.query(
        'SELECT verdict, COUNT(*) AS n FROM runs WHERE project_id = ? AND verdict IS NOT NULL GROUP BY verdict',
        (project_id,))}
    by_source = {row['source']: {k: v for k, v in row.items() if k != 'source'} for row in db.query(
        'SELECT source, COUNT(*) AS runs, ROUND(AVG(latency_ms)) AS avg_latency_ms, SUM(tokens_in) AS tokens_in, '
        'SUM(tokens_out) AS tokens_out FROM runs WHERE project_id = ? GROUP BY source', (project_id,))}
    recent = db.query('SELECT * FROM runs WHERE project_id = ? ORDER BY id DESC LIMIT ?', (project_id, limit))
    return {'summary': {**totals, 'errors': totals['errors'] or 0, 'verdicts': verdicts}, 'by_source': by_source,
            'runs': recent}
