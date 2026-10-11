# Implementation Plan: UI Framework

Ordered by dependency. **[gate]** tasks unblock multiple downstream tasks.

Legend: `[ ]` not started · `[~]` in progress · `[x]` done

**Prerequisites:** components 00, 01, 04, 05. The UI is a projection — it cannot be built
before the model it projects.

**Estimate: 1–2 weeks part-time.** Build last, but do not skip: it is the component that
converts "trust my description" into "here is the instrument."

---

## Phase 0 — Shell and refusals

- [ ] 0.1 `ui/app.py` — NiceGUI app, routing, one documented start command
  - `python -m biomedical.ui`
  - _Requirements: 8.5_

- [ ] 0.2 **[gate]** Fixture-mode detection
  - No credentials → load committed fixtures, show a persistent banner
  - Surface that cannot load its data states why rather than rendering empty
  - _Requirements: 8.1, 8.4_

- [ ] 0.3 **[gate]** `ui/guards.py` — render-time refusals
  - `render_rate()` returns an error panel when `n` or interval is absent
  - Unregistered claim does not render
  - Conformance result cannot be labelled "correct" or "true"
  - _Requirements: 5.2, 7.4_

- [ ] 0.4 `tests/unit/test_guards.py`
  - A bare proportion cannot reach the screen
  - _Requirements: 5.2, 7.4_

> 0.3 before any page. A guard added after the pages exist will have been routed around in
> three places by then.

---

## Phase 1 — The visual language

- [ ] 1.1 **[gate]** `ui/theme.py`
  - Three independent encodings per tier: border style, glyph, label — colour is the fourth
    channel, never the only one
  - Composable overlay states: below-floor, stale, contested, needs-review
  - _Requirements: 2.1, 2.2, 2.6, 2.7_

- [ ] 1.2 `ui/components/fact_chip.py`
  - A fact plus tier, confidence, method, validity interval, contradiction state
  - Staleness and contestation shown inline, not only in a detail panel
  - _Requirements: 1.2, 2.3, 2.4, 2.5_

- [ ] 1.3 `ui/components/outcome_badge.py`
  - `CANNOT_EVALUATE` visually distinct from `NO_VIOLATION`; never rendered as a pass
  - _Requirements: 1.4_

- [ ] 1.4 `ui/components/rate.py` and `interval_chart.py`
  - Rates always with `n` and interval; charts render intervals as marks, not tooltips
  - _Requirements: 5.1, 5.8_

- [ ] 1.5 `tests/unit/test_theme.py`
  - Every tier has a non-colour encoding
  - Tier and overlay states compose
  - **No page overrides the language** — assert by inspection
  - _Requirements: 2.1, 2.6, 2.7_

- [ ] 1.6 `tests/unit/test_outcome_badge.py`
  - _Requirements: 1.4_

---

## Phase 2 — Decision trace

- [ ] 2.1 **[gate]** `ui/pages/decision.py`
  - Question, retrieval results, facts supplied, rules and outcomes, verdict
  - _Requirements: 1.1_

- [ ] 2.2 **Facts-withheld panel** with ineligibility reasons
  - The view v1 had no equivalent of; answers *"how do requests become trusted rule inputs"*
  - _Requirements: 1.3_

- [ ] 2.3 `CANNOT_EVALUATE` with its withheld dependency shown
  - _Requirements: 1.4_

- [ ] 2.4 Decision-time context
  - Trust policy version and every active source version
  - _Requirements: 1.5_

- [ ] 2.5 Timings
  - End-to-end governance total as the headline; any component figure carries its
    qualifying note
  - _Requirements: 1.6_

- [ ] 2.6 Superseded-fact indication with a link to the current version
  - _Requirements: 1.7_

- [ ] 2.7 Snapshot test on the decision trace
  - So the withheld panel cannot be removed silently

- [ ] 2.8 Wire the carried-forward worked example
  - The catalog-vs-policy case: metadata asserts `certified: true`, the governing policy is
    violated, zero DQ rules exist

---

## Phase 3 — Provenance explorer

- [ ] 3.1 `ui/pages/entity.py` + `components/provenance_tree.py`
  - Tier-grouped tree: identifiers by authority (all retained), facts with intervals,
    candidates with scores, contradictions
  - _Requirements: 2.1, 2.2_

- [ ] 3.2 Supersession timeline

- [ ] 3.3 Time-travel control
  - Re-render the entity as of any instant via component 01's point-in-time query
  - _Requirements: 1.7_

---

## Phase 4 — Review queue (the only writing surface)

- [ ] 4.1 **[gate]** `ui/pages/review.py`
  - Prioritised list of mentions needing review with raw string and source record
  - _Requirements: 3.1_

- [ ] 4.2 Candidate panel
  - Every candidate with score, rank and the method that produced it
  - _Requirements: 3.2_

