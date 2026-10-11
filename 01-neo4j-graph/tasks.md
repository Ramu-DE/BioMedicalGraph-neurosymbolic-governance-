# Implementation Plan: Neo4j Knowledge Graph

Ordered by dependency. **[gate]** tasks unblock multiple downstream tasks.

Legend: `[ ]` not started · `[~]` in progress · `[x]` done

**Prerequisite:** component 00 Foundation complete — `Fact`, `ProvenanceStamp`, `Tier`,
`ValidityInterval`, trust boundary, `RuleOutcome`.

**Estimate: 2–3 weeks part-time.**

---

## Phase 0 — Infrastructure

- [ ] 0.1 **[gate]** Neo4j 5.x in Docker, uncapped
  - `docker-compose.yml` with a pinned Neo4j image tag
  - Document why not Aura Free: ~200k node cap is below R1's 100k-entity floor once
    relationships and mentions are counted
  - _Requirements: 1.6_

- [ ] 0.2 `graph/client.py` — driver wrapper
  - Parameterised queries only; retry with backoff; explicit database selection
  - No string interpolation into Cypher anywhere in the codebase (lint rule)

- [ ] 0.3 **[gate]** `graph/migrations/` — versioned schema
  - Constraints for `ResolvedEntity`, `Identifier` (incl. `release` in the key),
    `Mention`, `FactVersion`
  - `:Provenanced` marker label with NOT NULL constraints on `source`, `source_version`,
    `tier`
  - Forward-only migrations with recorded version
  - _Requirements: 1.4, 4.3_

---

## Phase 1 — Append-only store

- [ ] 1.1 **[gate]** `graph/store.py`
  - `append`, `supersede`, `at`, `history` — and nothing else
  - _Requirements: 3.1, 3.2, 3.3_

- [ ] 1.2 `tests/unit/test_store_interface.py`
  - **Introspect the protocol and assert no `update`/`delete`/`set` verb exists**
  - This invariant decays silently without a test
  - _Requirements: 3.3_

- [ ] 1.3 `graph/temporal.py`
  - Point-in-time query; staleness marking against per-source thresholds
  - _Requirements: 3.4, 3.5_

- [ ] 1.4 Decision → fact-version references
  - Decisions reference specific versions, never current values
  - _Requirements: 3.6_

- [ ] 1.5 `tests/integration/test_temporal.py` (needs DB)
  - Supersession closes `valid_to`; both versions retrievable; point-in-time correct
  - _Requirements: 3.2, 3.7_

---

## Phase 2 — Identity and resolution

- [ ] 2.1 **[gate]** `graph/identity.py`
  - `Mention` stored verbatim and never modified after write
  - `ResolvedEntity` with domain labels applied on top
  - `HAS_IDENTIFIER` retaining every authority without overwrite
  - _Requirements: 2.1, 2.5_

- [ ] 2.2 **[gate]** `ResolutionMethod` registry
  - Confidence is a property of the method; `write_resolution()` takes a method, not a float
  - Reject any write carrying a caller-supplied confidence
  - _Requirements: 2.2, 2.8_

- [ ] 2.3 Normalisation, rule by rule
  - Case, punctuation, dose/strength/form token stripping
  - **One test per rule** — silent resolution errors originate here more than in matching
  - _Requirements: 2.2_

- [ ] 2.4 Candidate recording
  - `CANDIDATE_FOR` with score and rank for **every** candidate
  - Multi-candidate → `RESOLVES_TO(MULTI_CANDIDATE)` + `review_state=NEEDS_REVIEW`
  - _Requirements: 2.3, 2.4_

- [ ] 2.5 Resolution revision as supersession
  - Revising a resolution closes the prior interval; nothing deleted
  - _Requirements: 2.6_

- [ ] 2.6 `tests/unit/test_resolution.py`
  - One case per method from messy fixtures
  - Assert a hand-assigned confidence is rejected at write time
  - Assert all candidates recorded in multi-candidate cases
  - _Requirements: 2.2, 2.3, 2.8_

- [ ] 2.7 **[gate]** Resolution quality evaluation without a gold standard
  - Internal consistency: brand and generic forms land on one RXCUI
  - Round-trip: RXCUI → canonical name → re-resolve
  - Stratified manual audit: ~30 per confidence band, intervals via the eval `stats` module
  - _Requirements: 2.7_

- [ ] 2.8 `docs/ENTITY_RESOLUTION.md`
  - The cascade, the confidence distribution, the review backlog
  - **How it was evaluated with no answer key, and what that cannot establish**
  - _Requirements: 2.7_

---

## Phase 3 — Fact supply through the trust boundary

- [ ] 3.1 **[gate]** `graph/bundle.py`
  - `FactBundle`: eligible facts, withheld facts with reasons, content hash, policy version
  - Every fact passes `is_eligible_rule_input` before inclusion
  - _Requirements: 7.1, 7.2, 7.4_

- [ ] 3.2 Rules hold no client
  - Verify by construction that no rule can reach an unfiltered query
  - _Requirements: 7.5_

- [ ] 3.3 `tests/unit/test_bundle.py`
  - One case per `IneligibilityReason`
  - Identical inputs → identical content hash (this is also the eval parity mechanism)
  - Withheld required fact → consumer gets `CANNOT_EVALUATE`
  - _Requirements: 7.3, 7.4_

---

## Phase 4 — Release pinning and schema evolution

- [ ] 4.1 **[gate]** `graph/releases.py`
  - Pinned load; `release` in the `Identifier` key so two releases coexist
  - Missing release config = hard failure, never "latest"
  - _Requirements: 4.1, 4.2, 4.3_

