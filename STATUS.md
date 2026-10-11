# Implementation status

Working code, verified against the live Aura and Qdrant instances in `.env`.

```bash
.venv/bin/python config.py                      # redacted readiness report
.venv/bin/python scripts/verify_services.py     # connectivity + capability probe
.venv/bin/python -m pytest                      # 100 tests, no credentials needed
.venv/bin/python scripts/check_gate1.py         # Gate 1 criteria + claims lint
.venv/bin/python scripts/demo_end_to_end.py     # live Neo4j: trust boundary -> verdict
.venv/bin/python scripts/demo_vectors.py        # live Qdrant: identity-filtered retrieval
```

## Verified environment

| Service | Status | Notes |
|---|---|---|
| Neo4j Aura | live | **5.27-aura enterprise**, vector + fulltext procedures present, write confirmed, 7 constraints created |
| Qdrant Cloud | live | 40 pre-existing collections left untouched; this work uses the `biomedv2__*` namespace |
| Embeddings / LLM | **not configured** | no Bedrock credentials; a deterministic hash stand-in is used |

**On the stand-in embedding.** It is normalised and deterministic, so every
*structural* property is testable — payload filtering, idempotent indexing,
collection isolation, visible degradation. It carries no semantic signal, so **no
retrieval-quality claim may be made from it.** Both the readiness report and the
demo say so on screen. Configure a real provider before measuring relevance.

## Built

### Component 00 — Foundation (complete; Gate 1 passed 6/6)

`biomedical/foundation/` — no I/O, no credentials, no domain logic.

| Module | What it enforces |
|---|---|
| `stamp.py` | `Tier` (4), `ResolutionMethod` registry, `Confidence` bound to a method, `ProvenanceStamp` |
| `temporal.py` | `ValidityInterval`, per-source `StalenessPolicy` |
| `contradiction.py` | `Contradiction`, `Resolution` requiring a cited authority, `AuthorityPrecedence` |
| `fact.py` | `Fact[T]` — stamp is a required parameter; `FactReference` |
| `trust.py` | `is_eligible_rule_input`, 7 `IneligibilityReason`s, versioned `TrustPolicy` |
| `bundle.py` | `FactBundle` with content hash, `assemble`, `assert_parity` |
| `outcome.py` | `RuleOutcome` incl. **`CANNOT_EVALUATE`**, `Verdict`, `Timings` |
| `claims.py` | `ClaimsRegister`, category separation, banned-phrase and bare-rate lint |
| `manifest.py` | `RunManifest`, `design_hash` |

### Component 01 — Graph (partial)

`biomedical/graph/store.py` — `GraphClient` (parameterised queries only) and
`FactStore` exposing `append` / `supersede` / `at` / `history` / `candidates` /
`tier_counts` / `purge_derived`. **No update, no delete** — asserted by
introspection in `test_isolation_and_interface.py`. 8 migrations create the
natural-key constraints and the mandatory-provenance constraints.

Not yet built: `Mention` / `CANDIDATE_FOR` resolution model, release pinning and
diffing, SHACL conformance, contradiction detection over real sources.

### Component 02 — Vectors (partial)

`biomedical/vectors/store.py` — collection naming that encodes
`(embedding_model, chunking_version)`, chunk payloads carrying full provenance,
idempotent indexing on `chunk_id`, retrieval filtered by resolved `entity_ids`,
`identity_grounded` as an explicit signal, and visible degradation.

Not yet built: sparse/hybrid fusion, retrieval evaluation.

## What the demos actually showed

**`demo_end_to_end.py`** — three tiers written to Aura, then read back through
the trust boundary:

- `FactBundle(3 eligible, 2 withheld, hash=05ba4e12…, policy=v1.0)`
- a rule whose dependency was withheld returned **`cannot_evaluate`**, and the
  verdict read `INCONCLUSIVE` rather than `ALLOWED`
- withheld facts recorded their reasons: `tier_too_weak,
  confidence_below_floor, awaiting_review`
- supersession retained both versions; the point-in-time query for yesterday
  returned the superseded value
