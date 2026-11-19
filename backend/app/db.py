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
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(projects)')}
            if 'domain' not in columns:  # databases created before domain packs
                conn.execute("ALTER TABLE projects ADD COLUMN domain TEXT NOT NULL DEFAULT 'automation_delivery'")

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