- [ ] 4.2 **[gate]** Find a real release pair with a relevant change
  - An HGNC rename or MONDO obsoletion touching an entity the rules reference
  - **Highest-risk task in this component.** Without a real changed identifier that
    intersects a real rule there is no demonstration
  - If no intersection exists, add a rule referencing a gene or disease known to have
    moved — do **not** manufacture a synthetic rename
  - _Requirements: 4.4_

- [ ] 4.3 Commit trimmed release-pair fixtures; document the trimming

- [ ] 4.4 `graph/release_diff.py`
  - `ChangeKind` classification; per-vocabulary extractors into one comparison function
  - _Requirements: 4.4, 4.5, 4.6_

- [ ] 4.5 `REPLACED_BY` edges and identifier supersession
  - _Requirements: 4.8_

- [ ] 4.6 `RemediationProposal` — propose, never apply
  - Orphans yield no proposal and require human resolution
  - _Requirements: 4.7_

- [ ] 4.7 `tests/unit/test_release_diff.py`
  - One fixture case per `ChangeKind` including an orphan; determinism verified by
    running the diff twice
  - _Requirements: 4.4, 4.5, 4.6_

---

## Phase 5 — Ontology and conformance

- [ ] 5.1 `graph/ontology.py` — load the 8 carried-forward TTL modules
  - Per-module triple count reported
  - _Requirements: 5.1_

- [ ] 5.2 `graph/conformance.py`
  - `ConformanceResult` carrying **no** field named `valid`, `correct` or `true`
  - _Requirements: 5.2, 5.3_

- [ ] 5.3 Quarantine on violation
  - Non-conformant records held with their violation report; never written, never dropped
  - _Requirements: 5.4_

- [ ] 5.4 External vocabulary → ontology class mapping with recorded method
  - _Requirements: 5.5_

- [ ] 5.5 Re-validation on ontology change; report newly non-conformant records
  - _Requirements: 5.6_

- [ ] 5.6 `tests/unit/test_conformance.py`
  - Assert the result type offers no vocabulary for truth or correctness
  - _Requirements: 5.3_

---

## Phase 6 — Contradiction

- [ ] 6.1 `graph/contradiction.py` — detection across sources
  - `Contradiction` node referencing both facts and both provenances; both retained
  - _Requirements: 6.1, 6.2_

- [ ] 6.2 Recorded resolution with declared authority precedence
  - _Requirements: 6.3_

- [ ] 6.3 Unresolved → ineligible by default
  - _Requirements: 6.4_

- [ ] 6.4 Cross-authority identifier disagreement recorded, not adjudicated
  - MONDO / DOID / MeSH published cross-references disagree; that is the finding
  - _Requirements: 6.6_

- [ ] 6.5 Contradiction report listing unresolved cases with counts
  - _Requirements: 6.5_

---

## Phase 7 — Scale

- [ ] 7.1 **[gate]** Reach the scale floor
  - ≥100,000 resolved entities, ≥1,000,000 relationships, ≥5 sources, ≥20 ingested entity
    types
  - Driven by component 03; this task is the acceptance check
  - _Requirements: 1.1, 1.2, 1.3_

- [ ] 7.2 Per-tier and per-source count reporting
  - Never a single total — a reader must see how much is real
  - _Requirements: 1.4_

- [ ] 7.3 Capacity pre-flight
  - Fail at configuration time with projected counts rather than truncating mid-ingest
  - _Requirements: 1.6_

- [ ] 7.4 Public description states the ingested slice
  - _Requirements: 1.5_

---

## Phase 8 — Verifiability

- [ ] 8.1 `graph/queries/` — the documented verification queries
  - Tier composition; derived edges with method/evidence/confidence; resolution confidence
    distribution; review backlog; unresolved contradictions; point-in-time
  - Publish in the README so a reader can check every count
  - _Requirements: 8.1, 8.2_

- [ ] 8.2 Derived-tier purge
  - Removes exactly the derived tier, nothing else
  - _Requirements: 8.3_

- [ ] 8.3 Rebuild-from-manifest reproducibility
  - Rebuild from pinned sources and slices; counts match the manifest within a documented
    tolerance
  - _Requirements: 8.4_

- [ ] 8.4 CI without a database
  - Graph tests skippable; everything else passes with no `.env`
  - _Requirements: 8.5_

- [ ] 8.5 Publish the backlog
  - Review backlog and unresolved contradiction counts in the README
  - A graph with a visible backlog is more credible than one with none

---

## Definition of done

1. A mention and a resolved entity are distinct objects, and `CANDIDATE_FOR` holds every
   competing candidate.
2. No confidence value in the graph was supplied by a caller.
3. The store interface offers no way to mutate or delete a fact.
4. Two vocabulary releases coexist, and the diff between them is deterministic.
5. A rule cannot obtain an ineligible fact by any code path.
6. A reader can reproduce every published count with a documented query.
7. `pytest` passes with no `.env`.
8. Scale floors in R1 are met and reported per tier and per source.

**Minimum viable subset: 0.1, 0.3, 1.1, 1.2, 2.1, 2.2, 2.4, 2.7, 3.1, 4.1, 4.2, 4.4.**
Identity with candidates, method-derived confidence, resolution evaluated without a gold
standard, the trust-boundary bundle, and release diffing — which together close
`CDL-9b` and `CDL-9d` and make `CDL-9a` a matter of running the ingest longer.