- **end-to-end governance latency: 389.46 ms** — of which rule evaluation was
  0.05 ms

That last line matters. v1 published **0.09 ms** as the cost of producing a
verdict. The reviewer asked whether it included database access. It did not: the
graph read is ~389 ms against Aura, roughly four orders of magnitude larger than
the figure that was published. `Timings` now makes the component figure
un-publishable without its qualifying note.

**`demo_vectors.py`** — indexed four chunks including a deliberate trap: a
nivolumab chunk with near-identical wording to the pembrolizumab chunks.

- unfiltered retrieval returned all four and flagged itself
  `UNFILTERED (identity unresolved)`
- filtering on the resolved entity returned 2 of 4, excluding both the
  nivolumab chunk and the unlinked trial chunk
- an unreachable store returned `degraded=True`, not an empty list that reads as
  "nothing matched"

## Tests

100 tests, **all passing with no `.env` and no network**:

| File | Covers |
|---|---|
| `test_stamp.py` | unstamped facts are a type error; confidence cannot be authored |
| `test_trust.py` | one test per `IneligibilityReason`; per-source staleness; policy versioning |
| `test_bundle_and_outcome.py` | parity hashing; `CANNOT_EVALUATE` is never safe; verdict records withheld facts |
| `test_claims_temporal_contradiction.py` | half-open intervals; authority precedence; claims lint catches v1's own headline |
| `test_isolation_and_interface.py` | fact path never meets retrieval path; no mutating verb on the store; no interpolated Cypher |
| `test_vectors.py` | collection isolation; provenance on payloads; degradation distinguishable from no-matches |

### Component 05 — Evaluation (Phase 1–2 complete; the experiment has run)

| Module | What it does |
|---|---|
| `eval/stats.py` | `Confusion`, Wilson intervals, Cohen's κ, seeded bootstrap CI, degeneracy detection. `accuracy` exists but carries a "not a headline metric" note. |
| `eval/cases.py` | 12 governance cases, pre-registered design, class balance stated (7 wrong / 5 correct) |
| `eval/arms.py` | four arms; `FactSet` materialised once per case and content-hashed, so parity is structural |
| `clients/bedrock.py` | Converse + Titan embeddings, with latency, token and cost accounting |

**The four-arm experiment has been run against live Bedrock.** Result, limits and
the claim-register changes are in [`FINDINGS.md`](./FINDINGS.md). Short version:
giving the judge the same facts the rules get moved κ from **0.273 to 0.833**, so
the v1 comparison was confounded exactly as the reviewer said — the variable was
evidence access, not evaluator architecture.

### Bedrock access, as tested

| Model | Status |
|---|---|
| `amazon.titan-embed-text-v2:0` | works, 1024-dim |
| `us.anthropic.claude-sonnet-4-6` | works — used as the judge (same model as v1) |
| `us.anthropic.claude-haiku-4-5-20251001-v1:0` | works |
| `us.amazon.nova-2-lite-v1:0` | works |
| Claude 5.x (`claude-sonnet-5-5`, `claude-haiku-5-5`) | **AccessDenied** — private marketplace eligibility on this `WSParticipantRole` |

## Not yet built

Multi-source ingestion (component 03), the declarative governance rule engine
(04), evaluation Phases 3–7, UI (06). Order and gates are in
[`ROADMAP.md`](./ROADMAP.md).

Next highest-value work: wire the four arms to read facts from Neo4j through
`foundation.bundle.assemble` rather than from authored dictionaries, so the
experiment runs over stored, provenanced, trust-filtered facts. The mechanism
already exists and is tested.

## Two operational notes

**Aura capacity.** This is an *enterprise* instance, so it is not subject to the
~200k node cap that Aura Free imposes. Confirm the actual limit on your plan
before the component 01 scale gate (≥100k resolved entities, ≥1M relationships);
`docker-compose` with self-hosted Neo4j remains the uncapped fallback.

**Credentials.** `.env` is gitignored and a scan confirms no secret appears in
any source file. The Aura password and Qdrant API key were shared in plaintext
during setup, so rotate both before this becomes a public repository — treat them
as development-only credentials.
