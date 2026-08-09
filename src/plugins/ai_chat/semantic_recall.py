from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Protocol, Sequence

import httpx


@dataclass(frozen=True)
class SemanticDocument:
    scope_key: str
    source_type: str
    source_handle: str
    content: str
    metadata: dict[str, object]

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SemanticHit:
    scope_key: str
    source_type: str
    source_handle: str
    content: str
    score: float
    metadata: dict[str, object]


class VectorBackend(Protocol):
    dimensions: int

    def upsert(
        self,
        document: SemanticDocument,
        vector: Sequence[float],
        *,
        model: str,
    ) -> None: ...

    def search(
        self,
        scope_keys: Sequence[str],
        query: str,
        vector: Sequence[float],
        *,
        model: str,
        limit: int,
    ) -> list[SemanticHit]: ...

    def stats(self) -> dict[str, object]: ...


class EmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int,
        timeout_seconds: int = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.dimensions = max(int(dimensions), 1)
        self.timeout_seconds = max(int(timeout_seconds), 5)
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        cleaned = [" ".join(str(text).split())[:12000] for text in texts]
        if not cleaned or any(not text for text in cleaned):
            raise ValueError("embedding input must not be empty")
        if not self.configured:
            raise RuntimeError("embedding provider is not configured")
        payload: dict[str, object] = {
            "model": self.model,
            "input": cleaned,
        }
        if self.dimensions > 0:
            payload["dimensions"] = self.dimensions
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self._transport,
        ) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        raw_data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(raw_data, list):
            raise RuntimeError("embedding response has no data array")
        ordered = sorted(
            (item for item in raw_data if isinstance(item, dict)),
            key=lambda item: int(item.get("index") or 0),
        )
        vectors: list[list[float]] = []
        for item in ordered:
            raw_vector = item.get("embedding")
            if not isinstance(raw_vector, list):
                raise RuntimeError("embedding response contains an invalid vector")
            vector = [float(value) for value in raw_vector]
            _validate_vector(vector, self.dimensions)
            vectors.append(vector)
        if len(vectors) != len(cleaned):
            raise RuntimeError("embedding response count does not match input")
        return vectors


class PgVectorBackend:
    """Small psycopg-backed vector index, loaded only when configured."""

    def __init__(self, dsn: str, *, dimensions: int) -> None:
        self.dsn = dsn.strip()
        self.dimensions = max(int(dimensions), 1)
        self._lock = threading.RLock()
        self._initialized = False

    def upsert(
        self,
        document: SemanticDocument,
        vector: Sequence[float],
        *,
        model: str,
    ) -> None:
        _validate_vector(vector, self.dimensions)
        self._ensure_schema()
        encoded_vector = _vector_literal(vector)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO semantic_documents (
                        scope_key, source_type, source_handle,
                        content, content_hash, metadata_json,
                        embedding_model, embedding, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb,
                              %s, %s::vector, %s, %s)
                    ON CONFLICT(scope_key, source_type, source_handle)
                    DO UPDATE SET
                        content = EXCLUDED.content,
                        content_hash = EXCLUDED.content_hash,
                        metadata_json = EXCLUDED.metadata_json,
                        embedding_model = EXCLUDED.embedding_model,
                        embedding = EXCLUDED.embedding,
                        updated_at = EXCLUDED.updated_at
                    WHERE semantic_documents.content_hash
                          IS DISTINCT FROM EXCLUDED.content_hash
                       OR semantic_documents.embedding_model
                          IS DISTINCT FROM EXCLUDED.embedding_model
                    """,
                    (
                        document.scope_key,
                        document.source_type,
                        document.source_handle,
                        document.content,
                        document.content_hash,
                        json.dumps(document.metadata, ensure_ascii=False),
                        model,
                        encoded_vector,
                        int(time.time()),
                        int(time.time()),
                    ),
                )
            connection.commit()

    def search(
        self,
        scope_keys: Sequence[str],
        query: str,
        vector: Sequence[float],
        *,
        model: str,
        limit: int,
    ) -> list[SemanticHit]:
        scopes = [str(scope) for scope in scope_keys if str(scope)]
        if not scopes:
            return []
        _validate_vector(vector, self.dimensions)
        self._ensure_schema()
        bounded = min(max(int(limit), 1), 50)
        encoded_vector = _vector_literal(vector)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT scope_key, source_type, source_handle,
                           content, metadata_json,
                           LEAST(
                             1.0,
                             GREATEST(0.0, 1.0 - (embedding <=> %s::vector))
                             + CASE WHEN content ILIKE %s THEN 0.12 ELSE 0 END
                           ) AS score
                    FROM semantic_documents
                    WHERE scope_key = ANY(%s)
                      AND embedding_model = %s
                    ORDER BY score DESC, updated_at DESC
                    LIMIT %s
                    """,
                    (
                        encoded_vector,
                        f"%{query}%",
                        scopes,
                        model,
                        bounded,
                    ),
                )
                rows = cursor.fetchall()
        hits: list[SemanticHit] = []
        for row in rows:
            metadata = row[4]
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}
            hits.append(
                SemanticHit(
                    scope_key=str(row[0]),
                    source_type=str(row[1]),
                    source_handle=str(row[2]),
                    content=str(row[3]),
                    metadata=metadata if isinstance(metadata, dict) else {},
                    score=float(row[5]),
                )
            )
        return hits

    def stats(self) -> dict[str, object]:
        self._ensure_schema()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*), COUNT(DISTINCT scope_key),
                           MAX(updated_at) FROM semantic_documents
                    """
                )
                row = cursor.fetchone()
        return {
            "configured": True,
            "documents": int(row[0] if row else 0),
            "scopes": int(row[1] if row else 0),
            "last_updated_at": int(row[2]) if row and row[2] is not None else None,
            "dimensions": self.dimensions,
        }

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
                    cursor.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS semantic_documents (
                            document_id BIGSERIAL PRIMARY KEY,
                            scope_key TEXT NOT NULL,
                            source_type TEXT NOT NULL,
                            source_handle TEXT NOT NULL,
                            content TEXT NOT NULL,
                            content_hash TEXT NOT NULL,
                            metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            embedding_model TEXT NOT NULL,
                            embedding vector({self.dimensions}) NOT NULL,
                            created_at BIGINT NOT NULL,
                            updated_at BIGINT NOT NULL,
                            UNIQUE(scope_key, source_type, source_handle)
                        )
                        """
                    )
                    cursor.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_semantic_scope_type
                        ON semantic_documents(scope_key, source_type, updated_at DESC)
                        """
                    )
                    cursor.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_semantic_embedding_hnsw
                        ON semantic_documents
                        USING hnsw (embedding vector_cosine_ops)
                        """
                    )
                connection.commit()
            self._initialized = True

    def _connect(self) -> Any:
        if not self.dsn:
            raise RuntimeError("AI_POSTGRES_DSN is not configured")
        try:
            psycopg = importlib.import_module("psycopg")
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL semantic recall requires psycopg; "
                "run pip install -r requirements.txt"
            ) from exc
        return psycopg.connect(self.dsn, connect_timeout=10)


