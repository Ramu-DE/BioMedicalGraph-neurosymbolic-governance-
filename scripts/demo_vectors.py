"""Demonstrate identity-filtered retrieval against live Qdrant (component 02).

The case payload filtering exists to defeat: a chunk whose *text* looks relevant
but which is about a different resolved entity. Pure text similarity retrieves
it; identity filtering does not.

    .venv/bin/python scripts/demo_vectors.py

Uses a `biomedv2__*` collection namespace and removes it afterwards, so the 40
existing collections in this cluster are untouched.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from biomedical.vectors.store import Chunk, VectorStore  # noqa: E402

NOW = datetime.now(timezone.utc)

PEMBRO = "ent:drug:pembrolizumab"
NIVO = "ent:drug:nivolumab"

CHUNKS = [
    Chunk(
        chunk_id="doc:label:pembro#c1",
        text="Pembrolizumab is a PD-1 blocking antibody indicated for melanoma.",
        source="dailymed", source_version="2026-09",
        source_document_id="label:pembro", retrieved_at=NOW,
        entity_ids=(PEMBRO,),
        entity_links=({"entity_id": PEMBRO, "method": "exact_identifier", "confidence": 1.0},),
    ),
    Chunk(
        chunk_id="doc:label:pembro#c2",
        text="Immune-mediated pneumonitis has been reported with pembrolizumab therapy.",
        source="dailymed", source_version="2026-09",
        source_document_id="label:pembro", retrieved_at=NOW,
        entity_ids=(PEMBRO,),
        entity_links=({"entity_id": PEMBRO, "method": "exact_identifier", "confidence": 1.0},),
    ),
    # the trap: near-identical wording, DIFFERENT resolved entity
    Chunk(
        chunk_id="doc:label:nivo#c1",
        text="Nivolumab is a PD-1 blocking antibody indicated for melanoma.",
        source="dailymed", source_version="2026-09",
        source_document_id="label:nivo", retrieved_at=NOW,
        entity_ids=(NIVO,),
        entity_links=({"entity_id": NIVO, "method": "exact_identifier", "confidence": 1.0},),
    ),
    Chunk(
        chunk_id="doc:trial:NCT00001#c1",
        text="Open-label phase 3 trial of checkpoint inhibition in advanced melanoma.",
        source="clinicaltrials", source_version="2026-09",
        source_document_id="NCT00001", retrieved_at=NOW,
        entity_ids=(),  # deliberately unlinked
    ),
]


def main() -> int:
    cfg = config.load()
    if not cfg.qdrant.configured:
        print("qdrant not configured; nothing to demonstrate")
        return 1

    with VectorStore(
        url=cfg.qdrant.url,
        api_key=cfg.qdrant.api_key.reveal(),
        embedding_model=cfg.embedding.model_id,
        dim=cfg.embedding.dim,
        timeout=cfg.qdrant.timeout,
    ) as store:
        print(f"collection: {store.collection}")
        if not cfg.embedding.configured:
            print("embedding provider: NONE -> deterministic stand-in in use.")
            print("  Structural behaviour below is real. Semantic ranking is not")
            print("  meaningful and no retrieval-quality claim may be made from it.\n")

        store.ensure_collection(recreate=True)
        n = store.index(CHUNKS)
        print(f"indexed {n} chunks (idempotent on chunk_id)")
        again = store.index(CHUNKS)
        count = store.client.count(store.collection).count
        print(f"re-indexed {again}; collection holds {count} points -> idempotent\n")

        query = "PD-1 blocking antibody for melanoma"

        print("1) unfiltered — identity unresolved")
        r = store.retrieve(query, limit=4)
        print(f"   {r.describe()}")
        print(f"   note: {r.note}")
        for c in r.chunks:
            print(f"     {c.score:+.4f}  {c.chunk_id:<26} entities={list(c.entity_ids) or '[]'}")

        print("\n2) filtered to the resolved entity")
        r2 = store.retrieve(query, entity_ids=(PEMBRO,), limit=4)
        print(f"   {r2.describe()}")
        for c in r2.chunks:
            print(f"     {c.score:+.4f}  {c.chunk_id:<26} entities={list(c.entity_ids)}")

        returned = {c.chunk_id for c in r2.chunks}
        trap = "doc:label:nivo#c1"
        unlinked = "doc:trial:NCT00001#c1"
        print()
        print(f"   nivolumab chunk excluded ....... {trap not in returned}")
        print(f"   unlinked trial chunk excluded .. {unlinked not in returned}")
        print(f"   all results are pembrolizumab .. {all(PEMBRO in c.entity_ids for c in r2.chunks)}")
        print(f"   identity_grounded flag ......... {r2.identity_grounded}")

        print("\n3) degradation is visible, not silent")
        broken = VectorStore(url="https://127.0.0.1:1", api_key="none", dim=cfg.embedding.dim)
        with broken as b:
            r3 = b.retrieve(query, entity_ids=(PEMBRO,))
        print(f"   {r3.describe()}")
        print(f"   note: {r3.note}")
        print(f"   degraded flag set rather than an empty 'nothing matched' .. {r3.degraded}")

        store.drop()
        print(f"\ncleanup: dropped {store.collection}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
