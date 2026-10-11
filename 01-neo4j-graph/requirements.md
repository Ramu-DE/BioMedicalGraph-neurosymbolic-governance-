# Requirements: Neo4j Knowledge Graph

## Introduction

The graph layer. It holds identity, facts, their epistemic status over time, and the
ontology that gives them meaning. It is the component the first reviewer dismissed:

> *"545 nodes, 6 entity types and a small synthetic ontology is a teaching toy rather than
> knowledge engineering. No entity resolution, no heterogeneous sources, no schema
> evolution, none of the problems a CDL masterclass audience actually faces."*

Three of those four objections are properties of this component. The fourth
(heterogeneous sources) is component 03, which writes through this one.

The response is not simply "more nodes." It is that **identity becomes a modelled object**.
In v1 a drug was a row with an authored `RxNorm` mapping at confidence `1.0`. Here, a drug
is a resolved entity with competing candidate identifiers from multiple authorities, a
derived confidence per mapping, a validity interval, and a review state. That change is
what separates a knowledge graph from a spreadsheet with edges.

### Inherited

Everything in [`../00-foundation/`](../00-foundation/). `Fact`, `ProvenanceStamp`, `Tier`,
`ValidityInterval`, `Contradiction` and the trust boundary are not redefined here — this
component binds them to Neo4j.

### Carried forward from v1

The 8 modular OWL/TTL ontologies and 10 SHACL shapes. They were never the weak part and
rebuilding them would be waste.

### Scope boundaries

| Boundary | Decision |
|---|---|
| Source adapters | Component 03. This component defines the write contract they must satisfy. |
| Embeddings and retrieval | Component 02. The graph stores identity; it does not store vectors. |
| Rule evaluation | Component 04. This component supplies facts through the trust boundary. |
| Ontology authoring | The 8 TTL modules are maintained, not rewritten. External vocabularies are ingested, never authored. |
| Hosting | Self-hosted Neo4j 5.x via Docker in development. Aura Free's ~200k node cap cannot hold a credible multi-source graph. |

---

## Requirement 1: Scale and Heterogeneity

**User story:** As a reviewer, I want the graph to be large enough and varied enough that
its problems are real problems, so that lessons drawn from it transfer.

**Trace:** `[CDL-9a]` `[CDL-9c]`

### Acceptance Criteria

1. WHEN the graph is populated THEN it SHALL contain at least **100,000 resolved entities**
   and at least **1,000,000 relationships**.
2. WHEN sources are counted THEN at least **five** independent external sources SHALL
   contribute facts.
3. WHEN entity types are counted THEN the graph SHALL carry at least **20** distinct
   ingested entity types, excluding runtime and authored types.
4. WHEN scale is reported THEN counts SHALL be reported **per tier** and per source, not as
   a single total, so that a reader can see how much is real.
5. WHEN the graph is described publicly THEN the description SHALL state the ingested slice
   rather than implying full coverage of any source.
6. IF the deployment target cannot hold the required scale THEN the deployment SHALL fail
   at configuration time with the projected counts, rather than silently truncating.

---

## Requirement 2: Identity as a Modelled Object

**User story:** As a knowledge engineer, I want entity identity to be an explicit object
with competing evidence, so that resolution decisions are inspectable and revisable.

**Trace:** `[CDL-9b]` — the core of this component.

### Acceptance Criteria

1. WHEN an external record is ingested THEN it SHALL be stored as a **mention** distinct
   from the **resolved entity** it is linked to.
2. WHEN a mention is linked to a resolved entity THEN the link SHALL carry the resolution
   `method`, its `evidence`, and a `Confidence` derived from that method.
3. WHEN multiple candidate entities match a mention THEN all candidates SHALL be recorded
   with their scores; the system SHALL NOT silently select one.
4. WHEN a resolution is uncertain THEN the mention SHALL be marked `needs_review` and SHALL
   be ineligible as a rule input until cleared.
5. WHEN a resolved entity carries identifiers from multiple authorities THEN all SHALL be
   retained with their source and confidence; no authority SHALL overwrite another.
6. WHEN a resolution decision is revised THEN the prior decision SHALL be superseded rather
   than deleted, with its validity interval closed.
7. WHEN resolution quality is reported THEN the report SHALL state how it was evaluated
   **without a gold standard**, and what that evaluation cannot establish.
8. WHEN any identifier mapping exists in the graph THEN its confidence SHALL be derived
   from a registered method; a hand-assigned confidence SHALL be rejected at write time.

---

## Requirement 3: Bitemporal Storage

**User story:** As an auditor, I want every fact's history retained, so that a past decision
can be explained under the facts that were available when it was made.

**Trace:** `[CDL-6b]`, inherits foundation R4

### Acceptance Criteria

1. WHEN a fact is written THEN it SHALL carry `valid_from`, `valid_to`, and `recorded_at`.
2. WHEN a fact changes THEN the prior version SHALL have `valid_to` closed and a new
   version appended.
3. WHEN the store interface is defined THEN it SHALL expose `append` and `supersede` only;
   no `update` or `delete` verb SHALL exist.
4. WHEN a point-in-time query is issued THEN the system SHALL return the fact versions
   valid at that instant.
5. WHEN a fact exceeds its source's staleness threshold THEN it SHALL be marked stale and
   become ineligible as a rule input.
