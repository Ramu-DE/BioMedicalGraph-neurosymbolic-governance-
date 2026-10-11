# Design: Neo4j Knowledge Graph

## Overview

The graph's job is **identity over time with recorded uncertainty**. Everything else —
retrieval, rules, UI — reads from it.

The structural change from v1 is the separation of **mention** from **resolved entity**.
v1 conflated them: a CSV row *was* the drug. Here an external record is a `Mention`, a
`ResolvedEntity` is a hypothesis about what that mention refers to, and `RESOLVES_TO` is a
dated, evidenced, revisable claim connecting them. Entity resolution stops being a lookup
table and becomes a modelled, auditable judgment.

```
   Mention                  RESOLVES_TO                ResolvedEntity
 ┌──────────────┐         (method, evidence,          ┌──────────────────┐
 │ raw string   │────────── confidence, valid_*,  ────►│ canonical entity │
 │ from FAERS   │           review_state)              │ + all authority  │
 │ + provenance │                                      │   identifiers    │
 └──────────────┘         may be 1-to-many             └──────────────────┘
       ▲                   (candidates retained)               │
       │                                                       │ ASSERTS
   one per external record                            ┌────────▼─────────┐
                                                      │ Fact (versioned) │
                                                      └──────────────────┘
```

> **Design stance.** Most of this component's cost is paid to make resolution *wrong in
> public* rather than *right in private*. A candidate list with scores and a review queue
> is less tidy than a single authored mapping, and that untidiness is the deliverable.

---

## Deployment

| Environment | Target | Why |
|---|---|---|
| Development | **Neo4j 5.x in Docker** | Aura Free caps near 200k nodes / 400k rels — below R1's floor. Docker is uncapped and free. |
| Hosted demo | Aura Pro | only if a live URL is needed |
| CI | no database | graph tests behind `skip_without_credentials`; everything else runs dry |

The v1 graph's 545 nodes were partly a *capacity* artefact. Naming that is worth a sentence
in the talk — "my graph was small because my free tier was small" is a more honest
explanation than a design rationale.

---

## Label and relationship model

### Core structural labels

| Label | Purpose | Tier |
|---|---|---|
| `Mention` | one external record as received, pre-resolution | `ingested` |
| `ResolvedEntity` | canonical entity; subtyped `:Drug`, `:Disease`, `:Gene`, `:Trial`, … | `ingested`/`derived` |
| `Identifier` | one authority's id for an entity (RXCUI, ChEMBL, MONDO, HGNC…) | `ingested` |
| `FactVersion` | a property assertion with validity interval | varies |
| `Contradiction` | two or more incompatible `FactVersion`s | `derived` |
| `VocabRelease` | a pinned external release | `ingested` |
| `RemediationProposal` | a proposed identifier replacement, pending | `derived` |
| `Ctx_*` | runtime decisions, actions, outcomes | `runtime` |

Domain labels (`:Drug`, `:Disease`, `:AdverseEvent`, `:ClinicalTrial`, `:Gene`,
`:Protein`, `:Pathway`, …) are applied **to** `ResolvedEntity` nodes, so every domain
entity inherits identity and temporal machinery automatically rather than per type.

### Key relationships

```
(Mention)-[:RESOLVES_TO {method, evidence, confidence, valid_from, valid_to, review_state}]->(ResolvedEntity)
(Mention)-[:CANDIDATE_FOR {score, rank}]->(ResolvedEntity)      // all candidates retained (R2.3)
(ResolvedEntity)-[:HAS_IDENTIFIER {authority, confidence}]->(Identifier)
(ResolvedEntity)-[:ASSERTS]->(FactVersion)
(FactVersion)-[:SUPERSEDES]->(FactVersion)
(FactVersion)-[:IN_CONTRADICTION]->(Contradiction)
(Identifier)-[:FROM_RELEASE]->(VocabRelease)
(Identifier)-[:REPLACED_BY {change_kind}]->(Identifier)
```

`CANDIDATE_FOR` is the edge that makes R2.3 real. v1 had no way to express "it might be one
of these three."

### Constraints and indexes

```cypher
CREATE CONSTRAINT resolved_entity_id   FOR (e:ResolvedEntity) REQUIRE e.entity_id IS UNIQUE;
CREATE CONSTRAINT identifier_natural   FOR (i:Identifier)     REQUIRE (i.authority, i.value, i.release) IS UNIQUE;
CREATE CONSTRAINT mention_natural      FOR (m:Mention)        REQUIRE (m.source, m.source_record_id) IS UNIQUE;
CREATE CONSTRAINT fact_version_id      FOR (f:FactVersion)    REQUIRE f.version_id IS UNIQUE;

// provenance is mandatory — R1.5 of foundation, enforced at the DB layer too
CREATE CONSTRAINT prov_source   FOR (n:Provenanced) REQUIRE n.source IS NOT NULL;
CREATE CONSTRAINT prov_version  FOR (n:Provenanced) REQUIRE n.source_version IS NOT NULL;
CREATE CONSTRAINT prov_tier     FOR (n:Provenanced) REQUIRE n.tier IS NOT NULL;
```

