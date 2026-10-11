# Requirements: Foundation — Epistemic Model

## Introduction

This component defines, once, the model that every other component inherits: what a fact
is, where it came from, how confident we are, when it was true, what contradicts it, and
whether a rule is permitted to read it.

It exists because the previous implementation had no answer to two reviewer questions that
are not reporting problems but **architectural gaps**:

> *"Explain how requests become trusted rule inputs, how stale or incorrect graph facts are
> handled."*

> *"A graph is not automatically ground truth, and SHACL checks constraints, not factual
> correctness."*

In v1, a rule read whatever the graph contained. There was no notion of a fact being
*ineligible* as a rule input — no trust boundary — and no notion of a fact being stale or
contested. SHACL validation was described as establishing what is "structurally true",
which conflated conformance with correctness.

This component makes those distinctions unavoidable at the type level. A fact without an
epistemic stamp must be unrepresentable, not merely discouraged.

### Design principle

**Enforcement over documentation.** v1's `docs/DATA_PROVENANCE.md` was an excellent
document describing a scheme that nothing enforced. Here, the same scheme is a schema, a
type signature, and a lint rule. Documentation describes what the code already guarantees.

### Scope boundaries

| Boundary | Decision |
|---|---|
| Domain logic | None. This component defines the epistemic model, not biomedical semantics. |
| Storage | Storage-agnostic. Neo4j and Qdrant bindings live in components 01 and 02. |
| Trust decisions | The *mechanism* of the trust boundary is here. The *policy* configured into it is component 04. |
| Credentials | None. This component is pure model and must be fully testable with no `.env`. |

---

## Requirement 1: Universal Provenance Stamp

**User story:** As a reviewer, I want every fact in the system to carry its origin, so that
no assertion can be examined without also seeing where it came from.

**Trace:** `[CDL-5a]` `[CDL-9b]`

### Acceptance Criteria

1. WHEN any fact is created THEN it SHALL carry a `ProvenanceStamp` containing `source`,
   `source_version`, `retrieved_at`, `tier`, and `confidence`.
2. WHEN a fact is derived by inference THEN its stamp SHALL additionally carry `method` and
   `evidence`.
3. WHEN a fact-creating function is defined THEN the stamp SHALL be a **required
   parameter**, such that constructing an unstamped fact is a type error rather than a
   runtime failure.
4. WHEN `confidence` is set THEN it SHALL be derived from a named `method`; IF no method can
   be named THEN the value SHALL NOT be set.
5. WHEN a stamp is created THEN `source_version` SHALL be explicit; IF the caller did not
   supply one THEN creation SHALL fail rather than default to "latest".
6. WHEN the system is linted THEN any literal confidence value assigned outside a
   registered resolution method SHALL fail the build.

> AC 4 and 6 exist because v1's `external_mappings.csv` carried 30 hand-asserted
> confidences (`1.0` for RxNorm, `0.95` for GS1) with no derivation. That must be
> structurally impossible here.

---

## Requirement 2: Four-Tier Epistemic Classification

**User story:** As a reviewer, I want to distinguish a curated fact from an inferred one at
a glance, so that an inference can never be mistaken for ground truth.

**Trace:** `[CDL-5a]`

### Acceptance Criteria

1. WHEN a fact is stamped THEN its `tier` SHALL be exactly one of: `asserted`, `ingested`,
   `derived`, `runtime`.
2. WHEN tier is `asserted` THEN the fact SHALL be authored by a human and identified as
   such, including all synthetic patient and policy data.
3. WHEN tier is `ingested` THEN the fact SHALL originate from an external source at a
   pinned version.
4. WHEN tier is `derived` THEN the fact SHALL carry `method` and `evidence` and SHALL be
   reversible by a purge operation that removes it without affecting other tiers.
5. WHEN tier is `runtime` THEN the fact SHALL be a record of system behaviour — a decision,
   action, or outcome — and SHALL be namespace-isolated from knowledge.
6. WHEN tier counts are queried THEN each tier SHALL be independently countable by a single
   query, so that published figures are verifiable by a reader.
7. WHEN a fact's tier is `derived` or lower-confidence THEN no component SHALL present it
   with the same visual or textual weight as an `asserted` or `ingested` fact.

