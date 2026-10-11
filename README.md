# BioMedical Context Layer

Neurosymbolic governance over a multi-source biomedical knowledge graph, with an
evaluation framework built to **bound its own claims**.

Every fact carries its epistemic status — where it came from, how confident we
are, when it was true, what contradicts it, and whether a rule is permitted to
read it at all. A governance rule that cannot evaluate says so, rather than
reporting "no violation found".

```bash
git clone https://github.com/Ramu-DE/BioMedicalGraph-neurosymbolic-governance-.git
cd BioMedicalGraph-neurosymbolic-governance-
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest          # 197 tests, no credentials, no network
```

That is the whole setup, and it is the claim most worth checking: **a fresh clone
with no `.env` runs 197 tests and launches the UI.** Verified by cloning this
repository into an empty directory and running exactly those four lines.

Notebooks `02` and `05` also execute with no credentials. The other seven call
`config.require(...)` and tell you precisely which variables are missing rather
than failing with a driver error.

---

## Why this exists

An earlier version of this work was reviewed at a conference and rejected at
2.67/5. Two reviewers found different problems, and both were right:

> *"545 nodes, 6 entity types and a small synthetic ontology is a teaching toy
> rather than knowledge engineering. No entity resolution, no heterogeneous
> sources, no schema evolution."*

> *"The claims substantially exceed the evidence. A graph is not automatically
> ground truth, and SHACL checks constraints, not factual correctness... Explain
> how requests become trusted rule inputs, how stale or incorrect graph facts are
> handled, and whether 0.09 ms includes database access."*

This rebuild treats both critiques as **architectural requirements** rather than
retrofits. Several of the numbers below are *worse* than the originals. That is
the deliverable.

---

## What the evidence says

### The claim that got withdrawn

The original headline was that deterministic rules beat LLM-as-judge. A reviewer
pointed out the comparison was confounded: the rules read the knowledge graph and
the judge did not, so the measured variable was **evidence access**, not method.

So a third arm was added — the same judge, same prompt, given exactly the facts
the rules receive. Parity is structural: one `FactSet` is materialised per case
and content-hashed, and the run aborts if two arms would see different evidence.

| Arm | facts | rubric | TPR | TNR | κ | κ 95% CI |
|---|---|---|---|---|---|---|
| deterministic | Y | N | 0.857 | 1.000 | 1.000 | [1.000, 1.000] |
| judge_blind | N | N | 0.857 | **0.400** | **0.273** | **[−0.286, 0.800]** |
| **judge_grounded** | **Y** | N | 0.857 | **1.000** | **0.833** | [0.437, 1.000] |
| judge_rubric | N | Y | 1.000 | 1.000 | 1.000 | — *(circularity control)* |

n=12, 7 answers wrong / 5 correct — an adversarial stress-test balance, stated up
front and **not** a prevalence estimate. Judge: `us.anthropic.claude-sonnet-4-6`.

**Giving the judge the same facts moved κ from 0.273 to 0.833.** The rules-vs-LLM
gap closed almost entirely. The reviewer was right; the original claim does not
survive the missing control. What does survive is narrower and more useful: *an
evaluator with no access to authoritative facts has poor specificity.*

The blind arm's κ interval includes zero, so even its 0.273 is not
distinguishable from no skill at this n. Both facts are in the claims register.

### What the latency actually is

The original work published **0.09 ms** as the cost of producing a verdict. A
reviewer asked whether that included database access. It did not:

```
governance total: 389.46 ms   (facts 389.41 ms · rules 0.05 ms
                               — rule evaluation over already-materialised facts,
                                 excludes the graph round-trip)
```

Roughly four orders of magnitude. `Timings` now makes the component figure
un-publishable without its qualifying note.

---

## The graph

**6,854 nodes · 8,034 relationships · 4 live open sources · 7 therapeutic areas**

| Source | API | Key needed |
|---|---|---|
| ClinicalTrials.gov v2 | REST | no |
| openFDA FAERS | REST | optional (raises 1k→120k/day) |
| RxNav / RxNorm | REST | no |
| EBI OLS4 (MONDO) | REST | no |
| HGNC | REST | no |
| ChEMBL | REST | no |

Licence-restricted vocabularies — MedDRA, SNOMED CT, UMLS, DrugBank — are
**excluded by design**, because a reader who must sign a licence cannot reproduce
the work.

