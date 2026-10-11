"""Rule outcomes and verdicts (component 00 R3.4, R3.5).

The single most important line in this component:

    RuleOutcome.CANNOT_EVALUATE

In v1 a rule whose dependency was missing or whose identifier had moved returned
"no violation found" — indistinguishable from "checked and clean". That is a
fail-open, and it is the most dangerous behaviour a governance system can have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .fact import FactReference
from .trust import WithheldFact


class RuleOutcome(Enum):
    VIOLATION = "violation"
    NO_VIOLATION = "no_violation"
    CANNOT_EVALUATE = "cannot_evaluate"

    @property
    def is_safe(self) -> bool:
        """Only NO_VIOLATION is safe. CANNOT_EVALUATE must never be permissive."""
        return self is RuleOutcome.NO_VIOLATION

    @property
    def blocks(self) -> bool:
        return self in (RuleOutcome.VIOLATION, RuleOutcome.CANNOT_EVALUATE)


class Severity(Enum):
    BLOCK = "block"
    WARN = "warn"
    INFORM = "inform"


@dataclass(frozen=True)
class Timings:
    """Latency, instrumented honestly (component 00, closes CDL-1).

    `governance_total_ms` is the only figure permitted as a headline. Component
    figures may be reported only with their qualifying note attached.
    """

    governance_total_ms: float
    facts_ms: float | None = None
    rules_ms: float | None = None

    RULES_NOTE = "rule evaluation over already-materialised facts; excludes the graph round-trip"

    def __post_init__(self) -> None:
        if self.governance_total_ms < 0:
            raise ValueError("governance_total_ms must be non-negative")

    @classmethod
    def of(cls, facts_ms: float, rules_ms: float) -> Timings:
        return cls(governance_total_ms=facts_ms + rules_ms, facts_ms=facts_ms, rules_ms=rules_ms)

    def headline(self) -> str:
        return f"{self.governance_total_ms:.2f} ms end-to-end"

    def describe(self) -> str:
        parts = [self.headline()]
        if self.facts_ms is not None and self.rules_ms is not None:
            parts.append(
                f"(facts {self.facts_ms:.2f} ms · rules {self.rules_ms:.2f} ms — {self.RULES_NOTE})"
            )
        return " ".join(parts)


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    outcome: RuleOutcome
    severity: Severity
    message: str = ""
    policy_iri: str | None = None
    facts_relied_on: tuple[FactReference, ...] = ()
    missing_dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome is RuleOutcome.CANNOT_EVALUATE and not (
            self.missing_dependencies or self.message
        ):
            raise ValueError("CANNOT_EVALUATE must state what was missing")


@dataclass(frozen=True)
class Verdict:
    """The decision record. Enumerates what was relied upon *and what was
    withheld* (R3.5) — the latter being the view v1 had no equivalent of."""

    question: str
    results: tuple[RuleResult, ...]
    facts_relied_on: tuple[FactReference, ...]
    facts_withheld: tuple[WithheldFact, ...]
    trust_policy_version: str
    source_versions: dict[str, str]
    timings: Timings
    decided_at: datetime
    bundle_hash: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return any(r.outcome.blocks and r.severity is Severity.BLOCK for r in self.results)

    @property
    def inconclusive(self) -> bool:
        """True when any rule could not evaluate. Must never read as clean."""
        return any(r.outcome is RuleOutcome.CANNOT_EVALUATE for r in self.results)

    def decision(self) -> str:
        if self.blocked:
            return "BLOCKED"
        if self.inconclusive:
            return "INCONCLUSIVE"
        return "ALLOWED"

    def describe(self) -> str:
        lines = [
            f"Q: {self.question}",
            f"decision: {self.decision()}   policy v{self.trust_policy_version}",
            f"timing: {self.timings.describe()}",
            "rules:",
        ]
        for r in self.results:
            mark = {"violation": "X", "no_violation": ".", "cannot_evaluate": "?"}[r.outcome.value]
            lines.append(f"  [{mark}] {r.rule_id:<48} {r.outcome.value} ({r.severity.value})")
            if r.outcome is RuleOutcome.CANNOT_EVALUATE:
                missing = ", ".join(r.missing_dependencies) or r.message
                lines.append(f"        withheld dependency: {missing}")
        if self.facts_relied_on:
            lines.append("facts relied on:")
            for f in self.facts_relied_on:
                lines.append(
                    f"  {f.tier.value:<9} {f.key}={f.value_repr} "
                    f"conf={f.confidence:.2f} {f.source}@{f.source_version} [{f.validity}]"
                )
        if self.facts_withheld:
            lines.append("facts withheld:")
            for w in self.facts_withheld:
                lines.append(f"  {w.describe()}")
        return "\n".join(lines)


def assert_exhaustive(outcome: RuleOutcome) -> None:
    """Call in a match's default branch so adding a variant fails loudly rather
    than falling through to a permissive path (design: outcome-exhaustiveness)."""
    raise AssertionError(f"unhandled RuleOutcome: {outcome!r}")
