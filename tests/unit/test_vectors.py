"""Component 02 — structural retrieval behaviour, no live instance needed."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from biomedical.vectors.store import (
    Chunk,
    RetrievalMode,
    RetrievalResult,
    VectorStore,
    standin_embed,
)

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def chunk(cid: str, text: str, entities: tuple[str, ...] = ()) -> Chunk:
    return Chunk(
        chunk_id=cid, text=text, source="dailymed", source_version="2026-09",
        source_document_id="doc1", retrieved_at=NOW, entity_ids=entities,
    )


# -- stand-in embedding is deterministic and honest about itself ---------------


def test_standin_embedding_is_deterministic():
    assert standin_embed("pembrolizumab") == standin_embed("pembrolizumab")


def test_standin_embedding_is_normalised():
    v = standin_embed("melanoma", dim=128)
    assert len(v) == 128
    assert sum(x * x for x in v) == pytest.approx(1.0, abs=1e-6)


def test_different_text_gives_different_vectors():
    assert standin_embed("a") != standin_embed("b")


# -- R4.2, R4.4 : collection name encodes its configuration -------------------


def test_collection_name_encodes_model_and_chunking():
    s = VectorStore(url="x", api_key="y", embedding_model="amazon.titan-embed-text-v2:0",
                    chunking_version="chunk-v3")
    assert "titan-embed-text-v2-0" in s.collection
    assert s.collection.endswith("chunk-v3")
    assert s.collection.startswith("biomedv2__")


def test_changing_the_model_changes_the_collection():
    a = VectorStore(url="x", api_key="y", embedding_model="m1").collection
    b = VectorStore(url="x", api_key="y", embedding_model="m2").collection
    assert a != b, "embeddings from different models must not co-reside"


def test_changing_chunking_changes_the_collection():
    a = VectorStore(url="x", api_key="y", chunking_version="chunk-v1").collection
    b = VectorStore(url="x", api_key="y", chunking_version="chunk-v2").collection
    assert a != b


# -- R4.1 : chunk payload carries full provenance -----------------------------


def test_payload_carries_provenance_and_configuration():
    p = chunk("c1", "text", ("ent:drug:x",)).payload("chunk-v1", "m1")
    for required in (
        "source", "source_version", "source_document_id", "retrieved_at",
        "tier", "chunking_version", "embedding_model", "entity_ids",
    ):
        assert required in p, required
    assert p["tier"] == "ingested", "a chunk is document text, never an asserted fact"


# -- R2.3, R2.6 : identity grounding is a signal, not an absence --------------


def test_unfiltered_result_is_marked_as_such():
    r = RetrievalResult(chunks=(), filters_applied=(), identity_grounded=False,
                        mode=RetrievalMode.DENSE, note="no entity resolved")
    assert not r.identity_grounded
    assert "UNFILTERED" in r.describe()


def test_filtered_result_records_its_filters():
    r = RetrievalResult(chunks=(), filters_applied=("entity_ids∈ent:drug:x",),
                        identity_grounded=True, mode=RetrievalMode.DENSE)
    assert r.identity_grounded
    assert "identity-grounded" in r.describe()
    assert r.filters_applied


# -- R6.1 : degradation is visible, never a silent empty result ---------------


def test_degraded_retrieval_is_distinguishable_from_no_matches():
    degraded = RetrievalResult(chunks=(), filters_applied=(), identity_grounded=True,
                               mode=RetrievalMode.DENSE, degraded=True, note="unreachable")
    empty = RetrievalResult(chunks=(), filters_applied=(), identity_grounded=True,
                            mode=RetrievalMode.DENSE)
    assert degraded.degraded and not empty.degraded
    assert "DEGRADED" in degraded.describe()
    assert "DEGRADED" not in empty.describe()


def test_unopened_store_refuses_to_act():
    with pytest.raises(RuntimeError, match="not opened"):
        _ = VectorStore(url="x", api_key="y").client


def test_retrieve_on_unreachable_host_returns_degraded_not_exception():
    with VectorStore(url="https://127.0.0.1:1", api_key="none", timeout=2) as s:
        r = s.retrieve("query", entity_ids=("ent:drug:x",))
    assert r.degraded
    assert r.chunks == ()
    assert "unavailable" in r.note
