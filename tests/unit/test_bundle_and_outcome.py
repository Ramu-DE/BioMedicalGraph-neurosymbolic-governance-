"""Component 00 R3.3-R3.5 — bundles, parity, and the fail-open fix."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from biomedical.foundation import (
    Fact,
    FactBundle,
    IneligibilityReason,
    ParityError,
    ResolutionMethod,
    ReviewState,
    RuleOutcome,
    RuleResult,
    Severity,
    Tier,
    Timings,
    TrustPolicy,
    ValidityInterval,
    Verdict,
    assemble,
    assert_exhaustive,
    assert_parity,
    derived,
    ingested,
)

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def fact(key: str, value: object, *, source="catalog", age_days=1, **kw) -> Fact:
    return Fact(
        key=key,
        value=value,
        stamp=kw.pop("stamp", ingested(source, "2026-09", NOW)),
        validity=ValidityInterval(valid_from=NOW - timedelta(days=age_days)),
        recorded_at=NOW,
        **kw,
    )


# -- R3.3 : withheld facts are recorded with reasons ---------------------------


def test_bundle_separates_eligible_from_withheld():
    good = fact("dq_rule_count", 0)
    weak = fact(
        "ae_severity",
        "Severe",
        stamp=derived("resolver", "1.0", NOW, ResolutionMethod.MULTI_CANDIDATE, "two candidates"),
    )
    pending = fact("rxcui", "1657993", review_state=ReviewState.NEEDS_REVIEW)

    b = assemble([good, weak, pending], TrustPolicy(), NOW)

    assert set(b.facts) == {"dq_rule_count"}
    assert set(b.withheld_keys()) == {"ae_severity", "rxcui"}
    reasons = {w.reference.key: set(w.reasons) for w in b.withheld}
    assert IneligibilityReason.CONFIDENCE_BELOW_FLOOR in reasons["ae_severity"]
    assert IneligibilityReason.AWAITING_REVIEW in reasons["rxcui"]


def test_require_reports_missing_keys_so_a_rule_can_refuse_to_guess():
    b = assemble([fact("dq_rule_count", 0)], TrustPolicy(), NOW)
    found, missing = b.require("dq_rule_count", "consent_status")
    assert [f.key for f in found] == ["dq_rule_count"]
    assert missing == ["consent_status"]


def test_bundle_exposes_source_versions():
    b = assemble([fact("a", 1, source="rxnorm"), fact("b", 2, source="catalog")], TrustPolicy(), NOW)
    assert b.source_versions() == {"rxnorm": "2026-09", "catalog": "2026-09"}


# -- component 05 R1 : parity is structural, not asserted ----------------------


def test_identical_evidence_hashes_identically():
    facts = [fact("dq_rule_count", 0), fact("consent_status", "Withdrawn")]
    a = assemble(facts, TrustPolicy(), NOW)
    b = assemble(list(reversed(facts)), TrustPolicy(), NOW)
    assert a.content_hash == b.content_hash, "hash must not depend on input order"
    assert assert_parity(a, b) == a.content_hash


def test_different_evidence_aborts_rather_than_producing_results():
    a = assemble([fact("dq_rule_count", 0)], TrustPolicy(), NOW)
    b = assemble([fact("dq_rule_count", 1)], TrustPolicy(), NOW)
    with pytest.raises(ParityError, match="evidence parity violated"):
        assert_parity(a, b)


def test_parity_detects_a_withheld_difference():
    """The exact confound that sank v1: one arm sees a fact, the other doesn't."""
    f = fact("consent_status", "Withdrawn")
    with_fact = assemble([f], TrustPolicy(), NOW)
    without = assemble([f], TrustPolicy(min_tier=Tier.ASSERTED), NOW)
    assert without.facts == {}
    with pytest.raises(ParityError):
        assert_parity(with_fact, without)


def test_empty_parity_call_is_an_error():
    with pytest.raises(ParityError):
        assert_parity()


# -- R3.4 : CANNOT_EVALUATE is never a pass -----------------------------------


