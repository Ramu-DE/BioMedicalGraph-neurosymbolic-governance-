# Design: Foundation — Epistemic Model

## Overview

One package, no I/O, no credentials, no domain logic. Everything else in the system depends
on it; it depends on nothing. That inversion is deliberate — in v1 the provenance scheme
lived in a markdown file and the code was free to ignore it.

```
biomedical/foundation/
├── stamp.py          ProvenanceStamp, Tier, stamp construction
├── fact.py           Fact[T] — the only way to hold a value in this system
├── temporal.py       validity intervals, staleness policy
├── contradiction.py  Contradiction, authority precedence
├── trust.py          is_eligible_rule_input, EligibilityVerdict
├── outcome.py        RuleOutcome incl. CANNOT_EVALUATE
├── claims.py         ClaimsRegister, ClaimCategory, build-time validation
├── manifest.py       RunManifest — pinned versions, seeds, design hash
└── lint/             build-time checks: bare rates, banned words, literal confidences
```

> **Design stance.** Several things here are deliberately inconvenient. You cannot
> construct a fact without a stamp. You cannot set confidence without naming a method. You
> cannot publish a rate without an interval. Each inconvenience corresponds to a specific
> sentence in the rejection.

---

## The core type

```python
@dataclass(frozen=True)
class Fact[T]:
    value: T
    stamp: ProvenanceStamp          # required — no default
    validity: ValidityInterval
    recorded_at: datetime
    contradiction: ContradictionRef | None = None
    review_state: ReviewState = ReviewState.NOT_REQUIRED
```

There is no constructor that omits `stamp` (R1.3). `Fact` is generic so a drug name, a
consent status and a trial phase all carry the same epistemic apparatus without duplicating
it per domain type.

### `ProvenanceStamp`

```python
class Tier(Enum):
    ASSERTED = auto()   # human-authored, incl. all synthetic patient/policy data
    INGESTED = auto()   # external source at a pinned version
    DERIVED  = auto()   # inferred; requires method + evidence; purgeable
    RUNTIME  = auto()   # decisions, actions, outcomes; namespace-isolated

@dataclass(frozen=True)
class ProvenanceStamp:
    source: str
    source_version: str              # explicit; construction fails if absent  (R1.5)
    retrieved_at: datetime
    tier: Tier
    confidence: Confidence | None = None
    method: str | None = None
    evidence: str | None = None

    def __post_init__(self):
        if self.tier is Tier.DERIVED and not (self.method and self.evidence):
            raise ValueError("derived facts require method and evidence")
        if self.confidence is not None and not self.method:
            raise ValueError("confidence requires a named derivation method")
```

### `Confidence` — the authored-value fix

```python
@dataclass(frozen=True)
class Confidence:
    value: float
    method: ResolutionMethod   # must be a registered method, not a string
```

Confidence cannot be constructed from a bare float. `ResolutionMethod` is a registry
populated by components 01 and 03; a value outside it is a type error. Combined with the
lint rule in R1.6, this makes v1's `confidence=1.0` literals unrepresentable.

---

## Trust boundary — the component v1 lacked

This is the direct answer to *"how do requests become trusted rule inputs."*

```python
class IneligibilityReason(Enum):
    TIER_TOO_WEAK = auto()
    CONFIDENCE_BELOW_FLOOR = auto()
    STALE = auto()
    UNRESOLVED_CONTRADICTION = auto()
    AWAITING_REVIEW = auto()
    UNVERIFIED = auto()

@dataclass(frozen=True)
class EligibilityVerdict:
    eligible: bool
    reasons: tuple[IneligibilityReason, ...]
    policy_version: str              # so past decisions stay interpretable  (R3.6)

def is_eligible_rule_input(fact: Fact, policy: TrustPolicy, now: datetime) -> EligibilityVerdict
```

### Flow

```
rule asks for fact
        │
        ▼
is_eligible_rule_input(fact, policy, now)
        │
    ┌───┴───┐
    │       │
eligible   ineligible
    │       │
    ▼       ▼
 rule     reason recorded; fact withheld
 reads    rule returns CANNOT_EVALUATE  (never NO_VIOLATION)
```