---

## Requirement 3: Trust Boundary on Rule Inputs

**User story:** As a governance owner, I want an explicit, testable predicate controlling
which facts a rule may read, so that a verdict can never rest on evidence we would not
defend.

**Trace:** `[CDL-6a]` — *"Explain how requests become trusted rule inputs."* **This is the
requirement v1 had no answer to, and it is the most important one in this component.**

### Acceptance Criteria

1. WHEN a rule requests a fact THEN the fact SHALL pass an `is_eligible_rule_input`
   predicate before being supplied.
2. WHEN the eligibility predicate is evaluated THEN it SHALL consider at minimum: tier,
   confidence against a configured floor, staleness against the source's policy,
   contradiction state, and review state.
3. WHEN a fact fails eligibility THEN the rule SHALL NOT receive it, and the attempt SHALL
   be recorded with the reason.
4. WHEN a rule cannot obtain a required fact THEN the rule SHALL return `CANNOT_EVALUATE`
   and SHALL NOT return "no violation found".
5. WHEN a verdict is produced THEN it SHALL enumerate every fact relied upon with that
   fact's tier, confidence, validity window and contradiction state.
6. WHEN eligibility policy changes THEN prior verdicts SHALL remain interpretable under the
   policy that was active when they were made.
7. WHEN a fact is marked `needs_review` THEN it SHALL be ineligible until review is
   recorded, regardless of its confidence.
8. WHEN the boundary is tested THEN there SHALL be a test asserting that a rule **cannot**
   obtain an ineligible fact by any available code path.

> AC 4 is the fail-open fix. Conflating "evaluated and found nothing" with "could not
> evaluate" is the single most dangerous behaviour in a governance system, and v1 did not
> distinguish them.

---

## Requirement 4: Bitemporal Validity and Staleness

**User story:** As an auditor, I want to know when a fact was true and when we learned it,
so that a past decision remains explicable under the facts available at the time.

**Trace:** `[CDL-6b]` — *"how stale or incorrect graph facts are handled"*

### Acceptance Criteria

1. WHEN a fact is stored THEN it SHALL carry both a validity interval (`valid_from`,
   `valid_to`) and a knowledge timestamp (`recorded_at`).
2. WHEN a fact is superseded THEN the prior version's `valid_to` SHALL be closed and a new
   version written; no in-place update or delete verb SHALL exist on the store interface.
3. WHEN a fact's age exceeds its source's configured staleness threshold THEN it SHALL be
   marked stale and SHALL become ineligible as a rule input per Requirement 3.
4. WHEN a staleness threshold is configured THEN it SHALL be per-source, because adverse
   event reports and gene symbol assignments do not age at the same rate.
5. WHEN a historical decision is inspected THEN the system SHALL report the fact versions
   and source versions that were active at decision time.
6. WHEN a decision rests on a fact that has since been superseded THEN that decision SHALL
   be identifiable as such by query.
7. WHEN a fact is corrected THEN both the incorrect and corrected versions SHALL remain
   retrievable with their intervals.

---

## Requirement 5: Contradiction as First-Class State

**User story:** As an analyst, I want contradictions between sources represented in the
data rather than resolved silently, so that a decision made on contested evidence is
visible as such.

**Trace:** `[CDL-9c]` `[CDL-6b]`

### Acceptance Criteria

1. WHEN two facts assert incompatible values for the same property of the same resolved
   entity THEN the system SHALL record a contradiction referencing both facts and both
   provenances.
2. WHEN a contradiction exists THEN both facts SHALL remain stored; neither SHALL be
   deleted or overwritten.
3. WHEN a contradiction is resolved THEN the resolution SHALL cite a declared authority
   precedence and SHALL record who or what applied it.
4. WHEN a fact is under unresolved contradiction THEN its eligibility as a rule input SHALL
   be determined by configured policy, and the default SHALL be ineligible.
5. WHEN a verdict relies on a fact that was under contradiction THEN the verdict SHALL
   record that state.
6. WHEN contradictions are reported THEN unresolved ones SHALL be listed explicitly rather
   than omitted from the count.

