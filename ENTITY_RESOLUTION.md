# Entity resolution

Two resolvers, both with confidence derived from the method that produced it.
No confidence value anywhere in this system is authored by a caller.

| Target | Authority | API | Key |
|---|---|---|---|
| drug mentions | RxNorm | RxNav (no key) | RXCUI |
| disease mentions | MONDO | EBI OLS4 (no key) | MONDO:nnnnnnn |

```bash
.venv/bin/python scripts/ingest.py --condition "..."      # drugs, during ingest
.venv/bin/python scripts/resolve_diseases.py --limit 400  # diseases
.venv/bin/python scripts/resolve_diseases.py --dry-run    # inspect without writing
```

## The problem it solves

ClinicalTrials.gov condition strings carry no identifier, so before this existed
the graph held one `Disease` node per surface form:

```
Diabetes Mellitus, Type 2            53 trials
Type 2 Diabetes                      45 trials
Type 2 Diabetes Mellitus             25 trials
Diabetes Mellitus                    14 trials
Diabetes                             10 trials
Type 2 Diabetes Mellitus (T2DM)       7 trials
Diabetes Mellitus Type 2              4 trials
Type II Diabetes                      4 trials
Type2 Diabetes                        4 trials
```

After resolution, **15 surface forms** collapse onto `MONDO:0005148`, carrying
158 trials as one disease. Non-small cell lung carcinoma collapsed 13 forms.

Raw strings are never discarded — each becomes a `:Mention` with
`RESOLVES_TO` → canonical term, and `STUDIES` edges are rewired to the canonical
node. `MENTIONS` preserves the original link.

## The cascade

| Stage | Method | Confidence | Review |
|---|---|---|---|
| 1 | `exact_identifier` — exact MONDO label | 1.00 | no |
| 2 | `normalised_exact` — exact after normalisation | 0.95 | no |
| 3 | `exact_synonym_match` — exact on a published synonym | 0.90 | no |
| 4 | `single_fuzzy_above_threshold` — one candidate ≥ 0.60 | 0.60 | **yes** |
| 5 | `multiple_candidates` — all retained | 0.30 | **yes** |
| — | no candidate | — | `unlinked` |

Stages 4 and 5 sit below the trust-boundary floor of 0.75, so they are ineligible
as rule inputs regardless. They are also flagged `needs_review` so the UI does
not present them as settled.

### Normalisation rules, each individually tested

| Rule | Example |
|---|---|
| MeSH comma inversion | `Diabetes Mellitus, Type 2` → `Type 2 Diabetes Mellitus` |
| trailing qualifier to front | `Diabetes Mellitus Type 2` → `Type 2 Diabetes Mellitus` |
| missing space repair | `Type2 Diabetes` → `Type 2 Diabetes` |
| trailing abbreviation strip | `Atrial Fibrillation (AF)` → `Atrial Fibrillation` |
| stage/severity noise strip | `Metastatic Melanoma` → `melanoma` |

**The last rule is lossy and that is a deliberate trade-off.** Stripping
"metastatic" merges clinically distinct cohorts onto one concept. Defensible for
*identity* resolution, wrong for cohort selection. The raw string survives on the
Mention, and `test_stage_stripping_is_lossy_and_that_is_a_known_trade_off`
exists so the trade cannot be quietly forgotten.

## Three defects found by manual audit

This is the stratified audit the method requires, and it found real problems. All
three are fixed; all three are recorded because the fix is less interesting than
the fact that the audit was necessary.

### 1. Fabricated confidence scores — the serious one

**OLS4 returns no relevance score.** `doc.get("score")` is `None` on every
document, with or without `score` in `fieldList`. The first version of the
adapter did `float(doc.get("score") or 1.0)`, so **every candidate received a
perfect 1.00**. The `single_fuzzy_above_threshold` band therefore fired on any
single-candidate result regardless of similarity, and the recorded evidence
claimed "best 1.00" for nonsense matches.

That is exactly the defect this project exists to prevent — a confidence value
with no basis — committed by me, in the module whose docstring promises derived
confidence.

Fixed by computing similarity locally (`difflib.SequenceMatcher` over the label
and every published synonym, best match wins). Deterministic and unit-tested.

Two serious false positives were corrected once scores became real:

| Raw string | Was | Now |
|---|---|---|
| `Sjogren's Syndrome` | **Marinesco-Sjogren syndrome** (unrelated rare disorder) | Sjogren syndrome ✓ |
| `Stroke` | **MELAS syndrome** (rare mitochondrial disorder) | stroke disorder ✓ |

78 stale resolutions carrying the fabricated-score signature were cleared and
re-resolved.

### 2. Veterinary terms matching human conditions

MONDO carries species variants. `Bradycardia` was resolving to
`sinus bradycardia, non-human animal`, and `type 2 diabetes mellitus, pig`
scored 0.906 against a human trial condition. Now filtered on
`non-human animal` plus an explicit species-suffix list, with a test asserting
the filter does not catch human terms that merely contain an animal word
(`catscratch disease`, `pigmented villonodular synovitis`).

### 3. Token-subset bonus too generous

A full token-subset match scored 0.85 regardless of how much broader the
candidate was, so `solid tumor` scored 0.85 against `NTRK fusion positive
cancer` (whose synonym set contains both tokens). Now the bonus requires the
candidate to carry at most one extra token. `solid tumor` correctly drops to
0.52 and goes unresolved — MONDO has no "solid tumor" concept.

## A limitation no threshold fixes

**MONDO is a disease ontology. Many trial conditions are signs or phenotypes.**

`Bradycardia`, `Albuminuria` and `Atrial Premature Complexes` are HPO phenotypes,
not MONDO diseases. Resolving them into MONDO is a category error: either nothing
matches, or something adjacent and wrong does. `Albuminuria` still scores 0.85
against `orthostatic proteinuria`, which is a different condition.

The honest fix is a second authority (HPO) and a decision about which to try
first — not a higher threshold. Not built.

Similarly, trial-specific eligibility phrases have no ontology home at all:

```
Anatomic Stage IIA Breast Cancer AJCC v8
Moderate to Severe Chronic Plaque Psoriasis
Hyperglycemia Due to Type 2 Diabetes Mellitus
```

These correctly go `needs_review` or `unlinked`. They are cohort definitions, not
disease concepts, and no disease ontology will resolve them.

## Evaluation without a gold standard

No answer key exists for these strings. Three techniques, none establishing
accuracy:

| Technique | Detects | Cannot establish |
|---|---|---|
| round-trip (RXCUI → name → re-resolve) | definite errors | correctness |
| internal consistency (brand vs generic → same RXCUI) | definite errors | correctness |
| stratified manual audit | per-band precision, with an interval | generalisation |

Drug round-trip agreement by slice: **57% – 93%** (melanoma 18/22, breast cancer
13/14, type 2 diabetes 8/14). Resolution quality is clearly domain-dependent.

The manual audit of 15 fuzzy-band disease resolutions found **5 wrong** — which
is why that band is now flagged for review rather than treated as resolved. At
n=15 the interval on that rate is wide; the finding is the direction, not the
magnitude.

## Current state

```
drugs      189 resolutions · 89 needs_review · 16 unlinked
diseases   347 raw condition strings → 511 canonical MONDO terms
           15 forms collapsed onto MONDO:0005148 (type 2 diabetes mellitus)
           13 forms collapsed onto MONDO:0005233 (non-small cell lung carcinoma)
```

Still unresolved: **trials and genes** remain raw strings. Trial NCT IDs are
already identifiers so they need no resolution; gene symbols would resolve
against HGNC, which is already ingested.

176 tests pass with no credentials and no network.
