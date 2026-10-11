# Implementation Plan: Qdrant Vector Layer

Ordered by dependency. **[gate]** tasks unblock multiple downstream tasks.

Legend: `[ ]` not started · `[~]` in progress · `[x]` done

**Prerequisites:** component 00 Foundation; component 01 Phase 2 (identity and resolution)
— chunks cannot be filtered by resolved identity before resolved identity exists.

**Estimate: 1–2 weeks part-time.**

---

## Phase 0 — Infrastructure and isolation

- [ ] 0.1 Qdrant in Docker with a pinned image tag; add to `docker-compose.yml`

- [ ] 0.2 `vectors/client.py` — wrapper plus a fixture-backed fake for CI
  - The fake is not a stub: it must support payload filtering so filter logic is testable
    without an instance
  - _Requirements: 6.6_

- [ ] 0.3 **[gate]** Enforce fact-path isolation — do this first, not last
  - `GovernanceEngine` constructor accepts no retrieval dependency
  - Import lint: `biomedical.governance.*` importing `biomedical.vectors.*` fails CI
  - `tests/unit/test_fact_path_isolation.py` walks transitive imports and asserts the
    vector client is absent
  - _Requirements: 1.1, 1.2, 1.6_

> 0.3 is the gate because retrofitting this boundary is how it gets lost. The whole
> neurosymbolic claim rests on it, and a reviewer should be able to verify it in one file.

---

## Phase 1 — Collections and chunking

- [ ] 1.1 **[gate]** `vectors/collections.py`
  - Name encodes `(embedding_model, chunking_version)`
  - Creation records dimension, distance metric and index params to the manifest
  - Reject any attempt to write mixed embedding models into one collection
  - _Requirements: 4.4, 6.4_

- [ ] 1.2 `vectors/chunking.py` — versioned strategies
  - Strategy and parameters recorded as a version identifier
  - Changing strategy requires a rebuild, never a backfill
  - _Requirements: 4.2, 4.3_

- [ ] 1.3 `vectors/embedding.py`
  - Batched Bedrock Titan v2 with backoff; model id recorded on every chunk
  - _Requirements: 4.4_

- [ ] 1.4 Stable chunk identifiers
  - `doc:{source}:{doc_id}#c{n}` — deterministic, so upsert is idempotent
  - _Requirements: 6.5_

- [ ] 1.5 `tests/unit/test_collections.py`
  - Collection name round-trips to its configuration
  - Mixed-model write is rejected
  - Chunk id stability across runs

---

## Phase 2 — Provenance on chunks

- [ ] 2.1 `ProvenanceStamp` on every chunk payload
  - `source`, `source_version`, `source_document_id`, `retrieved_at`, `tier: ingested`
  - _Requirements: 4.1_

- [ ] 2.2 Mark chunks as document text, never as asserted fact
  - _Requirements: 1.4_

- [ ] 2.3 Temporal consistency with component 00
  - Document supersession supersedes its chunks; `valid_from` / `valid_to` on payload
  - _Requirements: 4.5_

- [ ] 2.4 Manifest traceability
  - Every published retrieval figure traces to a manifest recording collection, chunking
    version and embedding model
  - _Requirements: 4.6_

---

## Phase 3 — Identity-filtered retrieval

- [ ] 3.1 **[gate]** `vectors/linking.py` — populate `entity_ids` on chunk payloads
  - Uses component 01's resolver; records `method` and `confidence` per link
  - _Requirements: 2.1, 2.4_

- [ ] 3.2 Payload indexes
  - `entity_ids` (keyword, multi-valued), `source`, `tier`, `chunking_version`, `valid_to`
  - _Requirements: 2.2_

- [ ] 3.3 **[gate]** `vectors/retrieve.py` — filter construction
  - Resolved entity → filter on `entity_ids`
  - Unresolved → unfiltered **and** `identity_grounded=False` on the result
  - Links below the confidence floor are not used as filter keys
  - `filters_applied` recorded on every result
  - _Requirements: 2.2, 2.3, 2.5, 2.6_

- [ ] 3.4 `tests/unit/test_filtering.py`
  - Resolved query filters correctly
  - Unresolved query sets `identity_grounded=False` rather than silently returning text
    matches
  - A 0.3-confidence `MULTI_CANDIDATE` link is not used as a filter key
  - **Fixture case: a chunk mentioning a similar string for a different resolved entity is
    excluded.** This is the case payload filtering exists to defeat
  - _Requirements: 2.2, 2.3, 2.5_

---

## Phase 4 — Hybrid retrieval