The `Identifier` key includes `release`, which is what lets two vocabulary releases coexist
(R4.3). A `:Provenanced` marker label on every ingested/derived node gives database-level
enforcement of the provenance stamp — belt and braces alongside the type-level guarantee in
component 00.

---

## Module layout

```
biomedical/graph/
├── client.py          driver, retry, parameterised queries only
├── store.py           append / supersede ONLY — no update, no delete  (R3.3)
├── identity.py        Mention, ResolvedEntity, candidate recording
├── resolution.py      RESOLVES_TO writes; registered methods; review state
├── temporal.py        point-in-time queries, staleness marking
├── contradiction.py   detection and recorded resolution
├── releases.py        pinned loading, side-by-side addressing
├── release_diff.py    ChangeKind classification
├── ontology.py        TTL loading, class mapping
├── conformance.py     SHACL → ConformanceResult (never "truth")
├── bundle.py          FactBundle assembly through the trust boundary  (R7)
├── migrations/        versioned schema migrations
└── queries/           the documented verification queries  (R8)
```

### `store.py` — the no-mutation contract

```python
class FactStore(Protocol):
    def append(self, fact: Fact, ...) -> VersionId: ...
    def supersede(self, prior: VersionId, new: Fact, at: datetime) -> VersionId: ...
    def at(self, entity_id: str, prop: str, instant: datetime) -> Fact | None: ...
    def history(self, entity_id: str, prop: str) -> Sequence[Fact]: ...
```

No `update`, no `delete` (R3.3). Supersession closes `valid_to` on the prior version and
appends. A test asserts by introspection that no mutating verb exists on the interface —
this is the kind of invariant that decays silently without a test.

### `resolution.py` — registered methods only

```python
class ResolutionMethod(Enum):
    EXACT_IDENTIFIER       = ("exact_identifier", 1.00)
    NORMALISED_EXACT       = ("normalised_exact", 0.95)
    UNIQUE_INGREDIENT      = ("unique_ingredient_after_dose_strip", 0.90)
    SINGLE_FUZZY_ABOVE_TAU = ("single_fuzzy_above_threshold", 0.60)
    MULTI_CANDIDATE        = ("multiple_candidates", 0.30)   # -> needs_review
```

Confidence is a property **of the method**, never a caller argument. `write_resolution()`
accepts a `ResolutionMethod`, not a float, so R2.8 is enforced by signature. The lint rule
from component 00 catches any attempt to route around it.

`MULTI_CANDIDATE` sets `review_state = NEEDS_REVIEW`, which makes the mention ineligible as
a rule input until cleared — the trust boundary doing real work on real data.

### `bundle.py` — the fact supply contract

```python
@dataclass(frozen=True)
class FactBundle:
    facts: Mapping[str, Fact]
    withheld: tuple[WithheldFact, ...]     # fact ref + IneligibilityReason
    content_hash: str
    trust_policy_version: str
```

Assembled by this component, consumed by component 04. Rules hold no client (R7.5). The
content hash serves double duty: it proves two consumers saw identical evidence, which is
exactly the parity mechanism the evaluation framework needs for its `judge_grounded` arm.
One mechanism, two problems.

---

## Entity resolution flow

```
external record
      │
      ▼
 ┌─────────────┐   store verbatim, stamped
 │  Mention    │   (never modified afterwards)
 └──────┬──────┘
        ▼
 normalise ──► candidate search (identifier → normalised → ingredient → fuzzy)
        │
    ┌───┴────────────────────────┐
    │                            │
 exactly one candidate      multiple candidates
    │                            │
    ▼                            ▼
 RESOLVES_TO                CANDIDATE_FOR × n
 method-derived conf        + RESOLVES_TO(MULTI_CANDIDATE, needs_review)
    │                            │
    ▼                            ▼
 eligible as rule input     ineligible until reviewed
```

Normalisation rules are individually tested — case folding, punctuation, dose/strength/form
token stripping. Silent resolution errors originate in normalisation more than in matching,
and an untested normaliser is where a graph quietly becomes wrong.

### Evaluating resolution without a gold standard (R2.7)

There is no answer key for FAERS drug strings. Three techniques, none establishing
correctness, and the documentation must say so:

| Technique | Detects | Cannot establish |
|---|---|---|
| Internal consistency — brand and generic forms must land on one RXCUI | definite errors | correctness |
| Round-trip — RXCUI → canonical name → re-resolve | definite errors | correctness |
| Stratified manual audit — ~30 per confidence band, hand-labelled | per-band precision with an interval | generalisation beyond the sample |

Intervals come from the evaluation framework's `stats` module, so resolution quality is
reported with the same discipline as everything else. This is the project's central theme
recurring one level down: **measuring something with no ground truth available** — and it
is honest material for the talk.

---

## Schema evolution

`releases.py` loads a vocabulary at a pinned release; absent configuration is a hard
failure (R4.2). `release_diff.py` classifies changes:

| `ChangeKind` | HGNC signal | MONDO signal |
|---|---|---|
| `RENAMED` | stable HGNC ID, changed `symbol`; `prev_symbol` populated | — |
| `WITHDRAWN` | entry withdrawn | — |
| `OBSOLETED_WITH_REPLACEMENT` | — | `owl:deprecated` + `IAO:0100001` |
| `OBSOLETED_ORPHAN` | — | `owl:deprecated`, no replacement |

`OBSOLETED_ORPHAN` is isolated because it is the only category with no automatic path
forward — it requires a human by construction.

Changes produce `REPLACED_BY` edges and `RemediationProposal` nodes. Nothing rewrites a
rule (R4.7). This mirrors v1's own stated reasoning about its hand-written abbreviation
map — *"guessing them with fuzzy matching is exactly the unaudited inference this layer
exists to prevent"* — applied to identifier drift.

The payoff is the demonstration: a rule correct at release *N* returning the wrong verdict
at *N+1*, with no code or rule change. That is the strongest governance slide available, and
it is only possible because releases are pinned and addressable side by side.

---

## Conformance vs verification

```python
@dataclass(frozen=True)
class ConformanceResult:
    conforms: bool
    violations: tuple[ShapeViolation, ...]
    shapes_version: str
    # deliberately carries no field named "valid", "correct" or "true"
```

Field naming is part of the design. SHACL conformance cannot be read as correctness if the
type offers no vocabulary for it (R5.3). Non-conformant records are quarantined with their
violation report rather than dropped (R5.4) — a quarantine table is also a useful dataset
in its own right.

---

## Verification queries (R8)

Published in `queries/` and in the README so a reader can check every count:

```cypher
// tier composition — one query per tier, each independently reproducible
MATCH (n:Provenanced) RETURN n.tier AS tier, count(*) ORDER BY tier;

// every derived edge with its method, evidence and confidence
MATCH ()-[r]->() WHERE r.method IS NOT NULL
RETURN type(r), r.method, r.evidence, r.confidence LIMIT 100;

// resolution confidence distribution — the shape of our uncertainty
MATCH (:Mention)-[r:RESOLVES_TO]->(:ResolvedEntity)
RETURN r.method, count(*), avg(r.confidence) ORDER BY count(*) DESC;

// mentions awaiting review — the honest backlog
MATCH (m:Mention)-[r:RESOLVES_TO]->() WHERE r.review_state = 'NEEDS_REVIEW'
RETURN count(m);

// unresolved contradictions
MATCH (c:Contradiction) WHERE c.resolution IS NULL RETURN count(c);

// what did we know at time T
MATCH (e:ResolvedEntity {entity_id:$id})-[:ASSERTS]->(f:FactVersion)
WHERE f.valid_from <= $t AND (f.valid_to IS NULL OR f.valid_to > $t)
RETURN f;
```

The review backlog and contradiction counts are deliberately published. A graph with a
visible backlog is more credible than one with none.

---

## Testing strategy

- **No database (runs in CI, no `.env`):** normalisation rules; `ChangeKind` classification
  from committed release-pair fixtures; `ResolutionMethod` confidence mapping;
  `FactBundle` assembly and hashing; store-interface introspection asserting no mutating
  verb; `ConformanceResult` has no truth-flavoured field.
- **With database (behind `skip_without_credentials`):** constraint enforcement; supersession
  closes intervals; point-in-time queries; two releases coexisting; purge removes exactly
  the derived tier; rebuild-from-manifest reproduces counts within tolerance.
- **Fixtures:** trimmed HGNC and MONDO release pairs, and deliberately messy FAERS drug
  strings — misspellings, combination products, trailing dosages. Those fixtures are an
  asset, not scaffolding.

---

## Migration from v1

Not a data migration — a re-ingest. v1's authored CSVs become tier `asserted` for the
synthetic patient and policy subset only; everything else is re-derived from real sources
through the resolution pipeline.

`data/nodes/external_mappings.csv` is **retired**, and kept in git history deliberately: 30
rows of authored confidence is the before-picture for the talk, and the most concrete
evidence that the criticism was accepted rather than argued with.
