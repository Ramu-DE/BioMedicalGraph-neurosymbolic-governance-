# Development Roadmap — BioMedical Context Layer v2

The full workflow, from an empty directory to a defensible artefact.

Written to be followed in order. The gates are real: each one exists because skipping it is
how v1 arrived at a graph it could not defend.

---

## The one workflow rule that matters

> **Never let a measurement change and a data change land in the same commit.**

If you improve the evaluation and ingest a new source in one step, you cannot tell which
moved the numbers. That is *exactly* the error the second reviewer identified — a comparison
in which two variables differed and the difference was attributed to one of them.

The project's central finding is about confounded comparisons. The development process has
to embody it, or the talk is hypocritical. In practice:

- A commit changes **code**, **data**, or **measurement** — never two.
- Before any data change, record a manifest. After, re-run and diff against it.
- Any number that moves must be attributable to one commit.

---

## Week 0 — Decide and set up

**Goal: an empty repo that already enforces the rules.**

| Step | Action |
|---|---|
| 0.1 | New repo, public, sensibly named. Not `*_personal`. Not a typo. |
| 0.2 | `docker-compose.yml`: Neo4j 5.x and Qdrant, both pinned tags. |
| 0.3 | `pyproject.toml`, `pytest`, pre-commit. CI runs on every push. |
| 0.4 | **CI gate from commit one:** `pytest` must pass with no `.env`. |
| 0.5 | Copy the four spec folders into `.kiro/specs/`. |
| 0.6 | `docs/CLAIMS.md` — empty, with the four mandatory columns. |
| 0.7 | `.env.example` with every pin: source versions, release ids, seeds, slice bounds. |

**Repo hygiene learned the hard way:** no third-party documents (v1 shipped someone else's
evaluation guide), no `linkedin/` drafts, no scratch notebooks in a repo you cite as
evidence.

**Gate 0:** CI is green on an empty project with the credential-free rule enforced. If that
rule is added later it will already have been broken.

---

## Weeks 1–2 — Component 00: Foundation

**Goal: a fact without an epistemic stamp is unrepresentable.**

Follow [`00-foundation/`](./00-foundation/). Build order: `stamp` → `fact` → `temporal` →
`contradiction` → `trust` → `outcome` → `claims` → `manifest` → `lint`.

This component has no I/O, so it should reach ~150 tests with nothing running. That is the
point: it is the part a reviewer verifies in ten minutes.

**Gate 1 — do not proceed until all six hold:**

1. Constructing an unstamped fact is a type error.
2. Setting confidence without a registered method raises.
3. `is_eligible_rule_input` has a test for every `IneligibilityReason`.
4. A withheld dependency yields `CANNOT_EVALUATE`, never `NO_VIOLATION`.
5. The claims register fails the build on a missing "does not establish".
6. The lint pass fails on a bare rate and on the word "proof".

> **Why alone and first.** The trust boundary and provenance stamp must be unavoidable at
> the type level. Every later component inherits them for free, or works around them
> forever.

---

## Weeks 3–5 — Component 01: Neo4j

**Goal: identity is a modelled object with competing evidence.**

Follow [`01-neo4j-graph/tasks.md`](./01-neo4j-graph/tasks.md). Phases 0–4 are the core;
phase 7 (scale) comes with component 03.

**Highest-risk task: 4.2** — find a real HGNC rename or MONDO obsoletion that intersects an
identifier your rules reference. Do this in week 3, not week 5. Everything in the
schema-evolution story depends on it, and if no intersection exists you need time to add a
rule referencing something you know has moved. Do not manufacture a synthetic rename; that
reproduces the criticism.

**Gate 2:**

1. `Mention` and `ResolvedEntity` are distinct; `CANDIDATE_FOR` holds every candidate.
2. No confidence in the graph came from a caller.
3. The store interface offers no mutate or delete verb (asserted by introspection).
4. Two vocabulary releases coexist and the diff between them is deterministic.
5. A rule cannot obtain an ineligible fact by any code path.
6. Published counts reproduce from the documented queries.

---

## Weeks 5–7 — Component 03: Multi-source ingestion

**Goal: the mess is not ours.**

Follow [`../specs/real-source-ingestion/tasks.md`](../specs/real-source-ingestion/tasks.md),
adapted to write through component 01's resolution pipeline.

Order: **licence pre-flight first** (phase 0), then RxNorm as the resolution target, then
openFDA FAERS as the messy input, then HGNC and MONDO.

Two sources resolved properly beats five ingested shallowly. The objection was about real
messiness, not node count.

**Gate 3:**

1. ≥100k resolved entities, ≥1M relationships, ≥5 sources, ≥20 ingested entity types.
2. Counts reported **per tier and per source**, never as one total.
3. `external_mappings.csv` retired; no authored confidence remains.
4. Resolution evaluated without a gold standard, with the limits stated.
5. No licence-restricted vocabulary is present or ingestible.
6. The real/synthetic boundary is stated publicly and enforced in code.

---

## Week 8 — Component 02: Qdrant

**Goal: retrieval is relevant, subordinate, and identity-filtered.**

Follow [`02-qdrant-vectors/tasks.md`](./02-qdrant-vectors/tasks.md). **Task 0.3 first** —
fact-path isolation. Retrofitting that boundary is how it gets lost, and it is the property
the neurosymbolic claim rests on.

**Gate 4:**

1. A test proves the governance engine cannot reach the vector client.
2. Identity-filtered retrieval excludes a similar-string chunk for a different entity.
3. An unresolved query is marked `identity_grounded=False`, not silently degraded.
4. No collection mixes embedding models or chunking versions.
5. Outage produces a visible degraded result, documented as availability only.

---

## Week 9 — Component 04: Governance engine

