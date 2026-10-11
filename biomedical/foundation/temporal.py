"""Bitemporal validity and per-source staleness (component 00 R4).

Two time axes:
  validity   - when the fact was true in the world (valid_from, valid_to)
  knowledge  - when we learned it (recorded_at, on Fact)

Both are needed to answer "what did we know when we decided?".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True)
class ValidityInterval:
    """Half-open interval [valid_from, valid_to). `None` means currently valid."""

    valid_from: datetime
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")

    @property
    def open(self) -> bool:
        return self.valid_to is None

    def contains(self, instant: datetime) -> bool:
        if instant < self.valid_from:
            return False
        return self.valid_to is None or instant < self.valid_to

    def close(self, at: datetime) -> ValidityInterval:
        """Return a closed copy. Supersession never mutates (R4.2)."""
        return ValidityInterval(valid_from=self.valid_from, valid_to=at)

    def age(self, now: datetime) -> timedelta:
        reference = self.valid_to or now
        return reference - self.valid_from

    def describe(self) -> str:
        to = self.valid_to.date().isoformat() if self.valid_to else "open"
        return f"{self.valid_from.date().isoformat()} -> {to}"


@dataclass(frozen=True)
class StalenessPolicy:
    """Per-source staleness thresholds (R4.4).

    An adverse-event report and a gene symbol assignment do not age at the same
    rate, so a single global TTL would be either useless or absurd.

    A source mapped to `None` is release-pinned: it does not go stale by age,
    because its currency is governed by which release is loaded.
    """

    thresholds: dict[str, timedelta | None] = field(default_factory=dict)
    default: timedelta | None = None

    def threshold_for(self, source: str) -> timedelta | None:
        if source in self.thresholds:
            return self.thresholds[source]
        # allow "openfda:faers" to inherit a policy set for "openfda"
        prefix = source.split(":", 1)[0]
        if prefix in self.thresholds:
            return self.thresholds[prefix]
        return self.default

    def is_stale(self, source: str, interval: ValidityInterval, now: datetime) -> bool:
        threshold = self.threshold_for(source)
        if threshold is None:
            return False
        return interval.age(now) > threshold

    @classmethod
    def default_policy(cls) -> StalenessPolicy:
        return cls(
            thresholds={
                "openfda": timedelta(days=90),
                "clinicaltrials": timedelta(days=30),
                "rxnorm": timedelta(days=180),
                "hgnc": None,    # release-pinned
                "mondo": None,   # release-pinned
                "chembl": timedelta(days=365),
                # authored synthetic data does not go stale by age
                "authored": None,
                "catalog": timedelta(days=90),
            },
            default=timedelta(days=365),
        )


def days(n: int) -> timedelta:
    return timedelta(days=n)
