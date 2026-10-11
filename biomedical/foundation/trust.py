"""The trust boundary (component 00 R3).

This is the direct answer to the reviewer question the previous implementation
had no answer to:

    "Explain how requests become trusted rule inputs."

In v1 a rule read whatever the graph contained. Here every fact must pass an
explicit, versioned, testable eligibility predicate first, and whatever is
withheld is recorded with its reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from .fact import Fact, FactReference, ReviewState
from .stamp import Tier
from .temporal import StalenessPolicy


class IneligibilityReason(Enum):
    TIER_TOO_WEAK = "tier_too_weak"
    CONFIDENCE_BELOW_FLOOR = "confidence_below_floor"
    STALE = "stale"
    UNRESOLVED_CONTRADICTION = "unresolved_contradiction"
    AWAITING_REVIEW = "awaiting_review"
    REVIEW_REJECTED = "review_rejected"
    NOT_VALID_NOW = "not_valid_at_evaluation_time"


@dataclass(frozen=True)
class EligibilityVerdict:
    eligible: bool
    reasons: tuple[IneligibilityReason, ...]
    policy_version: str

    def describe(self) -> str:
        if self.eligible:
            return f"eligible (policy v{self.policy_version})"
        return "ineligible: " + ", ".join(r.value for r in self.reasons)


@dataclass(frozen=True)
class WithheldFact:
    """Audit record of a fact a rule was not allowed to see (R3.3)."""

    reference: FactReference
    reasons: tuple[IneligibilityReason, ...]

    def describe(self) -> str:
        return f"{self.reference.key} withheld: " + ", ".join(r.value for r in self.reasons)


@dataclass(frozen=True)
class TrustPolicy:
    """Configured, versioned policy. Verdicts record the version so that past
    decisions stay interpretable after the policy changes (R3.6)."""

    version: str = "1.0"
    min_tier: Tier = Tier.INGESTED
    confidence_floor: float = 0.75
    contradiction_eligible: bool = False
    require_review_clearance: bool = True
    staleness: StalenessPolicy = field(default_factory=StalenessPolicy.default_policy)

    @classmethod
    def permissive(cls) -> TrustPolicy:
        """For tests and exploration only. Never a production default."""
        return cls(
            version="permissive-0",
            min_tier=Tier.RUNTIME,
            confidence_floor=0.0,
            contradiction_eligible=True,
            require_review_clearance=False,
            staleness=StalenessPolicy(thresholds={}, default=None),
        )


def is_eligible_rule_input(fact: Fact, policy: TrustPolicy, now: datetime) -> EligibilityVerdict:
    """Decide whether a rule may read this fact (R3.1, R3.2).

    Considers tier, confidence, staleness, contradiction state, review state and
    temporal validity. Collects *all* failing reasons rather than short-circuiting,
    because a reviewer wants the full picture.
    """
    reasons: list[IneligibilityReason] = []

    if fact.tier.strength < policy.min_tier.strength:
        reasons.append(IneligibilityReason.TIER_TOO_WEAK)

    if fact.confidence < policy.confidence_floor:
        reasons.append(IneligibilityReason.CONFIDENCE_BELOW_FLOOR)

    if policy.staleness.is_stale(fact.stamp.source, fact.validity, now):
        reasons.append(IneligibilityReason.STALE)

    if fact.contested and not policy.contradiction_eligible:
        reasons.append(IneligibilityReason.UNRESOLVED_CONTRADICTION)

    if fact.review_state is ReviewState.REJECTED:
        reasons.append(IneligibilityReason.REVIEW_REJECTED)
    elif fact.review_state is ReviewState.NEEDS_REVIEW and policy.require_review_clearance:
        # R3.7 - needs_review is ineligible regardless of confidence
        reasons.append(IneligibilityReason.AWAITING_REVIEW)

    if not fact.valid_at(now):
        reasons.append(IneligibilityReason.NOT_VALID_NOW)

    return EligibilityVerdict(
        eligible=not reasons,
        reasons=tuple(reasons),
        policy_version=policy.version,
    )


def stale_after(source: str, policy: TrustPolicy) -> timedelta | None:
    return policy.staleness.threshold_for(source)
