"""Hybrid retrieval: BM25 over jieba tokens, optionally fused with embeddings via RRF."""
import logging
import re
from dataclasses import dataclass

import jieba
import numpy as np
from rank_bm25 import BM25Okapi

from .config import Settings
from .db import Database

jieba.setLogLevel(logging.WARNING)

STOPWORDS = set('的 了 是 在 和 与 及 或 等 对 由 为 将 把 被 中 也 都 就 而 并 吗 呢 啊 吧 什么 哪些 怎么 如何 是否 一个 这个 那个 我们 你们 他们'.split())
WORD = re.compile(r'\w', re.UNICODE)
RRF_K = 60


def tokenize(text: str) -> list[str]:
    return [token for token in jieba.lcut_for_search(text.lower())
            if WORD.search(token) and token not in STOPWORDS]


@dataclass
class Hit:
    chunk_id: int
    document_id: int
    filename: str
    location: str
    text: str
    score: float

    def to_dict(self):
        return self.__dict__.copy()


class Embedder:
    def __init__(self, settings: Settings):
        from langchain_openai import OpenAIEmbeddings
        self.client = OpenAIEmbeddings(model=settings.embedding_model,
                                       base_url=settings.embedding_base_url or None,
                                       api_key=settings.embedding_api_key or None,
                                       check_embedding_ctx_length=False)

    def embed(self, texts: list[str]) -> np.ndarray:
        return _normalize(np.asarray(self.client.embed_documents(texts), dtype=np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize(np.asarray([self.client.embed_query(text)], dtype=np.float32))[0]


def _normalize(matrix):
    return matrix / np.maximum(np.linalg.norm(matrix, axis=-1, keepdims=True), 1e-12)


class _ProjectIndex:
    def __init__(self, rows):
        self.rows = rows
        self.bm25 = BM25Okapi([tokenize(f"{row['location']} {row['text']}") or ['∅'] for row in rows])
        vectors = [row['embedding'] for row in rows]
        self.vectors = (np.stack([np.frombuffer(v, dtype=np.float32) for v in vectors])
                        if vectors and all(v is not None for v in vectors) else None)


class Retriever:
    def __init__(self, db: Database, embedder: Embedder | None = None):
        self.db = db
        self.embedder = embedder
        self._indexes: dict[int, _ProjectIndex] = {}

    def invalidate(self, project_id: int):
        self._indexes.pop(project_id, None)

    def _index(self, project_id):
        if project_id not in self._indexes:
            rows = self.db.query(
                'SELECT c.id, c.document_id, c.location, c.text, c.embedding, d.filename '
                'FROM chunks c JOIN documents d ON d.id = c.document_id '
                'WHERE c.project_id = ? ORDER BY c.id', (project_id,))
            self._indexes[project_id] = _ProjectIndex(rows) if rows else None
        return self._indexes[project_id]

    def search(self, project_id: int, query: str, top_k: int = 6) -> list[Hit]:
        index = self._index(project_id)
        tokens = tokenize(query)
        if index is None or not tokens:
            return []
        rankings = []
        bm25 = index.bm25.get_scores(tokens)
        rankings.append([i for i in np.argsort(-bm25) if bm25[i] > 0])
        if self.embedder is not None and index.vectors is not None:
            similarity = index.vectors @ self.embedder.embed_query(query)
            rankings.append(list(np.argsort(-similarity)[:50]))
        fused: dict[int, float] = {}
        for ranking in rankings:
            for rank, i in enumerate(ranking[:50]):
                fused[int(i)] = fused.get(int(i), 0.) + 1. / (RRF_K + rank + 1)
        best = sorted(fused.items(), key=lambda item: -item[1])[:top_k]
        return [Hit(chunk_id=index.rows[i]['id'], document_id=index.rows[i]['document_id'],
                    filename=index.rows[i]['filename'], location=index.rows[i]['location'],
                    text=index.rows[i]['text'], score=round(score, 5))
                for i, score in best]