The structural guarantee (R3.8): rules receive a `FactBundle` assembled by the governance
engine and hold **no** store client. There is no code path from a rule to an ineligible
fact, because rules cannot query at all. This mirrors the `FactSet` parity pattern used in
the evaluation framework — the same technique applied to a different problem.

### `TrustPolicy`

Configuration, versioned, hashed into the run manifest:

```yaml
trust_policy:
  version: "1.0"
  min_tier: ingested              # derived facts ineligible by default
  confidence_floor: 0.75
  contradiction: ineligible       # default per R5.4
  require_review_clearance: true
  staleness:                      # per-source, per R4.4
    openfda_faers: 90d
    hgnc: 365d
    clinicaltrials: 30d
    mondo: release-pinned
```

Per-source staleness matters: an adverse-event report and a gene symbol assignment do not
age at the same rate, and a single global TTL would be either useless or absurd.

---

## Rule outcomes — the fail-open fix

```python
class RuleOutcome(Enum):
    VIOLATION       = auto()
    NO_VIOLATION    = auto()
    CANNOT_EVALUATE = auto()   # dependency missing, ineligible, or identifier moved
```

Every verdict consumer must treat `CANNOT_EVALUATE` as **not-safe**. A linted exhaustive
match is required at each consumer so adding a variant later cannot silently fall through
to a permissive branch.

### Verdict payload

```python
@dataclass(frozen=True)
class Verdict:
    rule_id: str
    outcome: RuleOutcome
    facts_relied_on: tuple[FactReference, ...]   # tier, confidence, validity, contradiction
    facts_withheld: tuple[WithheldFact, ...]     # + IneligibilityReason
    trust_policy_version: str
    source_versions: Mapping[str, str]
    timings: Timings
```

`facts_withheld` is the audit trail for the trust boundary — it records what the rule was
*not* allowed to see, which is as important as what it read (R3.3).

`Timings` carries `governance_total_ms` as a required field and component timings as
optional, with a renderer that refuses to emit a component figure without its qualifying
note. This closes `[CDL-1]`: there is no way to publish a rule-evaluation microsecond
figure as the cost of a verdict.

---

## Temporal model

```python
@dataclass(frozen=True)
class ValidityInterval:
    valid_from: datetime
    valid_to: datetime | None        # None = currently valid
```

Bitemporal: `validity` is when the fact was true in the world, `recorded_at` is when we
learned it. Both are needed to answer "what did we know when we decided?" (R4.5).

The store interface exposes `append` and `supersede` only — **no** `update`, **no**
`delete` (R4.2). Supersession closes the prior interval and appends. This carries forward
v1's `RuntimeStore` contract, which was already correct, and extends it to knowledge facts
rather than only runtime records.

Decisions record the source versions and trust policy version active at the time, which is
what makes R4.5 and R4.6 answerable by query rather than by reconstruction.

---

## Contradiction

```python
@dataclass(frozen=True)
class Contradiction:
    entity_id: str
    property: str
    facts: tuple[FactReference, ...]      # >= 2, all retained  (R5.2)
    resolution: Resolution | None = None

@dataclass(frozen=True)
class Resolution:
    chosen: FactReference
    authority: str          # declared precedence rule, not implicit ordering
    applied_by: str
    applied_at: datetime
```

Both facts survive. Resolution is a recorded act with an accountable authority, not a
silent `MERGE` that picks a winner. Default eligibility under unresolved contradiction is
**ineligible**, which is the conservative choice and is configurable.

---

## Structural vs factual checking

Two stages, two result types, deliberately not interchangeable:

| Stage | Mechanism | Establishes | Does **not** establish |
|---|---|---|---|
| `ConformanceResult` | SHACL shapes via `pyshacl` | the data matches declared shapes | that any value is correct |
| `VerificationResult` | re-check against a named external source | agreement with that source on that date | correctness in general |

