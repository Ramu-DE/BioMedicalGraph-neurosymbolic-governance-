# Requirements: Qdrant Vector Layer

## Introduction

The retrieval layer. It finds text that is *relevant*; it never establishes that anything
is *true*.

That sentence is the whole component. The architectural principle both reviewers were
circling — *"a graph is not automatically ground truth"* — has a corollary nobody stated:
**retrieved text is even less so**, and a system that lets a rule read a retrieved chunk has
no governance at all.

So this component is deliberately subordinate. Vectors supply context to the language
model. Facts for rules come only from component 01, through the trust boundary. The two
paths never cross, and that separation is what makes the system neurosymbolic rather than a
prompt that mentions rules.

The second job is integration: retrieval must be constrained by **resolved graph identity**.
A query about a drug should retrieve chunks about *that resolved entity*, not chunks whose
text happens to contain a similar string. This is why Qdrant is chosen over a
graph-native index — payload filtering against resolved identifiers is the feature that
matters.

### Inherited

Component 00's epistemic model. Chunks are facts about *documents*, carrying the same
`ProvenanceStamp` as everything else. Component 01's `ResolvedEntity` ids are the filter
keys.

### Scope boundaries

| Boundary | Decision |
|---|---|
| Fact supply | **Out of scope, permanently.** No rule may read a chunk. Facts come from component 01 only. |
| Embedding model | Pluggable. Bedrock Titan Embed v2 (1024-d) is the default, carried forward. |
| Generation | Component 04 assembles context; this component retrieves candidates. |
| Chunking | In scope — chunk boundaries materially affect retrieval and must be versioned. |
| Hosting | Qdrant in Docker for development; Qdrant Cloud optional for a hosted demo. |

---

## Requirement 1: Retrieval Is Never a Source of Facts

**User story:** As a governance owner, I want it to be structurally impossible for a rule to
base a verdict on retrieved text, so that no verdict rests on similarity.

**Trace:** `[CDL-5a]` extended to retrieval; preserves the v1 property both reviewers
accepted ("the rule engine never reads the prompt")

### Acceptance Criteria

1. WHEN a rule is evaluated THEN it SHALL receive facts only from a `FactBundle` produced by
   component 01, and SHALL have no access to retrieval results.
2. WHEN the retrieval client is constructed THEN the governance engine SHALL NOT hold a
   reference to it, so no code path exists from a rule to a chunk.
3. WHEN a retrieved chunk is used THEN it SHALL be used only as context for generation or
   for display to a human.
4. WHEN a chunk is stored THEN its payload SHALL mark it as `tier: ingested` document text,
   never as an asserted fact.
5. WHEN the architecture is documented THEN the separation of the retrieval path from the
   fact path SHALL be stated explicitly, with the reason.
6. WHEN tests run THEN a test SHALL assert that the governance engine's dependency graph
   excludes the vector client.

---

## Requirement 2: Retrieval Filtered by Resolved Identity

**User story:** As a user, I want retrieval constrained to the entity I actually asked
about, so that string coincidence does not pull in irrelevant text.

**Trace:** `[CDL-9b]` — the payoff of entity resolution extends into retrieval.

### Acceptance Criteria

1. WHEN a chunk is indexed THEN its payload SHALL carry the `entity_id` of every
   `ResolvedEntity` mentioned in it, as linked by component 01's resolver.
2. WHEN a query mentions an entity that resolves THEN retrieval SHALL be filtered to chunks
   carrying that `entity_id`.
3. WHEN a query mentions an entity that does **not** resolve THEN retrieval SHALL proceed
   unfiltered and the result SHALL be marked as unfiltered, so a caller can see that
   identity grounding was unavailable.
4. WHEN a chunk's entity links are derived THEN the payload SHALL record the resolution
   method and confidence for each link.
5. WHEN an entity link is below the confidence floor THEN it SHALL NOT be used as a filter
   key.
6. WHEN filtering is applied THEN the result SHALL record which filters were applied, so
   retrieval is explicable after the event.

---

## Requirement 3: Hybrid Retrieval

