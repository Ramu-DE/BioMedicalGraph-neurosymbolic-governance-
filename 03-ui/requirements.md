# Requirements: UI Framework — Inspection Surface

## Introduction

The UI's purpose is not demonstration. It is **inspection**: making the system's reasoning,
uncertainty and refusals examinable by someone who did not build it.

This reframing comes directly from the rejection. Reviewer 2 did not ask for a better demo;
they asked a series of questions a demo cannot answer:

> *"Explain how requests become trusted rule inputs, how stale or incorrect graph facts are
> handled, and whether 0.09 ms includes database access."*

Those are inspection questions. v1's three pages (`/ctx/`, `/ctx/eval`, `/ctx/membrane`)
showed the pipeline working. They could not show *why a rule was allowed to read a
particular fact*, *what was withheld from it*, or *which facts were stale* — because the
system did not model those things. Now it does, and the UI's job is to surface them.

A second, harder requirement comes from component 00 R2.7: **a derived fact must not look
like a curated fact.** If the interface renders an inference at confidence 0.3 with the same
visual weight as a pinned external identifier, the interface is undoing the epistemic model
underneath it. Visual hierarchy is a correctness concern here, not an aesthetic one.

### Inherited

Component 00's epistemic model; component 01's identity, temporal and contradiction model;
component 02's retrieval results; components 04 and 05's verdicts and evaluation reports.
The UI adds no model of its own — it is a projection.

### Scope boundaries

| Boundary | Decision |
|---|---|
| Authoring | Read-mostly. The one exception is the resolution review queue, which writes review decisions. |
| Business logic | None. Every number shown is computed upstream; the UI may not derive statistics. |
| Public deployment | Out of scope. Local-first; a hosted demo is optional and must carry no credentials in the client. |
| Mobile | Out of scope. This is an analyst's instrument. |

---

## Requirement 1: Decision Trace — End to End

**User story:** As a reviewer, I want to follow one decision from question to verdict and
see every input it rested on, so that I can judge the reasoning rather than the output.

**Trace:** `[CDL-6a]` `[CDL-5a]`

### Acceptance Criteria

1. WHEN a decision is selected THEN the UI SHALL display the question, the retrieval
   results, the facts supplied, the facts withheld, each rule's outcome, and the final
   verdict.
2. WHEN facts supplied are displayed THEN each SHALL show its tier, confidence, derivation
   method where applicable, validity interval, and contradiction state.
3. WHEN facts withheld are displayed THEN each SHALL show its ineligibility reason.
4. WHEN a rule outcome is displayed THEN `CANNOT_EVALUATE` SHALL be visually distinct from
   `NO_VIOLATION`, and SHALL NOT be rendered as a pass.
5. WHEN a verdict is displayed THEN the trust policy version and all source versions active
   at decision time SHALL be shown.
6. WHEN timings are displayed THEN the end-to-end governance figure SHALL be shown; IF a
   component timing is shown THEN its qualifying note SHALL be shown with it.
7. WHEN a decision rests on a fact that has since been superseded THEN the UI SHALL indicate
   this and link to the current version.

---

## Requirement 2: Epistemic Status Is Visually Encoded

**User story:** As a reader, I want to see at a glance how much to trust what I am looking
at, so that I do not read an inference as a curated fact.

**Trace:** component 00 R2.7 — *"a graph is not automatically ground truth"* made visible.

### Acceptance Criteria

1. WHEN any fact is rendered THEN its tier SHALL be encoded by a persistent visual
   treatment used consistently across every surface.
2. WHEN a `derived` fact is rendered THEN it SHALL be distinguishable from an `ingested` or
   `asserted` fact without the reader hovering, clicking or reading small print.
3. WHEN confidence is below the configured floor THEN the rendering SHALL indicate that the
   fact is ineligible as a rule input.
4. WHEN a fact is stale THEN staleness SHALL be indicated alongside the value, not only in
   a detail panel.
5. WHEN a fact is under unresolved contradiction THEN the contradiction SHALL be indicated
   at the point of display.
6. WHEN tier or confidence is encoded by colour THEN an equivalent non-colour encoding SHALL
   also be present, so the distinction survives colour-blindness and greyscale printing.
7. WHEN the visual language is defined THEN it SHALL be documented once and applied
   uniformly; per-page variations SHALL NOT be permitted.

---

## Requirement 3: Resolution Review Queue

**User story:** As a knowledge engineer, I want to work the queue of uncertain entity
resolutions, so that low-confidence mappings are cleared by a human rather than silently
trusted.

**Trace:** `[CDL-9b]` — this is the surface that makes entity resolution real work.

### Acceptance Criteria

1. WHEN mentions require review THEN the UI SHALL list them ordered by a configurable
   priority, showing the raw mention string and its source record.
2. WHEN a mention is reviewed THEN the UI SHALL display every candidate entity with its
   score, rank and the resolution method that produced it.
3. WHEN a reviewer accepts a candidate THEN the decision SHALL be recorded with reviewer
   identity and timestamp, and the prior resolution SHALL be superseded rather than
   overwritten.
4. WHEN a reviewer rejects all candidates THEN the mention SHALL be recordable as unresolved
   with a reason.