**Goal: a rule can only read what it is permitted to read.**

Mostly carried forward from v1 — the declarative YAML rules and the precedence model were
the acknowledged strength. What changes:

- Rules consume a `FactBundle`; they hold no store client.
- `RuleOutcome.CANNOT_EVALUATE` exists and every consumer handles it exhaustively.
- Verdicts enumerate facts relied on **and facts withheld with reasons**.
- `rules/integrity.yaml` blocks on rules citing obsolete identifiers
  ([`../specs/schema-evolution/`](../specs/schema-evolution/)).
- Latency reports `governance_total_ms`.

**Gate 5:**

1. No rule holds a client; no code path reaches an unfiltered query.
2. Every `RuleOutcome` match is exhaustive and `CANNOT_EVALUATE` is never permissive.
3. Verdicts carry tier, confidence, validity and contradiction state per fact.
4. The fail-open demonstration runs: correct at release N, wrong at N+1, no code change.

---

## Weeks 10–11 — Component 05: Evaluation

**Goal: every number bounded by its evidence.**

Follow [`../specs/evaluation-integrity/tasks.md`](../specs/evaluation-integrity/tasks.md).
`stats.py` first — component 01's resolution audit and component 02's retrieval evaluation
both depend on it, so it should actually land in week 3 and be reused throughout.

**Run the four-arm experiment and report what `judge_grounded` scores.** Either outcome is
publishable. Do not tune toward either.

**Gate 6:**

1. Four arms run with parity enforced by content hash.
2. Every rate carries `n` and an interval; no pooled accuracy as a headline.
3. Class balance stated; constant predictors labelled.
4. Failures categorised by arm, including the deterministic arm's.
5. Pre-registered design recorded with a design hash.
6. Groundedness computed over answered questions only.

---

## Week 12 — Component 06: UI

**Goal: hand a reviewer an instrument instead of a description.**

Follow [`03-ui/tasks.md`](./03-ui/tasks.md). Guards before pages. Even the minimum
subset — fixture mode, the visual language, and a decision trace with the withheld-facts
panel — answers all three of reviewer 2's inspection questions.

**Gate 7:** a stranger with no credentials runs one command, opens one decision, and sees
every fact used, every fact withheld and why, which arm saw which evidence, and what each
number does not establish.

---

## Week 13 — Consolidate

| Step | Action |
|---|---|
| 13.1 | Fill `docs/CLAIMS.md` completely. Delete any claim whose "does not establish" column you cannot write. |
| 13.2 | README from manifests only. Every number traces to a run id. |
| 13.3 | Rebuild from pinned sources on a clean machine; diff counts against the manifest. |
| 13.4 | Full suite green with no `.env`, no network, no licence. |
| 13.5 | `docs/LIMITATIONS.md` — the ceiling, named by you first. |
| 13.6 | Ask Takeda regulatory affairs for a specific instrument, **or cut the regulatory claim**. |

---

## Per-task discipline

Each task in a `tasks.md` is one unit of work:

```
1. Read the requirement it cites. If it is unclear, fix the spec before the code.
2. Write the test first — the acceptance criteria are already the test names.
3. Implement.
4. Verify: pytest with no .env.
5. If a published number changed, record the manifest and state why.
6. Commit: "01/2.4 candidate recording — Req 2.3, 2.4". One category of change only.
7. Tick the box in tasks.md.
```

When a requirement turns out wrong, change the spec in the same commit as the code. A spec
that drifts from the code is worse than none — v1's `DATA_PROVENANCE.md` described a scheme
nothing enforced, which is how an excellent document coexisted with an indefensible graph.

---

## Risks, and what to do about them

| Risk | Mitigation |
|---|---|
| **No real identifier change intersects your rules** (blocks the best demo) | Task 01/4.2 in week 3. If none exists, add a rule referencing a gene or disease known to have moved. Never fabricate a rename. |
| **Scale target slips** | Gate 3 is a floor, not a goal. Ingest a narrower slice of more sources rather than a deep slice of one. |
| **Resolution has no gold standard** | This is the finding, not the problem. Budget time for the stratified manual audit — it is the honest evidence. |
| **Foundation feels like overhead in week 1** | It is 150 credential-free tests a reviewer can run in ten minutes. That is the artefact. |
| **Scope creep into breadth** | Two sources properly beats nine shallowly. Reviewer 1 objected to missing *problems*, not missing rows. |
| **Discipline decays by week 8** | The lint pass and the UI guards are the enforcement. If they are bypassed, the project is back to v1. |

---

## If you only have four weeks

A credible subset, in this order:

1. **Component 00 entire** (weeks 1–2). The trust boundary, `CANNOT_EVALUATE`, the claims
   register, the lint pass.
2. **Component 01 minimum** — identity with candidates, method-derived confidence, release
   diffing (week 3).
3. **openFDA → RxNorm resolution** with derived confidence and the no-gold-standard
   evaluation (week 4).
4. **The four-arm experiment** on the existing 12 cases with intervals (week 4).
5. **UI minimum** — fixture mode and a decision trace with the withheld panel (week 4).

That subset answers every one of reviewer 2's points and the entity-resolution half of
reviewer 1's. Scale and breadth can follow; the defensibility cannot.

---

## What "done" means

A reviewer can, in fifteen minutes and with no credentials:

1. Run the test suite.
2. Open a decision and see every fact used and every fact withheld, with reasons.
3. Tell an inference from a curated fact at a glance.
4. See which evaluation arm had access to which evidence.
5. See a governance rule broken by an external identifier change.
6. Read what every published number does not establish.
7. Reproduce any figure from a manifest.

None of those seven were possible before. That gap — not the node count — is what
*"the claims substantially exceed the evidence"* was describing.