def test_cannot_evaluate_is_not_safe():
    assert RuleOutcome.NO_VIOLATION.is_safe
    assert not RuleOutcome.CANNOT_EVALUATE.is_safe
    assert not RuleOutcome.VIOLATION.is_safe


def test_cannot_evaluate_blocks():
    assert RuleOutcome.CANNOT_EVALUATE.blocks
    assert RuleOutcome.VIOLATION.blocks
    assert not RuleOutcome.NO_VIOLATION.blocks


def test_cannot_evaluate_must_state_what_was_missing():
    with pytest.raises(ValueError, match="must state what was missing"):
        RuleResult(
            rule_id="safety.ae_disclosure",
            outcome=RuleOutcome.CANNOT_EVALUATE,
            severity=Severity.WARN,
        )


def test_exactly_three_outcomes():
    assert {o.value for o in RuleOutcome} == {"violation", "no_violation", "cannot_evaluate"}


def test_assert_exhaustive_raises_loudly():
    with pytest.raises(AssertionError, match="unhandled RuleOutcome"):
        assert_exhaustive(RuleOutcome.CANNOT_EVALUATE)


# -- R3.5 : verdicts enumerate relied-upon and withheld facts ------------------


def _verdict(results: tuple[RuleResult, ...], bundle: FactBundle) -> Verdict:
    return Verdict(
        question="Certify Customer Master for the Q3 regulatory report",
        results=results,
        facts_relied_on=bundle.references(),
        facts_withheld=bundle.withheld,
        trust_policy_version=bundle.trust_policy_version,
        source_versions=bundle.source_versions(),
        timings=Timings.of(facts_ms=14.1, rules_ms=0.09),
        decided_at=NOW,
        bundle_hash=bundle.content_hash,
    )


def test_verdict_is_inconclusive_when_a_rule_cannot_evaluate():
    b = assemble([fact("dq_rule_count", 0)], TrustPolicy(), NOW)
    v = _verdict(
        (
            RuleResult("governance.hub_dq", RuleOutcome.NO_VIOLATION, Severity.BLOCK),
            RuleResult(
                "safety.ae_disclosure",
                RuleOutcome.CANNOT_EVALUATE,
                Severity.WARN,
                missing_dependencies=("ae_severity",),
            ),
        ),
        b,
    )
    assert v.inconclusive
    assert v.decision() == "INCONCLUSIVE", "must not read as ALLOWED"


def test_blocked_beats_inconclusive():
    b = assemble([fact("consent_status", "Withdrawn")], TrustPolicy(), NOW)
    v = _verdict(
        (RuleResult("consent.withdrawn_patient_data", RuleOutcome.VIOLATION, Severity.BLOCK),),
        b,
    )
    assert v.decision() == "BLOCKED"


def test_clean_verdict_is_allowed():
    b = assemble([fact("dq_rule_count", 3)], TrustPolicy(), NOW)
    v = _verdict((RuleResult("governance.hub_dq", RuleOutcome.NO_VIOLATION, Severity.BLOCK),), b)
    assert v.decision() == "ALLOWED"


def test_verdict_records_withheld_facts_in_its_description():
    pending = fact("rxcui", "1657993", review_state=ReviewState.NEEDS_REVIEW)
    b = assemble([fact("dq_rule_count", 0), pending], TrustPolicy(), NOW)
    v = _verdict((RuleResult("governance.hub_dq", RuleOutcome.VIOLATION, Severity.BLOCK),), b)
    text = v.describe()
    assert "facts withheld" in text
    assert "rxcui" in text
    assert "awaiting_review" in text


# -- closes CDL-1 : latency includes the graph round-trip ----------------------


def test_timings_headline_is_end_to_end():
    t = Timings.of(facts_ms=14.1, rules_ms=0.09)
    assert t.governance_total_ms == pytest.approx(14.19)
    assert "14.19 ms end-to-end" in t.headline()


def test_component_timing_cannot_be_shown_without_its_note():
    t = Timings.of(facts_ms=14.1, rules_ms=0.09)
    described = t.describe()
    assert "0.09" in described
    assert "excludes the graph round-trip" in described