- [ ] 4.3 Accept / reject / unresolvable
  - Writes a superseding decision with reviewer identity and timestamp; never overwrites
  - _Requirements: 3.3, 3.4_

- [ ] 4.4 Immediate eligibility reflection
  - _Requirements: 3.5_

- [ ] 4.5 Queue size pinned in the header, never hidden when non-empty
  - A visible backlog is more credible than a hidden one
  - _Requirements: 3.6_

- [ ] 4.6 Cleared and outstanding counts
  - _Requirements: 3.7_

---

## Phase 5 — Contradiction board

- [ ] 5.1 `ui/pages/conflicts.py`
  - Entity, property, every conflicting value with source and provenance
  - _Requirements: 4.1_

- [ ] 5.2 Resolution display: chosen value, authority cited, who and when
  - _Requirements: 4.2_

- [ ] 5.3 Unresolved → indicate affected facts are ineligible as rule inputs
  - _Requirements: 4.3_

- [ ] 5.4 Resolution requires a cited authority
  - Resolution without one is not permitted by the form
  - _Requirements: 4.4_

- [ ] 5.5 Cross-authority identifier disagreements listed as disagreements
  - MONDO / DOID / MeSH published xrefs disagree; do not present them as settled
  - _Requirements: 4.5_

---

## Phase 6 — Evaluation dashboard

- [ ] 6.1 `ui/pages/evaluation.py` — per-arm cards
  - `gets_graph_facts` and `gets_rubric` as explicit badges
  - This is the confound that sank v1 made visible
  - _Requirements: 5.3_

- [ ] 6.2 Every rate with denominator and interval
  - _Requirements: 5.1_

- [ ] 6.3 Constant-predictor labelling
  - State that accuracy is determined by class balance
  - _Requirements: 5.4_

- [ ] 6.4 Class balance shown beside every result
  - _Requirements: 5.5_

- [ ] 6.5 Pre-registered design and design hash reachable from the same view
  - _Requirements: 5.6_

- [ ] 6.6 Failures by category and by arm
  - **Including failures of the deterministic arm**
  - _Requirements: 5.7_

- [ ] 6.7 Charts render intervals as error bars or bands
  - _Requirements: 5.8_

---

## Phase 7 — Release diff and rule integrity

- [ ] 7.1 `ui/pages/releases.py` — change counts by `ChangeKind`, inspectable
  - _Requirements: 6.1_

- [ ] 7.2 Affected rules flagged prominently
  - _Requirements: 6.2_

- [ ] 7.3 Orphan obsoletions marked as requiring human resolution
  - _Requirements: 6.3_

- [ ] 7.4 Remediation proposals: replacement, evidence, pending status — **no apply button**
  - _Requirements: 6.4_

- [ ] 7.5 Fail-open demonstration view
  - Before/after verdicts, both release identifiers named, explicit statement that no code
    or rule changed
  - _Requirements: 6.5_

---

## Phase 8 — Claims register

- [ ] 8.1 `ui/pages/claims.py`
  - Each claim with evidence link, `n`, category, and what it does not establish
  - _Requirements: 7.1_

- [ ] 8.2 Category separation
  - An `availability` claim cannot appear under a safety or correctness heading
  - _Requirements: 7.2_

- [ ] 8.3 Numbers elsewhere link to their register entry
  - _Requirements: 7.3_

---

## Phase 9 — Fixtures and hardening

- [ ] 9.1 **[gate]** Complete fixture set
  - At least one of each: withheld fact, unresolved contradiction, review-queue item,
    `CANNOT_EVALUATE` verdict, stale fact, release diff with an orphan
  - _Requirements: 8.2_

- [ ] 9.2 `tests/unit/test_fixture_mode.py` — every surface renders from fixtures
  - _Requirements: 8.1, 8.2_

- [ ] 9.3 No secret reaches the client
  - _Requirements: 8.3_

- [ ] 9.4 `docs/UI.md` — the visual language documented once, with the tier encoding table
  - _Requirements: 2.7_

---

## Definition of done

1. One decision can be followed end to end, including every fact withheld and why.
2. A derived fact is distinguishable from a curated one without clicking, and without colour.
3. `CANNOT_EVALUATE` never renders as a pass.
4. A rate without `n` and an interval cannot reach the screen.
5. The review backlog and contradiction count are visible, not hidden.
6. Each evaluation arm's evidence access is shown as a badge.
7. A rule broken by an identifier change is visible with both releases named.
8. Every number links to a claim stating what it does not establish.
9. All of it runs locally from fixtures with no credentials, via one command.
10. `pytest` passes with no `.env`.

**Minimum viable subset: 0.1, 0.2, 0.3, 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 9.1.**
Fixture mode, the visual language, and a decision trace with the withheld-facts panel. That
subset alone answers reviewer 2's three inspection questions and is the strongest artefact
to hand a reviewer at the next submission.