Patients, consent status and internal policies remain **synthetic**. Drug, trial,
adverse-event, gene and disease facts are real. The boundary is enforced in code
and stated on the UI.

### Entity resolution, with derived confidence

No confidence value in this system is supplied by a caller. `Confidence` cannot
be constructed from a bare float — it is a property of a registered
`ResolutionMethod`, and a lint rule fails the build on any literal.

| Method | n | Confidence |
|---|---:|---:|
| `exact_identifier` | 196 | 1.00 |
| `exact_synonym_match` | 106 | 0.90 |
| `normalised_exact` | 56 | 0.95 |
| `single_fuzzy_above_threshold` | 55 | 0.60 → review |
| `multiple_candidates` | 140 | 0.30 → review |

**555 resolutions · 178 need review · 18 unlinked.** The backlog is published
deliberately; a visible one is more credible than none.

Collapsing surface forms onto canonical identifiers: **13 forms** of type 2
diabetes onto `MONDO:0005148`, **19 forms** of non-small cell lung carcinoma onto
`MONDO:0005233`. Raw strings are never discarded — each becomes a `:Mention`.

→ [`ENTITY_RESOLUTION.md`](./ENTITY_RESOLUTION.md) documents the cascade, the
normalisation rules, **three defects a manual audit found in my own code**, and
how a matcher is evaluated when no gold standard exists.

### Schema evolution, demonstrated rather than asserted

Real HGNC renames (`CDKN2 → CDKN2A`, `WS2A → MITF`, `PBT → KIT`, …) with the
governance consequence wired end to end:

```
RUN 1  release 'historical'           VIOLATION        -> BLOCKED   correct
RUN 2  release 'current', unchecked   NO_VIOLATION     -> ALLOWED   *** FAIL-OPEN ***
RUN 3  release 'current', checked     CANNOT_EVALUATE  -> BLOCKED   correct
```

Same rule, same code, same facts. Only the vocabulary release differs. A rule
authored **2016-04-02** against `CDKN2` silently stopped protecting anything.

The defect is not the rename — it is that *"found no violation"* and *"could not
evaluate"* returned the same value. `RuleOutcome.CANNOT_EVALUATE` separates them.
`scripts/check_rule_identifiers.py --offline` is a CI gate that exits non-zero.

### Cross-source contradictions

**37 conflicts detected; 0 auto-resolved; all 37 routed to a human.**

| Drug | ClinicalTrials.gov | openFDA FAERS |
|---|---|---|
| dacarbazine | investigated **FOR** cutaneous melanoma | co-reported **WITH** "Malignant melanoma" (n=7) |
| Actos | investigated **FOR** type 2 diabetes | co-reported **WITH** "Type 2 diabetes mellitus" (n=4) |

Read naively the FAERS edge says the drug caused the disease. But dacarbazine
*is* a melanoma chemotherapy — the disease is in the reports because the patients
have it. **Confounding by indication**, in real data.

`ConflictKind.INDICATION_VS_ADVERSE_EVENT.auto_resolvable` is deliberately
`False`: adjudicating needs clinical judgment, not a ranking of sources.
Precedence would produce a confident wrong answer.

---

## The trust boundary

The reviewer asked *"how do requests become trusted rule inputs?"* — a question
the previous architecture had no answer to. Now every fact must pass an explicit,
versioned predicate before a rule may read it:

```
rule asks for fact
        ▼
is_eligible_rule_input(fact, policy, now)
    ┌───┴───┐
eligible   ineligible
    ▼         ▼
 rule      reason recorded; fact withheld
 reads     rule returns CANNOT_EVALUATE  (never NO_VIOLATION)
```

Seven ineligibility reasons: tier too weak, confidence below floor, stale,
unresolved contradiction, awaiting review, review rejected, not valid now.
Staleness is **per source** — an adverse-event report and a gene symbol
assignment do not age at the same rate.

Rules hold no store client, so there is no code path from a rule to an
unfiltered query. A test asserts it.

---

## Architecture

```
06  UI — inspection surface (8 pages, pure Python)
05  EVALUATION — four arms, enforced parity, intervals on every rate
04  GOVERNANCE — declarative rules, trust boundary, CANNOT_EVALUATE
01  NEO4J — identity, resolution, bitemporal     02  QDRANT — payload-filtered
03  INGESTION — 6 open sources, derived confidence, conflict detection
00  FOUNDATION — epistemic model, trust boundary, claims register, lint
```

