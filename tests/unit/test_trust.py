"""Component 00 R3 — the trust boundary.

The critical suite. This is the direct answer to the reviewer question v1 had no
answer to: "Explain how requests become trusted rule inputs."
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from biomedical.foundation import (
    Confidence,
    Contradiction,
    Fact,
    IneligibilityReason,
    ProvenanceStamp,
    ResolutionMethod,
    ReviewState,
    StalenessPolicy,
    Tier,
    TrustPolicy,
    ValidityInterval,
    days,
    derived,
    ingested,
    is_eligible_rule_input,
)

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def make_fact(
    *,
    source: str = "openfda:faers",
    tier: Tier = Tier.INGESTED,
    confidence: float | None = None,
    method: ResolutionMethod | None = None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    review: ReviewState = ReviewState.NOT_REQUIRED,
    contested: bool = False,
) -> Fact:
    if tier is Tier.DERIVED:
        stamp = derived(source, "2026-09", NOW, method or ResolutionMethod.SINGLE_FUZZY_ABOVE_TAU, "evidence")
    elif confidence is not None and method is not None:
        stamp = ProvenanceStamp(
            source=source, source_version="2026-09", retrieved_at=NOW, tier=tier,
            confidence=Confidence(method=method, value=confidence), method=method.label,
        )
    else:
        stamp = ProvenanceStamp(
            source=source, source_version="2026-09", retrieved_at=NOW, tier=tier
        )

    fact: Fact = Fact(
        key="consent_status",
        value="Withdrawn",
        stamp=stamp,
        validity=ValidityInterval(
            valid_from=valid_from or datetime(2026, 10, 1, tzinfo=timezone.utc),
            valid_to=valid_to,
        ),
        recorded_at=NOW,
        review_state=review,
    )
    if contested:
        c = Contradiction(
            contradiction_id="c1",
            entity_id="pat:PAT015",
            property="consent_status",
            sources=("openfda:faers", "catalog"),
            values=("Withdrawn", "Active"),
            detected_at=NOW,
        )
        fact = fact.with_contradiction(c.ref())
    return fact


# -- the happy path -------------------------------------------------------------


def test_a_fresh_ingested_fact_is_eligible():
    v = is_eligible_rule_input(make_fact(), TrustPolicy(), NOW)
    assert v.eligible
    assert v.reasons == ()
    assert v.policy_version == "1.0"


# -- R3.2 : one test per ineligibility reason ----------------------------------


def test_tier_too_weak():
    f = make_fact(tier=Tier.RUNTIME)
    v = is_eligible_rule_input(f, TrustPolicy(min_tier=Tier.INGESTED), NOW)
    assert not v.eligible
    assert IneligibilityReason.TIER_TOO_WEAK in v.reasons


def test_confidence_below_floor():
    f = make_fact(tier=Tier.DERIVED, method=ResolutionMethod.MULTI_CANDIDATE)
    v = is_eligible_rule_input(f, TrustPolicy(min_tier=Tier.DERIVED, confidence_floor=0.75), NOW)
    assert not v.eligible
    assert IneligibilityReason.CONFIDENCE_BELOW_FLOOR in v.reasons


def test_stale_fact_is_ineligible():
    # faers threshold is 90 days; this fact is 100 days old
    f = make_fact(valid_from=NOW - timedelta(days=100))
    v = is_eligible_rule_input(f, TrustPolicy(), NOW)
    assert not v.eligible
    assert IneligibilityReason.STALE in v.reasons


def test_unresolved_contradiction_is_ineligible_by_default():
    v = is_eligible_rule_input(make_fact(contested=True), TrustPolicy(), NOW)
    assert not v.eligible
    assert IneligibilityReason.UNRESOLVED_CONTRADICTION in v.reasons


def test_awaiting_review_is_ineligible():
    v = is_eligible_rule_input(make_fact(review=ReviewState.NEEDS_REVIEW), TrustPolicy(), NOW)
    assert not v.eligible
    assert IneligibilityReason.AWAITING_REVIEW in v.reasons


def test_rejected_review_is_ineligible():
    v = is_eligible_rule_input(make_fact(review=ReviewState.REJECTED), TrustPolicy(), NOW)
    assert not v.eligible
    assert IneligibilityReason.REVIEW_REJECTED in v.reasons


def test_fact_not_valid_at_evaluation_time_is_ineligible():
    f = make_fact(
        valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        valid_to=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    v = is_eligible_rule_input(f, TrustPolicy(staleness=StalenessPolicy()), NOW)
    assert not v.eligible
    assert IneligibilityReason.NOT_VALID_NOW in v.reasons


def test_every_reason_is_reachable():
    """No dead enum members — each reason has a test above."""
    covered = {
        IneligibilityReason.TIER_TOO_WEAK,
        IneligibilityReason.CONFIDENCE_BELOW_FLOOR,
        IneligibilityReason.STALE,
        IneligibilityReason.UNRESOLVED_CONTRADICTION,
        IneligibilityReason.AWAITING_REVIEW,
        IneligibilityReason.REVIEW_REJECTED,
        IneligibilityReason.NOT_VALID_NOW,
    }
    assert covered == set(IneligibilityReason)


# -- R3.2 : all failing reasons collected, not short-circuited -----------------


def test_multiple_reasons_are_all_reported():
    f = make_fact(
        tier=Tier.DERIVED,
        method=ResolutionMethod.MULTI_CANDIDATE,
        valid_from=NOW - timedelta(days=400),
        review=ReviewState.NEEDS_REVIEW,
        contested=True,
    )
    v = is_eligible_rule_input(f, TrustPolicy(), NOW)
    assert not v.eligible
    assert len(v.reasons) >= 4, v.reasons


# -- R3.7 : needs_review beats high confidence ---------------------------------


def test_needs_review_overrides_perfect_confidence():
    f = make_fact(
        confidence=1.0,
        method=ResolutionMethod.EXACT_IDENTIFIER,
        review=ReviewState.NEEDS_REVIEW,
    )
    v = is_eligible_rule_input(f, TrustPolicy(), NOW)
    assert not v.eligible
    assert IneligibilityReason.AWAITING_REVIEW in v.reasons


# -- R3.6 : policy version recorded so past verdicts stay interpretable --------


def test_policy_version_is_recorded_on_every_verdict():
    for policy in (TrustPolicy(), TrustPolicy(version="2.0"), TrustPolicy.permissive()):
        v = is_eligible_rule_input(make_fact(), policy, NOW)
        assert v.policy_version == policy.version


# -- R4.4 : per-source staleness -----------------------------------------------


def test_release_pinned_sources_do_not_go_stale_by_age():
    old = make_fact(source="hgnc", valid_from=NOW - timedelta(days=5000))
    v = is_eligible_rule_input(old, TrustPolicy(), NOW)
    assert v.eligible, "hgnc is release-pinned, not age-limited"


def test_sources_age_at_different_rates():
    policy = TrustPolicy()
    age = timedelta(days=60)
    faers = make_fact(source="openfda:faers", valid_from=NOW - age)      # 90d threshold
    trials = make_fact(source="clinicaltrials", valid_from=NOW - age)    # 30d threshold
    assert is_eligible_rule_input(faers, policy, NOW).eligible
    assert not is_eligible_rule_input(trials, policy, NOW).eligible


def test_staleness_policy_inherits_by_source_prefix():
    p = StalenessPolicy.default_policy()
    assert p.threshold_for("openfda:faers") == days(90)
    assert p.threshold_for("openfda") == days(90)
    assert p.threshold_for("unknown:source") == days(365)


# -- the permissive policy is explicit, never a silent default -----------------


def test_permissive_policy_is_clearly_labelled():
    assert TrustPolicy.permissive().version.startswith("permissive")
    assert TrustPolicy().min_tier is Tier.INGESTED
    assert TrustPolicy().contradiction_eligible is False