6. WHEN a decision is recorded THEN it SHALL reference the specific fact versions it relied
   upon, not the current values.
7. WHEN history is queried THEN superseded versions SHALL remain retrievable with their
   intervals.

---

## Requirement 4: External Release Pinning and Schema Evolution

**User story:** As a maintainer, I want vocabulary releases pinned and their changes
detected, so that a rule cannot silently stop working when an identifier moves.

**Trace:** `[CDL-9d]` `[CDL-6b]`

### Acceptance Criteria

1. WHEN a versioned vocabulary is loaded THEN its release identifier SHALL be read from
   configuration and recorded on every derived node and edge.
2. WHEN a release is not explicitly configured THEN the load SHALL fail rather than default
   to the latest available.
3. WHEN two releases of one vocabulary are present THEN each SHALL be addressable
   independently without one overwriting the other.
4. WHEN two releases are compared THEN every changed identifier SHALL be classified as
   `renamed`, `withdrawn`, `obsoleted_with_replacement`, `obsoleted_orphan`, or `unchanged`.
5. WHEN an identifier is classified `obsoleted_orphan` THEN the system SHALL record that no
   replacement was published.
6. WHEN a diff is run THEN it SHALL be deterministic and reproducible from the two pinned
   release identifiers alone.
7. WHEN a replacement identifier is available THEN the system SHALL propose it but SHALL
   NOT rewrite any rule automatically.
8. WHEN an entity's identifier changes across releases THEN the change SHALL be recorded as
   a supersession with both identifiers retained.

---

## Requirement 5: Ontology and Structural Conformance

**User story:** As a knowledge engineer, I want shape conformance checked and reported as
conformance, so that it is never mistaken for factual correctness.

**Trace:** `[CDL-5b]`, inherits foundation R6

### Acceptance Criteria

1. WHEN the ontology loads THEN all 8 TTL modules SHALL load and report a per-module triple
   count.
2. WHEN data is validated THEN SHACL conformance SHALL be reported as a
   `ConformanceResult`, distinct in type from any factual verification result.
3. WHEN conformance passes THEN the result SHALL NOT be described as establishing truth,
   correctness, or validity of content.
4. WHEN a conformance violation occurs during ingest THEN the offending record SHALL be
   quarantined with its violation report rather than written or silently dropped.
5. WHEN external vocabulary terms are ingested THEN they SHALL be mapped to ontology
   classes with a recorded mapping method, not assumed to align.
6. WHEN the ontology changes THEN affected data SHALL be re-validated and newly
   non-conformant records SHALL be reported.

---

## Requirement 6: Contradiction Detection Across Sources

**User story:** As an analyst, I want contradictions between sources stored as data, so
that a decision made on contested evidence is identifiable afterwards.

**Trace:** `[CDL-9c]`, inherits foundation R5

### Acceptance Criteria

1. WHEN two sources assert incompatible values for the same property of one resolved entity
   THEN a contradiction SHALL be recorded referencing both facts.
2. WHEN a contradiction exists THEN both facts SHALL remain stored and queryable.
3. WHEN a contradiction is resolved THEN the resolution SHALL cite a declared authority
   precedence and record who applied it.
4. WHEN a contradiction is unresolved THEN the affected facts SHALL default to ineligible
   as rule inputs.
5. WHEN contradictions are reported THEN unresolved ones SHALL be listed explicitly with
   counts.
6. WHEN cross-authority identifier mappings disagree — as published MONDO, DOID and MeSH
   cross-references do — THEN the disagreement SHALL be recorded rather than adjudicated.

---

## Requirement 7: Fact Supply Through the Trust Boundary

**User story:** As a governance owner, I want the graph to supply facts only through the
eligibility predicate, so that a rule cannot read evidence we would not defend.

**Trace:** `[CDL-6a]`, inherits foundation R3

### Acceptance Criteria

1. WHEN the governance engine requests facts THEN the graph SHALL return a bundle in which
   every fact has passed `is_eligible_rule_input`.
2. WHEN a fact is withheld THEN the bundle SHALL record the fact reference and the
   ineligibility reason.
3. WHEN a required fact is withheld THEN the requesting rule SHALL be unable to proceed and
   SHALL return `CANNOT_EVALUATE`.
4. WHEN a fact bundle is assembled THEN it SHALL be immutable and content-hashed, so that
   two consumers provably receive identical evidence.
5. WHEN rules are constructed THEN they SHALL hold no graph client, so that no code path
   exists from a rule to an unfiltered query.

---

## Requirement 8: Operational Verifiability

**User story:** As a reader, I want to verify the graph's composition myself with a handful
of queries, so that published counts are checkable rather than asserted.

**Trace:** `[CDL-10]`

### Acceptance Criteria

1. WHEN tier composition is published THEN a single documented query per tier SHALL
   reproduce the count.
2. WHEN derived facts are published THEN a documented query SHALL return every derived edge
   with its method, evidence and confidence.
3. WHEN derived facts must be removed THEN a purge operation SHALL remove exactly the
   derived tier without affecting others.
4. WHEN the graph is rebuilt from pinned sources and slices THEN the resulting counts SHALL
   match the recorded manifest within a documented tolerance.
5. WHEN tests run THEN graph-dependent tests SHALL be skippable without credentials and the
   remainder SHALL pass with no `.env`.
