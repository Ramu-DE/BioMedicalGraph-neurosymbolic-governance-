"""Qdrant vector layer (component 02).

Two properties this module exists to guarantee:

1. Retrieval is **never** a source of facts. This module is not importable from
   `biomedical.governance`, and a test asserts it.
2. Retrieval is filtered by **resolved graph identity**, not by text similarity
   alone. When nothing resolves, the result says so rather than silently
   degrading to a text match.

Without an embedding provider configured, a deterministic hash-based stand-in is
used. It has no semantic meaning — it exists so every structural property above
is testable without credentials. The report says so explicitly.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    PointStruct,
    VectorParams,
)


class RetrievalMode(Enum):
    DENSE = "dense"
    SPARSE = "sparse"
    FUSED = "fused"


def standin_embed(text: str, dim: int = 256) -> list[float]:
    """Deterministic, credential-free pseudo-embedding.

    Hash-derived and L2-normalised. Carries no semantic signal: it is for
    testing structure, never for measuring retrieval quality.
    """
    out: list[float] = []
    counter = 0
    while len(out) < dim:
        digest = hashlib.sha256(f"{text}|{counter}".encode()).digest()
        out.extend((b - 127.5) / 127.5 for b in digest)
        counter += 1
    vec = out[:dim]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    source: str
    source_version: str
    source_document_id: str
    retrieved_at: datetime
    entity_ids: tuple[str, ...] = ()
    entity_links: tuple[dict, ...] = ()
    tier: str = "ingested"

    def payload(self, chunking_version: str, embedding_model: str) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "source": self.source,
            "source_version": self.source_version,
            "source_document_id": self.source_document_id,
            "retrieved_at": self.retrieved_at.isoformat(),
            "tier": self.tier,
            "chunking_version": chunking_version,
            "embedding_model": embedding_model,
            "entity_ids": list(self.entity_ids),
            "entity_links": [dict(link) for link in self.entity_links],
        }


@dataclass(frozen=True)
class ScoredChunk:
    chunk_id: str
    text: str
    score: float
    entity_ids: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class RetrievalResult:
    chunks: tuple[ScoredChunk, ...]
    filters_applied: tuple[str, ...]
    identity_grounded: bool
    mode: RetrievalMode
    degraded: bool = False
    note: str = ""

    def describe(self) -> str:
        state = "DEGRADED" if self.degraded else (
            "identity-grounded" if self.identity_grounded else "UNFILTERED (identity unresolved)"
        )
        return (
            f"{len(self.chunks)} chunk(s) · {self.mode.value} · {state}"
            + (f" · filters={list(self.filters_applied)}" if self.filters_applied else "")
        )


@dataclass
class VectorStore:
    """Collection name encodes (embedding_model, chunking_version) so a stale
    collection is visible by inspection and mixing is impossible."""

    url: str
    api_key: str
    embedding_model: str = "stand-in-hash-v1"
    chunking_version: str = "chunk-v1"
    dim: int = 256
    namespace: str = "biomedv2"
    timeout: int = 30
    _client: QdrantClient | None = field(default=None, repr=False)

    @property
    def collection(self) -> str:
        model = self.embedding_model.replace(":", "-").replace(".", "-").replace("/", "-")
        return f"{self.namespace}__{model}__{self.chunking_version}"

    def __enter__(self) -> VectorStore:
        self._client = QdrantClient(url=self.url, api_key=self.api_key, timeout=self.timeout)
        return self

    def __exit__(self, *exc) -> None:
        self._client = None

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            raise RuntimeError("store not opened; use `with VectorStore(...) as s:`")
        return self._client

    def ensure_collection(self, *, recreate: bool = False) -> str:
        if recreate and self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                self.collection,
                vectors_config=VectorParams(size=self.dim, distance=Distance.COSINE),
            )
        self.client.create_payload_index(
            self.collection, field_name="entity_ids", field_schema="keyword"
        )
        return self.collection

    def index(self, chunks: list[Chunk]) -> int:
        """Idempotent on chunk_id — re-running converges rather than duplicating."""
        points = [
            PointStruct(
                id=int(hashlib.sha256(c.chunk_id.encode()).hexdigest()[:12], 16),
                vector=standin_embed(c.text, self.dim),
                payload=c.payload(self.chunking_version, self.embedding_model),
            )
            for c in chunks
        ]
        self.client.upsert(self.collection, points=points)
        return len(points)

    def retrieve(
        self, query: str, *, entity_ids: tuple[str, ...] = (), limit: int = 5
    ) -> RetrievalResult:
        """Filter by resolved identity when available; say so when it is not."""
        qfilter = None
        filters: tuple[str, ...] = ()
        if entity_ids:
            qfilter = Filter(
                must=[FieldCondition(key="entity_ids", match=MatchAny(any=list(entity_ids)))]
            )
            filters = tuple(f"entity_ids∈{e}" for e in entity_ids)

        try:
            hits = self.client.query_points(
                self.collection,
                query=standin_embed(query, self.dim),
                query_filter=qfilter,
                limit=limit,
                with_payload=True,
            ).points
        except Exception as exc:  # noqa: BLE001
            # R6.1 - never an empty list that reads as "nothing matched"
            return RetrievalResult(
                chunks=(), filters_applied=filters, identity_grounded=bool(entity_ids),
                mode=RetrievalMode.DENSE, degraded=True,
                note=f"vector store unavailable: {type(exc).__name__}",
            )

        return RetrievalResult(
            chunks=tuple(
                ScoredChunk(
                    chunk_id=h.payload["chunk_id"],
                    text=h.payload["text"],
                    score=h.score,
                    entity_ids=tuple(h.payload.get("entity_ids", ())),
                    source=h.payload["source"],
                )
                for h in hits
            ),
            filters_applied=filters,
            identity_grounded=bool(entity_ids),
            mode=RetrievalMode.DENSE,
            note="" if entity_ids else "no entity resolved; results are text-similarity only",
        )

    def drop(self) -> None:
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