Component 00 has no I/O and no dependencies. Everything above inherits from it,
so a fact without provenance is **unrepresentable** rather than discouraged.

---

## Dependencies

Core — everything except the UI and the LLM judge:

```
neo4j>=5.20          knowledge graph driver
qdrant-client>=1.9   vector store (payload filtering is the feature that matters)
pyyaml>=6.0          declarative rule packs
```

Optional:

```
boto3>=1.34          Bedrock: LLM judge arms + Titan embeddings
nicegui>=2.0         the inspection UI — pure Python, no Node, no build step
pytest>=8.0          tests
```

Python ≥3.11 (uses PEP 695 generics). Install with
`pip install -r requirements.txt`, or `pip install -e ".[all]"`.

**The full test suite needs none of them beyond core, no credentials and no
network.** That is a hard invariant, verified in CI.

---

## Running it

```bash
python config.py        # redacted readiness report — no secret, no instance id
python -m pytest        # 197 tests, credential-free, no network
python -m biomedical.ui.plain --export site   # static HTML — no server, no port
python -m biomedical.ui.plain  # inspection UI, zero JavaScript, http://localhost:8098
python -m biomedical.ui        # same surfaces via NiceGUI,     http://localhost:8099
```

Everything else is an **executed notebook with its outputs saved** — open one and
the results are already there, no credentials needed to read it:

| Notebook | Needs |
|---|---|
| [`02_foundation_gate`](notebooks/02_foundation_gate.ipynb) — trust boundary, `CANNOT_EVALUATE`, claims lint | **nothing** |
| [`05_governance_decision_trace`](notebooks/05_governance_decision_trace.ipynb) — one decision, facts withheld and why | nothing |
| [`09_four_arm_evaluation`](notebooks/09_four_arm_evaluation.ipynb) — the claim that got withdrawn | Bedrock |
| [`04_entity_resolution`](notebooks/04_entity_resolution.ipynb) — derived confidence, three defects found by audit | Neo4j |
| [`07_schema_evolution_fail_open`](notebooks/07_schema_evolution_fail_open.ipynb) — a rule that silently stops working | Neo4j |
| [`08_conflict_detection`](notebooks/08_conflict_detection.ipynb) — confounding by indication | Neo4j |

→ [`notebooks/README.md`](notebooks/README.md) for all nine, with runtimes.

`02` needs no credentials at all. It is the one to read first.

Use `ui.plain` if your browser or proxy shows *"Your browser does not support ES
modules"* — it is server-rendered with no JavaScript, no websocket and no external
assets, and needs no optional dependency.

→ [`UI.md`](./UI.md) · [`GRAPH.md`](./GRAPH.md) ·
[`FINDINGS.md`](./FINDINGS.md) · [`STATUS.md`](./STATUS.md)

---

## Honest gaps

Stated here rather than discovered by a reviewer:

- **No ontology or SHACL in this build.** The previous version had 8 OWL/TTL
  modules and 10 SHACL shapes; they were not ported. Structural conformance
  checking does not currently exist.
- **Scale is ~7k nodes**, against the 100k-entity floor in the spec. The route
  there is more condition slices, not more code — the pipeline is idempotent.
- **MONDO is a disease ontology; many trial conditions are phenotypes.**
  `Bradycardia` and `Albuminuria` belong in HPO. No threshold fixes that; it
  needs a second authority, which is not built.
- **Trials and genes are not resolved.** NCT IDs are already identifiers; gene
  symbols would resolve against HGNC, which is already ingested.
- **FAERS co-report edges are not causal evidence.** No denominator, no controls,
  heavy reporting bias. Registered under `safety` with an explicit statement that
  they do not establish harm.
- **The two vocabulary "releases" are reconstructed from HGNC `prev_symbol`** —
  real, authoritative data, but not a diff of two dated release files, which
  would additionally establish *when* each change landed.

Every public claim has a row in the claims register recording what it does **not**
establish. A claim whose limits cannot be stated is rejected at build time.

## Licence

MIT. Synthetic patient data only — no PHI, and none of this is clinical advice.
