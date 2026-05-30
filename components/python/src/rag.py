"""
Lightweight RAG utilities.

The project uses embeddings plus a SQLite-backed vector-like store:
- knowledge chunks are persisted in SQLite
- embeddings are stored as JSON arrays
- semantic retrieval uses cosine similarity over the stored vectors
"""

from __future__ import annotations

import hashlib
import math
import os
import logging
from dataclasses import dataclass
from typing import Iterable

from langchain_openai import OpenAIEmbeddings

from ai_errors import classify_ai_error
import call_center_db

logger = logging.getLogger(__name__)


class HashFallbackEmbeddings:
    """Deterministic low-cost fallback when external embeddings are unavailable."""

    def __init__(self, dimensions: int = 128):
        self.dimensions = dimensions
        self.model_name = "local-hash-embeddings"

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in text.lower().split():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            idx = int.from_bytes(digest[:2], "big") % self.dimensions
            sign = 1.0 if digest[2] % 2 == 0 else -1.0
            weight = 1.0 + (digest[3] / 255.0)
            vector[idx] += sign * weight
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def _cosine_similarity(a: Iterable[float], b: Iterable[float]) -> float:
    vec_a = list(a)
    vec_b = list(b)
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0
    numerator = sum(x * y for x, y in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(x * x for x in vec_a))
    norm_b = math.sqrt(sum(y * y for y in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return numerator / (norm_a * norm_b)


@dataclass
class RetrievalHit:
    id: str
    title: str
    source: str
    content: str
    score: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "source": self.source,
            "content": self.content,
            "score": round(self.score, 4),
        }


class SemanticRetriever:
    def __init__(self) -> None:
        embed_model = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
        timeout_s = float(os.getenv("OPENAI_EMBED_TIMEOUT_SECONDS", "8"))
        if os.getenv("OPENAI_API_KEY"):
            self.backend = OpenAIEmbeddings(
                model=embed_model,
                request_timeout=timeout_s,
                max_retries=1,
            )
            self.model_name = embed_model
        else:
            self.backend = HashFallbackEmbeddings()
            self.model_name = self.backend.model_name
        self.last_error_kind: str | None = None

    def _fallback_to_local_embeddings(self, reason: Exception) -> None:
        kind = classify_ai_error(reason)
        self.last_error_kind = kind
        logger.warning(
            "SemanticRetriever: switching to local hash embeddings due to %s: %s",
            kind,
            reason,
        )
        self.backend = HashFallbackEmbeddings()
        self.model_name = self.backend.model_name

    def ensure_index(self, conn) -> None:
        pending = call_center_db.list_unembedded_chunks(conn)
        if not pending:
            return
        try:
            vectors = self.backend.embed_documents([chunk["content"] for chunk in pending])
        except Exception as exc:
            if isinstance(self.backend, HashFallbackEmbeddings):
                raise
            self._fallback_to_local_embeddings(exc)
            vectors = self.backend.embed_documents([chunk["content"] for chunk in pending])
        for chunk, vector in zip(pending, vectors):
            call_center_db.save_chunk_embedding(
                conn,
                chunk_id=chunk["id"],
                vector=vector,
                embedding_model=self.model_name,
            )

    def search(self, conn, query: str, *, k: int = 4) -> list[dict]:
        query = query.strip()
        if not query:
            return []
        self.ensure_index(conn)
        chunks = call_center_db.list_embedded_chunks(conn)
        if not chunks:
            return []
        try:
            query_vector = self.backend.embed_query(query)
        except Exception as exc:
            if isinstance(self.backend, HashFallbackEmbeddings):
                raise
            self._fallback_to_local_embeddings(exc)
            query_vector = self.backend.embed_query(query)
        scored: list[RetrievalHit] = []
        for chunk in chunks:
            score = _cosine_similarity(query_vector, chunk["embedding"])
            scored.append(
                RetrievalHit(
                    id=chunk["id"],
                    title=chunk["title"],
                    source=chunk["source"],
                    content=chunk["content"],
                    score=score,
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        return [item.to_dict() for item in scored[:k]]
