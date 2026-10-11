"""Contradiction as first-class state (component 00 R5).

When two sources disagree, both facts survive. Resolution is a recorded act with
an accountable authority, never a silent MERGE that picks a winner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Resolution:
    """How a contradiction was settled, and by whom (R5.3)."""

    chosen_source: str
    authority: str          # declared precedence rule, not an implicit ordering
    applied_by: str
    applied_at: datetime

    def __post_init__(self) -> None:
        if not self.authority:
            # R5.3 / UI R4.4 - resolution without a cited authority is not permitted
            raise ValueError("resolution requires a cited authority precedence")
        if not self.applied_by:
            raise ValueError("resolution requires an accountable applied_by")


@dataclass(frozen=True)
class ContradictionRef:
    """Lightweight pointer carried on a Fact."""

    contradiction_id: str
    resolved: bool = False


@dataclass(frozen=True)
class Contradiction:
    """Two or more incompatible assertions about one entity property."""

    contradiction_id: str
    entity_id: str
    property: str
    sources: tuple[str, ...]
    values: tuple[str, ...]
    detected_at: datetime
    resolution: Resolution | None = None

    def __post_init__(self) -> None:
        if len(self.sources) < 2:
            raise ValueError("a contradiction needs at least two sources")
        if len(self.sources) != len(self.values):
            raise ValueError("sources and values must correspond")

    @property
    def resolved(self) -> bool:
        return self.resolution is not None

    def ref(self) -> ContradictionRef:
        return ContradictionRef(contradiction_id=self.contradiction_id, resolved=self.resolved)

    def describe(self) -> str:
        pairs = ", ".join(f"{s}={v!r}" for s, v in zip(self.sources, self.values, strict=True))
        state = (
            f"resolved -> {self.resolution.chosen_source} by {self.resolution.authority}"
            if self.resolution
            else "UNRESOLVED"
        )
        return f"{self.entity_id}.{self.property}: {pairs} [{state}]"


@dataclass(frozen=True)
class AuthorityPrecedence:
    """Declared precedence used to resolve contradictions (R5.3).

    Explicit and ordered. An unlisted source cannot win by accident.
    """

    order: tuple[str, ...]

    def rank(self, source: str) -> int:
        prefix = source.split(":", 1)[0]
        for candidate in (source, prefix):
            if candidate in self.order:
                return self.order.index(candidate)
        return len(self.order)  # unlisted sources rank last

    def prefer(self, sources: tuple[str, ...]) -> str | None:
        """Return the preferred source, or None if the choice is ambiguous."""
        ranked = sorted(sources, key=self.rank)
        if len(ranked) >= 2 and self.rank(ranked[0]) == self.rank(ranked[1]):
            return None  # tie - requires a human
        return ranked[0]

    @classmethod
    def default(cls) -> AuthorityPrecedence:
        # Regulatory and authority sources outrank aggregators; aggregators
        # outrank derived text. Deliberately explicit so it can be argued with.
        return cls(order=("rxnorm", "hgnc", "mondo", "clinicaltrials", "openfda", "chembl", "catalog"))
