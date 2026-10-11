# Design: Qdrant Vector Layer

## Overview

Two jobs: retrieve relevant text, and stay out of the fact path.

```
                        ┌──────────────────────────────┐
   user query ──────────►  entity linking (comp. 01)   │
                        └───────────┬──────────────────┘
                                    │ resolved entity_ids
              ┌─────────────────────┴────────────────────┐
              ▼                                          ▼
   ┌─────────────────────┐                  ┌──────────────────────────┐
   │ 02 QDRANT           │                  │ 01 NEO4J                 │
   │ hybrid search,      │                  │ FactBundle through the   │
   │ payload-filtered by │                  │ trust boundary           │
   │ entity_id           │                  └────────────┬─────────────┘
   └──────────┬──────────┘                               │
              │ chunks                                   │ eligible facts
              ▼                                          ▼
      ┌───────────────┐                        ┌──────────────────┐
      │ LLM generation│                        │  RULE ENGINE     │
      └───────────────┘                        └──────────────────┘
              │                                          │
              └────────────► verdict precedence ◄─────────┘
                        symbolic overrides neural
```

**The two arrows never cross.** The LLM reads chunks; rules read facts. v1 had this property
and it was the thing neither reviewer attacked — here it is enforced by dependency structure
rather than by convention.

> **Design stance.** This component is deliberately less interesting than it could be. Every
> temptation to let retrieval contribute to a verdict is refused, because similarity is not
> evidence.

---

## Deployment

| Environment | Target |
|---|---|
| Development | Qdrant in Docker, pinned image tag |
| CI | no instance — fixture-backed fake client |
| Hosted demo | Qdrant Cloud (free tier is ample for a demo slice) |

---

## Collections

One collection per `(embedding_model, chunking_version)` pair. Mixing is forbidden (R4.4):
embeddings from different models are not comparable, and silently co-residing them is a
class of bug that produces plausible nonsense.

```
biomed__titan-v2__chunk-v3        dense 1024-d, cosine
biomed__titan-v2__chunk-v3__sparse  sparse lexical
```

Collection name encodes the configuration, so a manifest reference is unambiguous and a
stale collection is visible by inspection.

### Payload schema

```python
{
  "chunk_id": "doc:openfda:faers:12345#c2",   # stable -> idempotent upsert (R6.5)
  "text": "...",

  # provenance — same stamp as everything else (R4.1)
  "source": "openfda:faers",
  "source_version": "2026-09",
  "source_document_id": "...",
  "retrieved_at": "...",
  "tier": "ingested",

  # chunking + embedding provenance (R4.2)
  "chunking_version": "chunk-v3",
  "embedding_model": "amazon.titan-embed-text-v2:0",

  # the integration that matters (R2.1)
  "entity_ids": ["ent:drug:00123", "ent:disease:00456"],
  "entity_links": [
      {"entity_id": "ent:drug:00123",
       "method": "normalised_exact", "confidence": 0.95}
  ],

  # temporal (R4.5)
  "valid_from": "...", "valid_to": null
}
```

Indexed payload fields: `entity_ids` (keyword), `source`, `tier`, `chunking_version`,
`valid_to`. `entity_ids` is the one that earns its keep.

---

## Module layout

```
biomedical/vectors/
├── client.py          Qdrant wrapper; fixture-backed fake for CI
├── collections.py     naming, creation, config recorded to manifest
├── chunking.py        versioned strategies; boundaries are a decision, not an accident
├── embedding.py       batched Bedrock embedding with backoff; model id recorded
├── indexer.py         idempotent upsert on chunk_id
├── retrieve.py        dense / sparse / fused; filter construction
├── linking.py         chunk -> entity_id payload population (uses comp. 01 resolver)
└── evaluate.py        retrieval metrics with intervals, failure categorisation
```

### `retrieve.py` — filter construction

```python
@dataclass(frozen=True)
class RetrievalResult:
    chunks: tuple[ScoredChunk, ...]
    filters_applied: tuple[Filter, ...]     # R2.6
    identity_grounded: bool                 # False when nothing resolved — R2.3
    mode: RetrievalMode                     # dense | sparse | fused
    fusion: FusionSpec | None               # method + params — R3.2
    degraded: bool = False                  # R6.1
```

`identity_grounded=False` is a first-class signal, not an absence. A caller must be able to
tell "filtered to this entity" from "we could not resolve the entity, so this is a text
match." v1 could not express that distinction, and it is where retrieval quietly stops being
about the thing you asked about.

Links below the confidence floor are not used as filter keys (R2.5) — a 0.3-confidence
`MULTI_CANDIDATE` resolution is not a sound basis for narrowing a search.

### Hybrid fusion