A fact with no possible verification is marked `UNVERIFIED` rather than assumed correct
(R6.4). The renderer is forbidden from describing conformance as truth (R6.2) — this is
enforced by the same lint pass that bans "proof".

---

## Claims register

A YAML register validated at build time:

```yaml
- id: agreement-illusion
  claim: "An unanchored LLM judge was a constant-FAIL predictor on this set"
  category: accuracy
  evidence: manifests/eval-20261011.json
  n: 12
  interval: "TNR 0.000, Wilson 95% [0.00, 0.43]"
  does_not_establish: >
    That LLM judges lack predictive value in general. One domain, one model,
    one prompt style, 5 true-negative opportunities.

- id: llm-outage-rules-fire
  claim: "Rules continued to evaluate while the model was unavailable"
  category: availability            # NOT safety  (R7.3)
  evidence: manifests/outage-20260827.json
  n: 1
  does_not_establish: >
    That the decisions made during the outage were correct. This is an
    availability observation, not a safety property.
```

`claims.py` fails the build on any entry missing `does_not_establish` (R7.2) and on any
claim whose text trips the banned-word lint (R7.5). Category separation prevents the v1
error of presenting availability as safety.

---

## Lint pass

Runs in CI, no credentials needed:

| Check | Fails on | Closes |
|---|---|---|
| bare-rate | a percentage or proportion in public text without `n` and an interval | `CDL-2` |
| banned-words | "proof", "proves", "adversarial-proof" applied to an empirical result | `CDL-4` |
| literal-confidence | a numeric confidence assigned outside a registered `ResolutionMethod` | `CDL-9b` |
| conformance-language | conformance results described as truth or correctness | `CDL-5b` |
| unregistered-claim | a public claim with no register entry | `CDL-7`, `CDL-8` |
| outcome-exhaustiveness | a `RuleOutcome` match without a `CANNOT_EVALUATE` branch | `CDL-6a` |

The lint pass is how this component stays load-bearing after the first enthusiastic week.

---

## Run manifest

```python
@dataclass(frozen=True)
class RunManifest:
    run_id: str
    design_hash: str
    source_versions: Mapping[str, str]
    slice_params: Mapping[str, str]
    seeds: Mapping[str, int]
    trust_policy_version: str
    code_commit: str
```

Every published figure traces to a manifest (R8.3). This is what resolves the class of
confusion v1 produced — a README table whose numbers came from three runs with different
designs, impossible to attribute after the fact.

---

## Testing strategy

Fully credential-free, which this component must be since it has no I/O:

- `test_stamp.py` — unstamped construction is a type error; derived without method/evidence
  raises; confidence without method raises; absent `source_version` raises.
- `test_trust.py` — **the critical suite.** One case per `IneligibilityReason`; a test
  asserting no code path exists from a rule to an ineligible fact; policy version recorded
  on every verdict.
- `test_outcome.py` — a withheld dependency yields `CANNOT_EVALUATE`, never `NO_VIOLATION`.
- `test_temporal.py` — supersession closes intervals; no `update`/`delete` on the interface;
  "what did we know at time T" answerable.
- `test_contradiction.py` — both facts retained; default ineligible; resolution records
  authority.
- `test_claims.py` — missing `does_not_establish` fails the build; availability cannot be
  categorised as safety.
- `test_lint.py` — each lint rule has a positive and negative fixture.

Target: this component alone carries >150 tests and needs no services running. It is the
part a reviewer can verify in ten minutes, which is the point.

---

## What a reviewer should be able to do after this component

1. Point at any fact and see its tier, confidence, derivation, validity and contradiction
   state.
2. Ask "why was this rule allowed to read that?" and get a recorded eligibility verdict.
3. Ask "what was withheld from this rule?" and get a list with reasons.
4. Distinguish "no violation found" from "could not evaluate".
5. Find the claims register and see what each claim does not establish.
6. Run the full test suite with no credentials, no network, no licence.

None of those six were possible in v1. That gap, not the node count, is what
*"the claims substantially exceed the evidence"* was describing.
