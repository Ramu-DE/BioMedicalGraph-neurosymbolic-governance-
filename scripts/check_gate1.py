"""Gate 1 check — component 00 Foundation (see ROADMAP.md).

Asserts the six criteria programmatically rather than by inspection, and runs the
claims lint over the repository's public prose.

    .venv/bin/python scripts/check_gate1.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from biomedical.foundation import (  # noqa: E402
    Claim,
    ClaimCategory,
    ClaimError,
    Confidence,
    Fact,
    IneligibilityReason,
    ProvenanceStamp,
    ResolutionMethod,
    RuleOutcome,
    RuleResult,
    Severity,
    Tier,
    TrustPolicy,
    ValidityInterval,
    assemble,
    ingested,
    is_eligible_rule_input,
    lint_public_text,
)

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)
PASSED, FAILED = [], []


def check(name: str, fn) -> None:
    try:
        fn()
        PASSED.append(name)
        print(f"  [ ok ] {name}")
    except Exception as exc:  # noqa: BLE001
        FAILED.append((name, exc))
        print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- criterion 1
def c1_unstamped_fact_is_a_type_error() -> None:
    try:
        Fact(key="k", value="v")  # type: ignore[call-arg]
    except TypeError:
        return
    raise AssertionError("an unstamped Fact was constructed")


# ---------------------------------------------------------------- criterion 2
def c2_confidence_needs_a_registered_method() -> None:
    try:
        Confidence(method=0.95)  # type: ignore[arg-type]
    except TypeError:
        pass
    else:
        raise AssertionError("confidence accepted a bare float")

    try:
        ProvenanceStamp(
            source="s", source_version="v", retrieved_at=NOW, tier=Tier.INGESTED,
            confidence=Confidence.of(ResolutionMethod.EXACT_IDENTIFIER), method=None,
        )
    except ValueError:
        return
    raise AssertionError("confidence accepted without a named method")


# ---------------------------------------------------------------- criterion 3
def c3_every_ineligibility_reason_is_reachable() -> None:
    def f(**kw):
        return Fact(
            key="k", value="v",
            stamp=kw.pop("stamp", ingested("openfda:faers", "2026-09", NOW)),
            validity=kw.pop("validity", ValidityInterval(valid_from=NOW - timedelta(days=1))),
            recorded_at=NOW, **kw,
        )

    from biomedical.foundation import ReviewState, derived

    seen: set[IneligibilityReason] = set()
    cases = [
        f(stamp=ProvenanceStamp(source="s", source_version="v", retrieved_at=NOW, tier=Tier.RUNTIME)),
        f(stamp=derived("r", "1", NOW, ResolutionMethod.MULTI_CANDIDATE, "e")),
        f(validity=ValidityInterval(valid_from=NOW - timedelta(days=500))),
        f(review_state=ReviewState.NEEDS_REVIEW),
        f(review_state=ReviewState.REJECTED),
        f(validity=ValidityInterval(
            valid_from=NOW - timedelta(days=300), valid_to=NOW - timedelta(days=200))),
    ]
    for fact in cases:
        seen.update(is_eligible_rule_input(fact, TrustPolicy(), NOW).reasons)

    # contested requires a contradiction ref
    from biomedical.foundation import Contradiction
    contested = f().with_contradiction(
        Contradiction(
            contradiction_id="c", entity_id="e", property="p",
            sources=("a", "b"), values=("1", "2"), detected_at=NOW,
        ).ref()
    )
    seen.update(is_eligible_rule_input(contested, TrustPolicy(), NOW).reasons)

    missing = set(IneligibilityReason) - seen
    if missing:
        raise AssertionError(f"unreachable reasons: {[m.value for m in missing]}")


# ---------------------------------------------------------------- criterion 4
def c4_withheld_dependency_yields_cannot_evaluate() -> None:
    from biomedical.foundation import ReviewState

    pending = Fact(
        key="ae_severity", value="Severe",
        stamp=ingested("openfda:faers", "2026-09", NOW),
        validity=ValidityInterval(valid_from=NOW - timedelta(days=1)),
        recorded_at=NOW, review_state=ReviewState.NEEDS_REVIEW,
    )
    bundle = assemble([pending], TrustPolicy(), NOW)
    found, missing = bundle.require("ae_severity")

    if found or not missing:
        raise AssertionError("an ineligible fact reached the rule")

    result = RuleResult(
        rule_id="safety.ae_disclosure",
        outcome=RuleOutcome.CANNOT_EVALUATE,
        severity=Severity.WARN,
        missing_dependencies=tuple(missing),
    )
    if result.outcome.is_safe:
        raise AssertionError("CANNOT_EVALUATE reported itself as safe")
    if not bundle.withheld:
        raise AssertionError("withholding was not recorded")


# ---------------------------------------------------------------- criterion 5
def c5_claim_without_limits_fails() -> None:
    try:
        Claim(
            claim_id="x", claim="A bounded claim", category=ClaimCategory.ACCURACY,
            evidence="manifests/x.json", n=12, does_not_establish="",
        )
    except ClaimError:
        return
    raise AssertionError("a claim with no stated limits was accepted")


# ---------------------------------------------------------------- criterion 6
def c6_lint_catches_bare_rates_and_banned_words() -> None:
    if not lint_public_text("The judge scored 58% accuracy."):
        raise AssertionError("bare rate not flagged")
    if not lint_public_text("Adversarial-proof by architecture."):
        raise AssertionError("banned phrase not flagged")
    if lint_public_text("Accuracy 58% (n=12, TNR [0.00, 0.43])."):
        raise AssertionError("a properly bounded rate was wrongly flagged")


def lint_repo_prose() -> None:
    print("\nclaims lint over repository prose")
    docs = sorted(p for p in ROOT.rglob("*.md") if ".venv" not in str(p))
    total = 0
    for doc in docs:
        problems = lint_public_text(doc.read_text(), path=str(doc.relative_to(ROOT)))
        if problems:
            total += len(problems)
            print(f"  {doc.relative_to(ROOT)}: {len(problems)} finding(s)")
            for p in problems[:3]:
                print(f"      - {p.split(': ', 1)[1]}")
    print(f"  scanned {len(docs)} documents, {total} finding(s)")
    print("  note: findings in spec documents are expected — they quote the rejected")
    print("        submission's own claims as examples of what the lint must catch.")


def main() -> int:
    print("Gate 1 — component 00 Foundation\n")
    check("1. an unstamped fact is a type error", c1_unstamped_fact_is_a_type_error)
    check("2. confidence requires a registered method", c2_confidence_needs_a_registered_method)
    check("3. every IneligibilityReason is reachable", c3_every_ineligibility_reason_is_reachable)
    check("4. a withheld dependency yields CANNOT_EVALUATE", c4_withheld_dependency_yields_cannot_evaluate)
    check("5. a claim with no stated limits fails", c5_claim_without_limits_fails)
    check("6. lint catches bare rates and banned words", c6_lint_catches_bare_rates_and_banned_words)

    lint_repo_prose()

    print(f"\nGate 1: {len(PASSED)}/6 criteria met")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