class SemanticRecallService:
    def __init__(
        self,
        embedder: EmbeddingClient,
        backend: VectorBackend,
    ) -> None:
        if embedder.dimensions != backend.dimensions:
            raise ValueError("embedding and pgvector dimensions must match")
        self.embedder = embedder
        self.backend = backend

    async def index(self, documents: Sequence[SemanticDocument]) -> int:
        unique: dict[tuple[str, str, str], SemanticDocument] = {}
        for document in documents:
            content = " ".join(document.content.split()).strip()
            if not content:
                continue
            unique[
                (
                    document.scope_key,
                    document.source_type,
                    document.source_handle,
                )
            ] = SemanticDocument(
                scope_key=document.scope_key,
                source_type=document.source_type,
                source_handle=document.source_handle,
                content=content[:12000],
                metadata=dict(document.metadata),
            )
        items = list(unique.values())
        if not items:
            return 0
        vectors = await self.embedder.embed([item.content for item in items])
        for item, vector in zip(items, vectors):
            await asyncio.to_thread(
                self.backend.upsert,
                item,
                vector,
                model=self.embedder.model,
            )
        return len(items)

    async def search(
        self,
        scope_keys: Sequence[str],
        query: str,
        *,
        limit: int = 10,
    ) -> list[SemanticHit]:
        cleaned = " ".join(query.split()).strip()
        if not cleaned:
            return []
        vector = (await self.embedder.embed([cleaned]))[0]
        return await asyncio.to_thread(
            self.backend.search,
            list(scope_keys),
            cleaned,
            vector,
            model=self.embedder.model,
            limit=limit,
        )

    async def stats(self) -> dict[str, object]:
        return await asyncio.to_thread(self.backend.stats)


class SemanticIndexState:
    """Local checkpoint for derived vectors; source data remains authoritative."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path) if str(path) != ":memory:" else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(path), timeout=10.0, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        with self._transaction() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS semantic_index_state (
                    source_key TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    indexed_at INTEGER NOT NULL
                )
                """
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def changed(
        self,
        documents: Sequence[SemanticDocument],
    ) -> list[SemanticDocument]:
        if not documents:
            return []
        keys = [_document_key(document) for document in documents]
        placeholders = ",".join("?" for _ in keys)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT source_key, content_hash FROM semantic_index_state
                WHERE source_key IN ({placeholders})
                """,
                keys,
            ).fetchall()
        known = {str(row["source_key"]): str(row["content_hash"]) for row in rows}
        return [
            document
            for document in documents
            if known.get(_document_key(document)) != document.content_hash
        ]

    def mark(self, documents: Sequence[SemanticDocument]) -> None:
        now = int(time.time())
        with self._transaction() as cursor:
            for document in documents:
                cursor.execute(
                    """
                    INSERT INTO semantic_index_state (
                        source_key, content_hash, indexed_at
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(source_key) DO UPDATE SET
                        content_hash = excluded.content_hash,
                        indexed_at = excluded.indexed_at
                    """,
                    (_document_key(document), document.content_hash, now),
                )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                yield cursor
            except Exception:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()
            finally:
                cursor.close()


def _validate_vector(vector: Sequence[float], dimensions: int) -> None:
    if len(vector) != int(dimensions):
        raise ValueError(
            f"embedding dimension mismatch: expected {dimensions}, got {len(vector)}"
        )
    if any(not math.isfinite(float(value)) for value in vector):
        raise ValueError("embedding contains a non-finite value")


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(format(float(value), ".10g") for value in vector) + "]"


def _document_key(document: SemanticDocument) -> str:
    return "\0".join(
        (document.scope_key, document.source_type, document.source_handle)
    )