5. WHEN a review decision is recorded THEN the affected mention SHALL become eligible or
   remain ineligible accordingly, and the change SHALL be visible immediately.
6. WHEN the queue is displayed THEN its size SHALL be shown prominently and SHALL NOT be
   hidden when non-empty.
7. WHEN review progress is reported THEN cleared and outstanding counts SHALL both be shown.

> AC 6 is deliberate. A visible backlog is more credible than a hidden one, and concealing
> it would reproduce exactly the impression of tidiness that the rejection punctured.

---

## Requirement 4: Contradiction Board

**User story:** As an analyst, I want to see where sources disagree, so that I can judge
which disagreements matter and record how they were settled.

**Trace:** `[CDL-9c]` `[CDL-6b]`

### Acceptance Criteria

1. WHEN contradictions exist THEN the UI SHALL list them with the entity, the property, and
   every conflicting value with its source and provenance.
2. WHEN a contradiction is resolved THEN the UI SHALL show the chosen value, the authority
   precedence cited, who applied it and when.
3. WHEN a contradiction is unresolved THEN the UI SHALL indicate that affected facts are
   ineligible as rule inputs.
4. WHEN a reviewer resolves a contradiction THEN the resolution SHALL be recorded with a
   cited authority; resolution without an authority SHALL NOT be permitted.
5. WHEN cross-authority identifier disagreements exist THEN they SHALL be listed as
   disagreements rather than presented as resolved.

---

## Requirement 5: Evaluation Dashboard

**User story:** As a reviewer, I want the evaluation results with their uncertainty and
their design, so that I can see what the numbers do and do not support.

**Trace:** `[CDL-2]` `[CDL-3]` `[CDL-10]`

### Acceptance Criteria

1. WHEN an evaluation result is displayed THEN every rate SHALL be shown with its
   denominator and confidence interval.
2. WHEN a rate is displayed without an interval THEN the UI SHALL refuse to render it and
   SHALL show an error, rather than displaying a bare number.
3. WHEN arms are compared THEN the UI SHALL show, per arm, whether it received graph facts
   and whether it received a rubric.
4. WHEN an arm is a constant predictor THEN the UI SHALL label it as such and SHALL state
   that its accuracy is determined by class balance.
5. WHEN a result is displayed THEN the class balance of the evaluation set SHALL be shown
   alongside it.
6. WHEN an evaluation run is displayed THEN its pre-registered design and design hash SHALL
   be reachable from the same view.
7. WHEN failures are displayed THEN they SHALL be shown by category and by arm, including
   failures of the deterministic arm.
8. WHEN a chart is rendered THEN confidence intervals SHALL be visually represented, not
   only present in a tooltip.

---

## Requirement 6: Release Diff and Rule Integrity

**User story:** As a governance owner, I want to see which identifiers moved between
vocabulary releases and which rules they affect, so that silent governance failure becomes
visible.

**Trace:** `[CDL-9d]`

### Acceptance Criteria

1. WHEN two releases are compared THEN the UI SHALL show per-category change counts and
   allow inspection of each changed identifier.
2. WHEN a rule depends on a changed identifier THEN the UI SHALL flag that rule prominently.
3. WHEN an identifier is an orphan obsoletion THEN the UI SHALL indicate that no automatic
   replacement exists and that human resolution is required.
4. WHEN a remediation proposal exists THEN the UI SHALL show the proposed replacement, its
   evidence, and its pending status, and SHALL NOT apply it automatically.
5. WHEN the fail-open demonstration is available THEN the UI SHALL show the before and after
   verdicts with both release identifiers named, and SHALL state that no code or rule
   changed between them.

---

## Requirement 7: Claims Register Surface

**User story:** As a reviewer, I want to read what each published claim does not establish,
so that I can calibrate every number I have just been shown.

**Trace:** `[CDL-2]` `[CDL-4]` `[CDL-7]` `[CDL-8]`

### Acceptance Criteria

1. WHEN the claims register is displayed THEN each claim SHALL show its evidence reference,
   `n`, category, and what it does not establish.
2. WHEN a claim is categorised `availability` THEN the UI SHALL NOT present it under a
   safety or correctness heading.
3. WHEN a number displayed elsewhere corresponds to a registered claim THEN the UI SHALL
   link to that claim's entry.
4. WHEN a claim has no register entry THEN the UI SHALL NOT display it.

---

## Requirement 8: Local-First Operation

**User story:** As a reader reproducing the work, I want to run the interface locally
against fixtures, so that I can inspect the system without credentials.

**Trace:** `[CDL-10]`

### Acceptance Criteria

1. WHEN the UI starts with no credentials THEN it SHALL run against committed fixtures and
   indicate that it is in fixture mode.
2. WHEN in fixture mode THEN every surface SHALL be navigable with representative data,
   including at least one of each: withheld fact, contradiction, review-queue item, and
   `CANNOT_EVALUATE` verdict.
3. WHEN the UI is served THEN no credential or secret SHALL be transmitted to the client.
4. WHEN a surface cannot load its data THEN it SHALL state why rather than rendering empty.
5. WHEN the UI is run THEN a single documented command SHALL start it.