- [ ] 4.1 Dense search (Titan v2, cosine)
  - _Requirements: 3.1_

- [ ] 4.2 Sparse lexical collection and search
  - Must retrieve exactly on NCT numbers, RXCUIs and gene symbols
  - _Requirements: 3.1, 3.3_

- [ ] 4.3 Reciprocal Rank Fusion with recorded parameters
  - `FusionSpec` (method + params) attached to every fused result
  - _Requirements: 3.1, 3.2_

- [ ] 4.4 Versioned retrieval configuration
  - Results are not comparable across configurations; the version is part of the manifest
  - _Requirements: 3.4_

- [ ] 4.5 Per-result metadata
  - Score, retrieval mode, rank on every returned chunk
  - _Requirements: 3.5_

- [ ] 4.6 `tests/unit/test_fusion.py` — determinism given recorded parameters

---

## Phase 5 — Honest retrieval evaluation

- [ ] 5.1 **[gate]** `vectors/evaluate.py`
  - recall@k and nDCG@k with `k`, query-set size, and intervals from the component 05
    `stats` module
  - _Requirements: 5.1_

- [ ] 5.2 Relevance judgment provenance
  - Record who or what produced each judgment and whether a gold standard exists
  - _Requirements: 5.2, 5.3_

- [ ] 5.3 **Query-set provenance disclosure**
  - Document the synthesis method
  - **State explicitly whether questions were generated from the documents they are meant
    to retrieve** — if so, the benchmark rewards lexical echo and the report must say so
  - _Requirements: 5.5_

- [ ] 5.4 Fair configuration comparison
  - Same query set, same judgments, both configurations
  - _Requirements: 5.4_

- [ ] 5.5 Failure categorisation
  - Categories, not only aggregates: `entity_link_failure`, `no_relevant_chunk_indexed`,
    `lexical_echo_artifact`, `chunk_boundary_split`, `embedding_mismatch`
  - _Requirements: 5.6_

- [ ] 5.6 Evaluation without a gold standard
  - Internal consistency, round-trip, small stratified manual audit with an interval
  - State what each can and cannot establish
  - _Requirements: 5.3_

---

## Phase 6 — Degradation and operations

- [ ] 6.1 Visible degradation
  - Unreachable store → `RetrievalResult(degraded=True, chunks=())`
  - **Never** a bare empty list that reads as "nothing matched"
  - _Requirements: 6.1_

- [ ] 6.2 Degradation recorded on answers
  - Grounding assessment accounts for degraded retrieval
  - _Requirements: 6.2_

- [ ] 6.3 Rule evaluation unaffected by vector-store outage
  - Document as an **availability** property; the claims register forbids presenting it as
    safety
  - _Requirements: 6.3_

- [ ] 6.4 Idempotent indexing on `chunk_id`
  - _Requirements: 6.5_

- [ ] 6.5 Rebuild-from-manifest reproducibility
  - _Requirements: 4.6_

- [ ] 6.6 `tests/unit/test_degradation.py`
  - Degraded path sets the flag; rule evaluation proceeds; the claim category recorded is
    `availability`
  - _Requirements: 6.1, 6.3_

---

## Phase 7 — Documentation

- [ ] 7.1 `docs/RETRIEVAL.md`
  - The two-path architecture and **why** retrieval is subordinate
  - The Qdrant-vs-Neo4j-native decision, including the trade-off accepted
  - _Requirements: 1.5_

- [ ] 7.2 `docs/RETRIEVAL_EVALUATION.md`
  - Metrics with intervals; query-set provenance; failure categories; what the evaluation
    cannot establish
  - _Requirements: 5.1, 5.3, 5.5_

---

## Definition of done

1. A test proves the governance engine cannot reach the vector client.
2. Retrieval filtered by resolved entity excludes a similar-string chunk for a different
   entity.
3. An unresolved query is marked `identity_grounded=False` rather than silently degrading
   to text matching.
4. Every chunk carries source, chunking and embedding provenance.
5. No collection mixes embedding models or chunking versions.
6. Retrieval metrics are published with `k`, n and intervals, and the query set's
   provenance is disclosed.
7. Vector-store outage produces a visible degraded result, documented as availability only.
8. `pytest` passes with no `.env` and no running Qdrant.

**Minimum viable subset: 0.2, 0.3, 1.1, 1.4, 2.1, 3.1, 3.3, 3.4, 6.1.**
Fact-path isolation, versioned collections, chunk provenance, identity-filtered retrieval
and visible degradation. Hybrid fusion and formal retrieval evaluation can follow — but
0.3 must be first, because it is the property the architecture claims.