**User story:** As a practitioner, I want dense and lexical retrieval combined, because
biomedical queries contain both concepts and exact identifiers.

**Trace:** practical; `[CDL-9c]` (heterogeneous text sources)

### Acceptance Criteria

1. WHEN a query is issued THEN the system SHALL support dense vector search, sparse lexical
   search, and a documented fusion of both.
2. WHEN results are fused THEN the fusion method and its parameters SHALL be recorded with
   the result.
3. WHEN a query contains an exact identifier — an NCT number, an RXCUI, a gene symbol —
   THEN lexical matching SHALL be able to retrieve on it exactly.
4. WHEN retrieval configuration changes THEN the change SHALL be versioned, because
   retrieval results are not comparable across configurations.
5. WHEN results are returned THEN each SHALL carry its score, its retrieval mode, and its
   rank.

---

## Requirement 4: Chunk Provenance and Versioning

**User story:** As a reviewer, I want every chunk traceable to its document and its chunking
configuration, so that retrieval behaviour is reproducible.

**Trace:** `[CDL-10]`

### Acceptance Criteria

1. WHEN a chunk is created THEN it SHALL carry a `ProvenanceStamp` identifying the source
   document, the source version, and the retrieval timestamp.
2. WHEN a chunk is created THEN it SHALL record the chunking strategy and its parameters as
   a version identifier.
3. WHEN the chunking configuration changes THEN affected collections SHALL be rebuilt rather
   than mixed, and the collection SHALL record which configuration produced it.
4. WHEN the embedding model changes THEN the collection SHALL be rebuilt and the model
   identifier recorded; embeddings from different models SHALL NOT coexist in one
   collection.
5. WHEN a document is superseded THEN its chunks SHALL be superseded consistently with
   component 00's temporal model.
6. WHEN a published retrieval figure is cited THEN it SHALL be traceable to a run manifest
   recording the collection, the chunking version and the embedding model.

---

## Requirement 5: Honest Retrieval Evaluation

**User story:** As a reviewer, I want retrieval quality reported with the same discipline as
everything else, so that a retrieval claim is bounded by its evidence.

**Trace:** `[CDL-2]` `[CDL-3]` — the reporting discipline applies here too.

### Acceptance Criteria

1. WHEN retrieval quality is reported THEN it SHALL state the metric, `k`, the query set
   size, and a confidence interval.
2. WHEN a relevance judgment is used THEN the report SHALL state who or what produced it
   and whether a gold standard exists.
3. IF no gold standard exists THEN the report SHALL say so and state what the evaluation
   can and cannot establish.
4. WHEN retrieval configurations are compared THEN both SHALL be evaluated over the same
   query set with the same judgments.
5. WHEN a query set is synthesised THEN the synthesis method SHALL be documented, and the
   report SHALL state whether questions were generated from the documents they are meant to
   retrieve.
6. WHEN retrieval is evaluated THEN failure cases SHALL be categorised, not only aggregated.

> AC 5 exists because a benchmark whose questions are generated from the target corpus
> rewards lexical echo and reports it as retrieval quality.

---

## Requirement 6: Operational Behaviour

**User story:** As a maintainer, I want the vector layer to fail visibly and degrade
predictably.

**Trace:** `[CDL-7]` — availability is not safety; degradation must be reported as such.

### Acceptance Criteria

1. WHEN the vector store is unavailable THEN the system SHALL report degraded retrieval
   explicitly rather than returning empty results as if nothing matched.
2. WHEN retrieval is degraded THEN any generated answer SHALL record that fact, and the
   grounding assessment SHALL account for it.
3. WHEN the vector store is unavailable THEN rule evaluation SHALL be unaffected, and
   documentation SHALL state that this is an availability property and not evidence that
   decisions made during degradation were correct.
4. WHEN a collection is created THEN its vector dimension, distance metric and index
   parameters SHALL be recorded in the manifest.
5. WHEN indexing runs THEN it SHALL be idempotent on a stable chunk identifier.
6. WHEN tests run THEN retrieval tests SHALL be runnable against committed fixtures with no
   live Qdrant instance and no credentials.
