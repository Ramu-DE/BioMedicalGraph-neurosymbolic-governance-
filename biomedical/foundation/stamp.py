"""Provenance stamps and derived confidence.

Enforces component 00 requirements R1 and R2:
  - a fact cannot exist without a stamp (the stamp is a required parameter)
  - confidence cannot be set without a registered derivation method
  - source_version is explicit; there is no implicit "latest"
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Tier(Enum):
    """Epistemic tier. R2.1 — exactly one of four."""

    ASSERTED = "asserted"   # human-authored, incl. all synthetic patient/policy data
    INGESTED = "ingested"   # external source at a pinned version
    DERIVED = "derived"     # inferred; requires method + evidence; purgeable
    RUNTIME = "runtime"     # decisions, actions, outcomes; namespace-isolated

    @property
    def strength(self) -> int:
        """Ordering for the trust policy's min_tier comparison."""
        return {"runtime": 0, "derived": 1, "ingested": 2, "asserted": 3}[self.value]


class ResolutionMethod(Enum):
    """The only legal sources of a confidence value (R1.4, R1.6).

    Confidence is a property *of the method*, never a caller argument. Adding a
    method here is a deliberate, reviewable act.
    """

    EXACT_IDENTIFIER = ("exact_identifier", 1.00)
    NORMALISED_EXACT = ("normalised_exact", 0.95)
    UNIQUE_INGREDIENT = ("unique_ingredient_after_dose_strip", 0.90)
    # An exact hit on a published ontology synonym. Scored below a label match
    # because synonym sets are curated with varying strictness, and a few MONDO
    # synonyms are broader than the term they are attached to.
    EXACT_SYNONYM = ("exact_synonym_match", 0.90)
    CURATED_MAP = ("curated_abbreviation_map", 0.80)
    SINGLE_FUZZY_ABOVE_TAU = ("single_fuzzy_above_threshold", 0.60)
    MULTI_CANDIDATE = ("multiple_candidates", 0.30)
    INVERSE_OF = ("inverse_of_relation", 1.00)
    SHARED_NAME_TOKEN = ("shared_name_token", 0.90)
    HUMAN_REVIEW = ("human_review", 1.00)

    def __init__(self, label: str, confidence: float):
        self.label = label
        self.default_confidence = confidence


@dataclass(frozen=True)
class Confidence:
    """A confidence value bound to the method that produced it.

    Cannot be constructed from a bare float — that is the whole point.
    """

    method: ResolutionMethod
    value: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.method, ResolutionMethod):
            raise TypeError("confidence requires a registered ResolutionMethod")
        v = self.default_or(self.value)
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence out of range: {v}")
        object.__setattr__(self, "value", v)

    def default_or(self, value: float | None) -> float:
        return self.method.default_confidence if value is None else float(value)

    @classmethod
    def of(cls, method: ResolutionMethod) -> Confidence:
        return cls(method=method)

    def __float__(self) -> float:
        return float(self.value)

    def __repr__(self) -> str:
        return f"Confidence({self.value:.2f} via {self.method.label})"


@dataclass(frozen=True)
class ProvenanceStamp:
    """Where a fact came from. Required on every fact (R1.1, R1.3)."""

    source: str
    source_version: str
    retrieved_at: datetime
    tier: Tier
    confidence: Confidence | None = None
    method: str | None = None
    evidence: str | None = None

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("source is required")
        if not self.source_version:
            # R1.5 - no implicit "latest"
            raise ValueError(
                "source_version is required and must be explicit; "
                "defaulting to 'latest' makes a run unreproducible"
            )
        if not isinstance(self.tier, Tier):
            raise TypeError("tier must be a Tier")
        if self.tier is Tier.DERIVED and not (self.method and self.evidence):
            # R2.4
            raise ValueError("derived facts require both method and evidence")
        if self.confidence is not None and not self.method:
            # R1.4 - confidence must trace to a named method
            raise ValueError("confidence requires a named derivation method")

    @property
    def confidence_value(self) -> float:
        """Facts with no explicit confidence are treated as fully confident in
        their own assertion; eligibility is still gated on tier and staleness."""
        return 1.0 if self.confidence is None else float(self.confidence)

    def describe(self) -> str:
        bits = [f"{self.tier.value}", f"{self.source}@{self.source_version}"]
        if self.confidence is not None:
            bits.append(f"conf={self.confidence.value:.2f}")
        if self.method:
            bits.append(f"method={self.method}")
        return " · ".join(bits)


def asserted(source: str, version: str, at: datetime) -> ProvenanceStamp:
    """Human-authored fact, including all synthetic data (R2.2)."""
    return ProvenanceStamp(source=source, source_version=version, retrieved_at=at, tier=Tier.ASSERTED)


def ingested(source: str, version: str, at: datetime) -> ProvenanceStamp:
    """Fact from an external source at a pinned version (R2.3)."""
    return ProvenanceStamp(source=source, source_version=version, retrieved_at=at, tier=Tier.INGESTED)


def derived(
    source: str, version: str, at: datetime, method: ResolutionMethod, evidence: str
) -> ProvenanceStamp:
    """Inferred fact. Carries method, evidence and method-derived confidence (R2.4)."""
    return ProvenanceStamp(
        source=source,
        source_version=version,
        retrieved_at=at,
        tier=Tier.DERIVED,
        confidence=Confidence.of(method),
        method=method.label,
        evidence=evidence,
    )


def runtime(source: str, version: str, at: datetime) -> ProvenanceStamp:
    """Record of system behaviour — decision, action, outcome (R2.5)."""
    return ProvenanceStamp(source=source, source_version=version, retrieved_at=at, tier=Tier.RUNTIME)