Dense (Titan v2, cosine) + sparse lexical, fused by Reciprocal Rank Fusion with the `k`
parameter recorded. Sparse exists because biomedical queries carry exact tokens — NCT
numbers, RXCUIs, gene symbols — where dense embeddings are weakest and lexical match is
exactly right (R3.3).

Fusion parameters are part of the configuration version. Comparing retrieval across fusion
settings without recording them is how retrieval claims become unreproducible.

### `chunking.py`

Chunk boundaries materially change retrieval, so strategy is versioned and a collection
records which version produced it. Changing strategy means rebuilding, never backfilling
into an existing collection (R4.3).

---

## The structural guarantee (R1)

```python
# governance engine construction — no vector client in scope
engine = GovernanceEngine(
    fact_bundle_source=graph.bundle_assembler,   # comp. 01, trust-boundary filtered
    rules=rule_packs,
    # deliberately absent: any retrieval client
)
```

Enforced three ways:

1. **Constructor signature** — the engine accepts no retrieval dependency.
2. **Import lint** — `biomedical.governance.*` importing `biomedical.vectors.*` fails CI.
3. **Dependency test** — `test_fact_path_isolation.py` walks the engine's transitive imports
   and asserts the vector client is absent (R1.6).

Three mechanisms for one property is justified because this is the invariant the whole
neurosymbolic claim rests on. A reviewer can check it in one test file.

---

## Honest retrieval evaluation (R5)

The discipline from component 05 applies here, including the uncomfortable part.

| Metric | Reported as |
|---|---|
| recall@k, nDCG@k | with `k`, query-set size, and a Wilson or bootstrap interval |
| per-category failure counts | not only aggregates (R5.6) |

**The query-set provenance disclosure (R5.5) is the important one.** If questions are
synthesised by extracting salient terms from a chunk and asking which section discusses
them, the golden document is by construction the chunk the vocabulary came from — so any
term-overlap retriever wins for free and the benchmark measures lexical echo. Where a
synthesised query set is used, the report must say so and state the limitation.

Where no gold standard exists, `evaluate.py` reports what it can establish and what it
cannot (R5.3), using the same three techniques as entity resolution: internal consistency,
round-trip, and a small stratified manual audit with an interval.

---

## Degradation (R6)

| Condition | Behaviour |
|---|---|
| Qdrant unreachable | `RetrievalResult(degraded=True, chunks=())` — never an empty result that reads as "nothing matched" |
| Degraded retrieval | recorded on the answer; grounding assessment accounts for it |
| Qdrant down | rule evaluation unaffected — **documented as availability, explicitly not safety** (R6.3) |

R6.3 is the corrected version of v1's claim. *"When the LLM went dark, all rules still
fired. Fails closed, not open"* presented an availability observation as a safety property.
The behaviour is worth keeping; the claim it supports is narrower, and the claims register
enforces the distinction.

---

## Testing strategy

**No instance required (runs in CI, no `.env`):**

- `test_fact_path_isolation.py` — **the critical test.** Governance engine's transitive
  imports exclude the vector client.
- Filter construction: resolved entity → filter; unresolved → `identity_grounded=False`;
  below-floor link not used as a key.
- Chunk id stability → idempotent upsert.
- Fusion determinism given recorded parameters.
- Collection naming round-trips to its configuration.
- Degraded path returns `degraded=True`, never a bare empty list.

**With instance (behind `skip_without_credentials`):** payload index behaviour, filtered vs
unfiltered recall on a fixture corpus, rebuild reproducibility from manifest.

Fixtures: a small corpus with known entity links, including chunks that mention a *similar
string* for a different resolved entity — the case payload filtering exists to defeat, and
the one a pure text index gets wrong.

---

## Why Qdrant rather than Neo4j-native vectors

v1 used `VECTOR_BACKEND=qdrant` with Neo4j-native and OpenSearch as alternatives. The
decision is now principled rather than configurable:

| Need | Qdrant | Neo4j-native |
|---|---|---|
| Payload filtering on a multi-valued keyword field | first-class, indexed | workable but awkward at scale |
| Separate dense and sparse collections with independent lifecycle | yes | no |
| Rebuilding the index without touching the graph | yes — different store | couples two lifecycles |
| Keeping retrieval *architecturally* subordinate | separate service makes the boundary visible | co-residence invites shortcuts |

The last row is the real argument. Co-locating chunks with facts makes it easy to write one
query that reads both, and that query is the end of the governance property. A separate
service makes the boundary something you have to cross deliberately.

Trade-off accepted: two stores to operate, and chunk↔entity consistency must be maintained
by the indexer rather than guaranteed by the database. The indexer's idempotency on
`chunk_id` plus rebuild-from-manifest is the mitigation.
