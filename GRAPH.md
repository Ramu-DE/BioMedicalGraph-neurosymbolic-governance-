# The persisted graph

Live in your Aura instance now. Built by `scripts/ingest.py` from four real open
sources. Nothing is cleaned up — browse it at https://console.neo4j.io

```bash
.venv/bin/python scripts/ingest.py --condition "<condition>" --trials 200 --ae 60 --resolve 30
.venv/bin/python scripts/ingest.py --wipe        # clear and rebuild
```

## What is in it

**6,854 nodes · 8,034 relationships · 4 sources · 7 therapeutic areas**
(melanoma, lung cancer, breast cancer, type 2 diabetes, psoriasis, rheumatoid
arthritis, atrial fibrillation)

| Label | Count | | Source | Nodes |
|---|---:|---|---|---:|
| Mention | 3,270 | | clinicaltrials.gov | 4,213 |
| ClinicalTrial | 1,208 | | openfda.faers | 2,146 |
| Disease | 946 | | rxnorm (RxNav) | 472 |
| Reaction | 526 | | hgnc | 23 |
| AdverseEventReport | 409 | | | |
| Drug | 333 | | | |
| Identifier | 154 | | | |
| Gene | 8 | | | |

Relationships: `MENTIONS`, `STUDIES`, `REPORTS_REACTION`, `RESOLVES_TO`,
`CANDIDATE_FOR`, `HAS_IDENTIFIER`, `INVESTIGATES`, `ASSOCIATED_WITH_REACTION`,
`REPLACED_BY`.

Every node carries `source`, `source_version`, `retrieved_at` and `tier`, enforced
by `:Provenanced` constraints. RxNorm release pinned at `05-Oct-2026`.

## Entity resolution — derived, never authored

v1's `external_mappings.csv` had 30 rows, every RxNorm mapping hand-asserted at
`1.0`. Here every confidence is a property of the **method** that produced it:

| Method | n | Confidence |
|---|---:|---:|
| `exact_identifier` | 78 | 1.00 |
| `normalised_exact` | 20 | 0.95 |
| `unique_ingredient_after_dose_strip` | 1 | 0.90 |
| `multiple_candidates` | 90 | 0.30 → `needs_review` |

**189 resolved · 90 need review (48%) · 16 unlinked.**

That 48% is the headline, and it is published deliberately. The input is
human-entered FAERS `medicinalproduct` text and free-text ClinicalTrials.gov
intervention names — strings nobody authored for convenience:

```
ABRAXANE (PACLITAXEL) (UNKNOWN)          -> 3 candidates, best 0.30  needs_review
ASPIR-81                                 -> 3 candidates, best 0.30  needs_review
Allogeneic natural killer (NK) cell      -> 4 candidates, best 0.30  needs_review
ASPIRIN.                                 -> aspirin (1191)      0.95 normalised_exact
ADALIMUMAB                               -> adalimumab (327361) 1.00 exact_identifier
```

### Evaluated without a gold standard

No answer key exists for these strings, so quality is assessed by **round-trip**:
RXCUI → canonical name → re-resolve → same RXCUI?

| Condition slice | Round-trip agreement |
|---|---|
| melanoma | 18/22 = 82% |
| breast cancer | 13/14 = 93% |
| lung cancer | 13/18 = 72% |
| psoriasis / RA / AF | 7/10 = 70% |
| type 2 diabetes | 8/14 = 57% |

A disagreement is a **definite error** even without knowing the right answer.
Agreement establishes nothing about accuracy, and the report says so on screen.
Resolution quality is clearly domain-dependent — 57% to 93% across slices.

## Real schema evolution, not a synthetic rename

HGNC publishes previous symbols. These are genuine, dated, public renames now in
the graph as `REPLACED_BY` edges:

```
CDKN2  -> CDKN2A        MLM   -> CDKN2A
WS2A   -> MITF          WS2   -> MITF
PBT    -> KIT           BZS   -> PTEN        MHAM -> PTEN
```

A governance rule written against `CDKN2` silently matches nothing today. That is
the fail-open the spec called for, and no part of it was fabricated.

## Two problems the real data exposed

**1. Disease identity is unresolved — and it shows.**

```
Diabetes Mellitus, Type 2    53 trials
Type 2 Diabetes              45 trials
```

Two nodes, one disease. I resolved *drugs* against RxNorm but left disease strings
as raw ClinicalTrials.gov text. This is exactly the MONDO/DOID/MeSH identity
problem the spec predicted, visible in the data rather than argued about. It is
the next thing to build, and it is honest talk material: the moment you ingest
real sources, identity stops being optional.

**2. `ASSOCIATED_WITH_REACTION` is co-reporting, not causation.**

`Avonex → Malignant melanoma (n=21)` means those terms appeared in the same FAERS
reports. FAERS is a spontaneous reporting system with no denominator, no control
group and heavy reporting bias. These edges are `tier: derived` with
`derivation: faers_coreport` precisely so they cannot be mistaken for evidence of
harm. **No causal claim may be made from them**, and the claims register must say
so before any of this is published.

## Browse it

```cypher
// trials and the drugs they investigate (resolution working end to end)
MATCH (t:ClinicalTrial)-[:INVESTIGATES]->(d:Drug) RETURN t, d LIMIT 50;

// the review backlog - real messy strings needing a human
MATCH (m:Mention)-[r:RESOLVES_TO]->(d:Drug)
WHERE r.review_state = 'needs_review'
RETURN m.raw_text, r.confidence, r.method, d.canonical_name LIMIT 30;

// competing candidates retained, not silently collapsed
MATCH (m:Mention)-[c:CANDIDATE_FOR]->(d:Drug)
RETURN m.raw_text, d.canonical_name, c.score, c.rank ORDER BY m.raw_text LIMIT 50;

// real HGNC symbol renames
MATCH (old:Identifier {authority:'hgnc-symbol'})-[:REPLACED_BY]->(g:Gene)
RETURN old.value AS was, g.symbol AS now, g.name;

// provenance - every node, every tier, countable
MATCH (n:Provenanced) RETURN n.source, n.source_version, n.tier, count(*) ORDER BY count(*) DESC;

// the disease-identity problem, visible
MATCH (d:Disease) WHERE toLower(d.name) CONTAINS 'diabetes'
RETURN d.name, size([(t)-[:STUDIES]->(d) | t]) AS trials ORDER BY trials DESC;
```

## Against the reviewer's objection

> *"545 nodes, 6 entity types and a small synthetic ontology is a teaching toy...
> No entity resolution, no heterogeneous sources, no schema evolution."*

| Objection | Now |
|---|---|
| 545 nodes | 6,854 nodes / 8,034 relationships, from live APIs |
| 6 entity types | 8 labels, 9 relationship types, all ingested |
| no entity resolution | 189 resolutions across 4 derived-confidence methods, 48% review backlog, candidates retained |
| no heterogeneous sources | 4 independent sources with incompatible models |
| no schema evolution | 7 real HGNC renames as `REPLACED_BY` edges |

Still short of the spec's 100k-entity floor, and the route there is more slices
rather than more code — the pipeline is idempotent, so re-running with new
conditions accumulates. Disease resolution should land before scaling further,
or the duplicate-identity problem above multiplies.
