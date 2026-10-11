"""The Fact type — the only way to hold a value in this system.

Component 00 R1: the stamp is a required positional-or-keyword parameter with no
default, so `Fact(value=...)` alone is a TypeError. That is the enforcement the
previous implementation lacked: its provenance scheme lived in a markdown file
and the code was free to ignore it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from .contradiction import ContradictionRef
from .stamp import ProvenanceStamp, Tier
from .temporal import ValidityInterval


class ReviewState(Enum):
    NOT_REQUIRED = "not_required"
    NEEDS_REVIEW = "needs_review"
    CLEARED = "cleared"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Fact[T]:
    """A value with its full epistemic status.

    Generic, so a drug name, a consent status and a trial phase all carry the
    same apparatus without duplicating it per domain type.
    """

    key: str
    value: T
    stamp: ProvenanceStamp
    validity: ValidityInterval
    recorded_at: datetime
    entity_id: str | None = None
    contradiction: ContradictionRef | None = None
    review_state: ReviewState = ReviewState.NOT_REQUIRED
    version_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.stamp, ProvenanceStamp):
            raise TypeError("Fact requires a ProvenanceStamp")
        if not self.key:
            raise ValueError("fact key is required")

    # -- derived views -------------------------------------------------------

    @property
    def tier(self) -> Tier:
        return self.stamp.tier

    @property
    def confidence(self) -> float:
        return self.stamp.confidence_value

    @property
    def contested(self) -> bool:
        return self.contradiction is not None and not self.contradiction.resolved

    def valid_at(self, instant: datetime) -> bool:
        return self.validity.contains(instant)

    # -- transitions (never mutation) ---------------------------------------

    def close(self, at: datetime) -> Fact[T]:
        """Close this version's validity. Used by supersession (R4.2)."""
        return replace(self, validity=self.validity.close(at))

    def with_contradiction(self, ref: ContradictionRef) -> Fact[T]:
        return replace(self, contradiction=ref)

    def with_review(self, state: ReviewState) -> Fact[T]:
        return replace(self, review_state=state)

    def describe(self) -> str:
        bits = [f"{self.key}={self.value!r}", self.stamp.describe(), self.validity.describe()]
        if self.contested:
            bits.append("CONTESTED")
        if self.review_state is ReviewState.NEEDS_REVIEW:
            bits.append("NEEDS_REVIEW")
        return " | ".join(bits)


@dataclass(frozen=True)
class FactReference:
    """A pointer to a fact, carrying the epistemic detail a verdict must record
    (component 00 R3.5)."""

    key: str
    value_repr: str
    tier: Tier
    confidence: float
    validity: str
    contested: bool
    source: str
    source_version: str

    @classmethod
    def of(cls, fact: Fact) -> FactReference:
        return cls(
            key=fact.key,
            value_repr=repr(fact.value),
            tier=fact.tier,
            confidence=fact.confidence,
            validity=fact.validity.describe(),
            contested=fact.contested,
            source=fact.stamp.source,
            source_version=fact.stamp.source_version,
        )