---

## Requirement 6: Structural Validation Separated From Factual Verification

**User story:** As a reviewer, I want conformance checking and factual checking to be
distinct stages with distinct outputs, so that neither is mistaken for the other.

**Trace:** `[CDL-5b]` — *"SHACL checks constraints, not factual correctness"*

### Acceptance Criteria

1. WHEN the system validates data THEN structural validation (shape conformance) and
   factual verification (agreement with a source) SHALL be separate named stages.
2. WHEN structural validation passes THEN the result SHALL be reported as conformance only,
   and SHALL NOT be described as establishing truth or correctness.
3. WHEN factual verification occurs THEN it SHALL name the external source checked against
   and the date of the check.
4. WHEN no factual verification is possible for a fact THEN the fact SHALL be marked
   unverified rather than assumed correct.
5. WHEN documentation describes validation THEN it SHALL state explicitly that shape
   conformance does not establish factual correctness.

---

## Requirement 7: Claims Register

**User story:** As a reviewer, I want every public claim bound to its evidence and its
limits, so that I can check a claim without reconstructing the experiment.

**Trace:** `[CDL-2]` `[CDL-4]` `[CDL-7]` `[CDL-8]`

### Acceptance Criteria

1. WHEN a claim appears in any public artefact — README, paper, talk, post — THEN a claims
   register entry SHALL exist recording: claim, evidence reference, `n`,
   **what it does not establish**, and claim category.
2. IF the "does not establish" field is empty THEN the claim SHALL be rejected at build
   time rather than published.
3. WHEN a claim is categorised THEN the category SHALL distinguish at minimum
   `availability`, `safety`, `accuracy`, and `robustness`, so that an availability result
   cannot be presented as a safety result.
4. WHEN a regulatory basis is asserted THEN the entry SHALL cite a specific named
   instrument; IF none can be cited THEN the assertion SHALL be removed.
5. WHEN public text is linted THEN the words "proof", "proves" and "adversarial-proof"
   SHALL fail the build when applied to an empirical result.
6. WHEN a rate is published THEN the register SHALL confirm an `n` and an interval are
   present; a bare proportion SHALL fail the build.

> AC 3 exists because v1 claimed *"when the LLM went dark, all rules still fired. Fails
> closed, not open"* — an availability observation presented as a safety property.

---

## Requirement 8: Reproducibility Contract

**User story:** As a reader, I want to reproduce any published number from the repository
alone, so that I can check the work rather than trust it.

**Trace:** `[CDL-10]` — *"the evaluation made reproducible"*

### Acceptance Criteria

1. WHEN any external input is used THEN its version SHALL be pinned in configuration and
   recorded in the run manifest.
2. WHEN a run produces results THEN the manifest SHALL record every pinned version, the
   slice parameters, the random seeds, and a design hash.
3. WHEN a published figure is cited THEN it SHALL be traceable to a manifest.
4. WHEN the test suite runs THEN it SHALL pass with no `.env` present and no network access.
5. WHEN a source adapter is tested THEN it SHALL run against committed fixtures.
6. WHEN a stochastic procedure runs THEN its seed SHALL be recorded and the procedure SHALL
   be deterministic given that seed.
7. WHEN a reader follows the documented setup THEN they SHALL be able to reproduce the
   evaluation without obtaining any licence.

---

## Requirement 9: Real/Synthetic Boundary

**User story:** As a reader, I want to know exactly which parts of the data are real, so
that I do not over-generalise from a synthetic demonstration.

**Trace:** `[CDL-9a]`, ethical and licensing constraints

### Acceptance Criteria

1. WHEN patient-related facts are created THEN they SHALL be tier `asserted` and
   synthetic; the system SHALL reject any real patient-level record.
2. WHEN the boundary is documented THEN it SHALL name which entity types are real and which
   are authored, as a table rather than in prose.
3. WHEN a licensed vocabulary is configured as a source THEN a pre-flight check SHALL fail
   the run.
4. WHEN results are presented THEN any finding resting on synthetic data SHALL be labelled
   as such.
5. WHEN the graph is described publicly THEN the description SHALL state the slice ingested
   rather than implying full source coverage.
