# Design: UI Framework — Inspection Surface

## Overview

Six surfaces, one visual language, no business logic. The UI is a projection of the
epistemic model — it computes nothing and may not derive a statistic.

| Surface | Route | Answers |
|---|---|---|
| Decision trace | `/decision/{id}` | *"Why this verdict, on what evidence, and what was withheld?"* |
| Provenance explorer | `/entity/{id}` | *"What do we believe about this entity, how strongly, since when?"* |
| Review queue | `/review` | *"Which resolutions need a human?"* |
| Contradiction board | `/conflicts` | *"Where do sources disagree, and how was it settled?"* |
| Evaluation | `/eval` | *"What do the numbers support, and what don't they?"* |
| Release diff | `/releases` | *"What moved, and which rules broke?"* |
| Claims register | `/claims` | *"What does each claim not establish?"* |

v1 had three pages that showed the pipeline working. These seven show the pipeline's
*uncertainty*, which is what the rejection asked for.

---

## Framework decision

**NiceGUI**, carried forward from v1.

| Criterion | NiceGUI | FastAPI + React |
|---|---|---|
| Single language, single process | ✅ | ✗ two toolchains |
| One documented start command (R8.5) | ✅ `python -m biomedical.ui` | ✗ build step, two servers |
| Reproducible by a reviewer with Python only | ✅ | ✗ needs Node |
| Server-side rendering ⇒ no secrets in client (R8.3) | ✅ by default | needs care |
| Continuity with v1 | ✅ ~90 KB of working UI code | ✗ rewrite |
| Rich custom visualisation | adequate — Plotly/ECharts embed | ✅ better |
| Complex client interaction | weaker | ✅ better |

The deciding criterion is R8 — reproducibility. A reviewer who must install Node and run a
build step to inspect the evidence will not inspect the evidence. Everything this UI does is
read-mostly tables, trees and charts, which NiceGUI handles without strain.

**Flip this decision if** the review queue grows into a high-throughput annotation tool. At
that point the interaction complexity justifies React, and the surfaces are decoupled enough
that `/review` could migrate alone.

---

## Module layout

```
biomedical/ui/
├── app.py              routing, fixture-mode detection
├── theme.py            THE VISUAL LANGUAGE — tier/confidence/staleness encoding
├── guards.py           render-time refusals (bare rates, unregistered claims)
├── components/
│   ├── fact_chip.py        a fact + its full epistemic status
│   ├── withheld_list.py    facts withheld + reasons
│   ├── outcome_badge.py    VIOLATION / NO_VIOLATION / CANNOT_EVALUATE
│   ├── rate.py             a rate + n + interval — refuses to render without them
│   ├── interval_chart.py   charts that render intervals as marks, not tooltips
│   └── provenance_tree.py  tier-grouped expansion
└── pages/
    ├── decision.py  entity.py  review.py
    ├── conflicts.py  evaluation.py  releases.py  claims.py
```

### `theme.py` — the visual language as code

Component 00 R2.7 makes visual hierarchy a correctness concern: a derived fact must not look
like a curated one. So the encoding is centralised and mandatory, not a per-page choice
(R2.7).

Each tier carries **three** independent encodings so the distinction survives
colour-blindness, greyscale printing and screenshots in a slide deck (R2.6):

| Tier | Border | Icon/glyph | Label | Weight |
|---|---|---|---|---|
| `asserted` | solid | ● | "authored" | full |
| `ingested` | solid | ◆ | source name + version | full |
| `derived` | **dashed** | ◇ | method + confidence | **reduced** |
| `runtime` | dotted | ○ | "observed" | reduced |

Overlay states, composable with tier:

| State | Encoding |
|---|---|
| below confidence floor | strikethrough value + "ineligible" tag |
| stale | clock glyph + age, inline (R2.4) |
| unresolved contradiction | split-value marker + "contested" tag (R2.5) |
| needs review | pending glyph + queue link |

Colour is the *fourth* channel, never the only one. Chart work should follow the project's
visualisation conventions for palette and accessibility; the requirement here is simply that
no distinction is carried by hue alone.

### `guards.py` — the UI refuses to mislead

```python
def render_rate(value: float, n: int, interval: Interval | None, ...):
    if interval is None or n is None:
        return error_panel("Refusing to render a rate without n and an interval")
```

R5.2 is implemented as a *refusal*, not a convention. A bare proportion cannot reach the
screen. Likewise an unregistered claim (R7.4) and a conformance result described as truth.

This is the same enforcement-over-documentation pattern as component 00's lint pass, applied
at render time. It is the mechanism that keeps the discipline alive after the first month.

---

## Surface designs

### `/decision/{id}` — the surface that answers the rejection

