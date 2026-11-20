"""SQLite storage for projects, documents, chunks and conversation metadata.

Conversation messages themselves live in the LangGraph checkpointer.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT 'automation_delivery',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    n_chunks INTEGER NOT NULL DEFAULT 0,
    lang TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    location TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS chunks_project ON chunks(project_id);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    conversation_id TEXT,
    source TEXT NOT NULL,
    domain TEXT,
    lang TEXT,
    question TEXT,
    verdict TEXT,
    tool_calls INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS runs_project ON runs(project_id);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn):
        """Bring databases created by earlier versions up to the current schema."""
        columns = lambda table: {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}  # noqa: E731
        if 'domain' not in columns('projects'):  # before domain packs
            conn.execute("ALTER TABLE projects ADD COLUMN domain TEXT NOT NULL DEFAULT 'automation_delivery'")
        if 'lang' not in columns('documents'):  # before languages: detect from the stored chunks
            from .i18n import detect_language
            conn.execute('ALTER TABLE documents ADD COLUMN lang TEXT')
            for (document_id,) in conn.execute('SELECT id FROM documents').fetchall():
                text = '\n'.join(row[0] for row in conn.execute(
                    'SELECT text FROM chunks WHERE document_id = ? ORDER BY ordinal', (document_id,)))
                conn.execute('UPDATE documents SET lang = ? WHERE id = ?', (detect_language(text[:20000]), document_id))

    def document_languages(self, project_id: int) -> dict[str, int]:
        """How many of the project's documents are in each language, e.g. {'zh': 7, 'en': 2}."""
        rows = self.query('SELECT lang, COUNT(*) AS n FROM documents WHERE project_id = ? AND lang IS NOT NULL '
                          'GROUP BY lang', (project_id,))
        return {row['lang']: row['n'] for row in rows}

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def query(self, sql, params=()):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params)]

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None