```
┌────────────────────────────────────────────────────────────────────┐
│ Q: "Certify Customer Master so I can publish PAT015's response"    │
│ Verdict: ⛔ BLOCKED      policy v1.0   sources: faers 2026-09 …     │
│ Governance total: 14.2 ms   (facts 14.1 · rules 0.09 — eval only)  │
├────────────────────────────────────────────────────────────────────┤
│ RULES                                                              │
│  ⛔ consent.withdrawn_patient_data        VIOLATION                 │
│  ⛔ governance.hub_table_without_dq_rule  VIOLATION                 │
│  ⚠  safety.ae_disclosure                  CANNOT_EVALUATE           │
│       └ dependency withheld: STALE (faers fact, 94d > 90d)          │
├────────────────────────────────────────────────────────────────────┤
│ FACTS RELIED ON                                                     │
│  ◆ consent_status = Withdrawn   ingested · POL001 · valid 2026-03→  │
│  ◆ dq_rule_count = 0            ingested · catalog · valid 2026-08→  │
├────────────────────────────────────────────────────────────────────┤
│ FACTS WITHHELD                                    ← v1 had no such view
│  ◇ ae_severity = Severe   derived 0.6 · STALE · ineligible          │
│  ◇ drug↔RXCUI mapping     derived 0.3 · AWAITING_REVIEW             │
└────────────────────────────────────────────────────────────────────┘
```

Three things here exist only because of the rejection: the **facts-withheld** panel (answers
*"how do requests become trusted rule inputs"*), the **`CANNOT_EVALUATE`** outcome with its
withheld dependency (the fail-open fix), and the **governance total** with the component
figure carrying its qualifying note (answers *"does 0.09 ms include database access"*).

The worked example is the catalog-vs-policy case carried forward from v1 — metadata
asserting certification while the governing policy is violated. It survives because it was
never the weak part.

### `/entity/{id}` — provenance explorer

Tier-grouped tree: identifiers by authority (all retained, none overwritten), facts with
validity intervals, a timeline of supersessions, candidate resolutions with scores, and
contradictions. A time-travel control re-renders the entity as of any instant, reading
component 01's point-in-time query (R1.7).

### `/review` — the working queue

Left: prioritised mentions. Right: the raw string, its source record, and every candidate
with score, rank and method. Accept / reject / unresolvable, each writing a superseding
decision with reviewer identity (R3.3). Queue size is pinned in the header and never hidden
(R3.6).

This is the only writing surface. It is also the one that makes entity resolution visibly
*work* rather than visibly *claimed*.

### `/eval` — evaluation dashboard

Per-arm cards showing `gets_graph_facts` and `gets_rubric` as explicit badges (R5.3) —
because the confound that sank v1 was precisely that these differed between arms and nobody
could see it. Constant predictors carry a label stating their accuracy is determined by class
balance (R5.4). Class balance is shown beside every result (R5.5). Charts render intervals
as error bars or bands, never tooltip-only (R5.8). The pre-registered design and design hash
are one click away (R5.6).

### `/releases` — release diff

Change counts by `ChangeKind`; orphan obsoletions flagged as requiring a human; affected
rules listed prominently; remediation proposals shown as pending with their evidence and no
apply button (R6.4). The fail-open demonstration renders the before/after verdicts with both
release identifiers and the statement that nothing in the code or rules changed (R6.5).

### `/claims` — the register

Every claim with `n`, evidence link, category and *what it does not establish*. Categories
are visually separated so an `availability` claim cannot sit under a safety heading (R7.2).
Numbers elsewhere in the UI link here (R7.3).

---

## Fixture mode (R8)

No credentials ⇒ the app loads committed fixtures and shows a persistent "fixture mode"
banner. Fixtures must include at least one of each: withheld fact, unresolved contradiction,
review-queue item, `CANNOT_EVALUATE` verdict, stale fact, and a release diff with an orphan
(R8.2).

This is how a reviewer inspects the system in five minutes with no accounts, and it is the
single highest-leverage thing in this component for the next submission.

---

## Testing strategy

Credential-free:

- `test_guards.py` — **the critical suite.** A rate without an interval produces an error
  panel, not a number. An unregistered claim does not render. A conformance result cannot be
  labelled "correct".
- `test_theme.py` — every tier has a non-colour encoding; tier and overlay states compose;
  no page overrides the language.
- `test_outcome_badge.py` — `CANNOT_EVALUATE` renders distinctly from `NO_VIOLATION` and
  never as a pass.
- `test_fixture_mode.py` — every surface renders from fixtures; each required fixture case
  is present.
- Snapshot tests on the decision trace so the withheld panel cannot be removed silently.

---

## What a reviewer can do after this component

1. Open one decision and see every fact it used, every fact it was denied, and why.
2. Tell an inference from a curated fact without clicking anything.
3. See the review backlog and the contradiction count, undisguised.
4. See which evaluation arm had access to which evidence.
5. See a rule that broke because an identifier moved, with both releases named.
6. Read what every published number does not establish.
7. Do all of it locally, from fixtures, with no credentials and no licence.

Item 7 is why this component exists. The previous submission asked reviewers to trust a
description; this one hands them an instrument.
