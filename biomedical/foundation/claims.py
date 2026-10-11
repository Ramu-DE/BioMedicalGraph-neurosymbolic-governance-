"""Claims register (component 00 R7).

Every public claim must record what it does NOT establish. A claim that cannot
complete that field is rejected at build time rather than published.

This single artefact would have prevented the rejection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

BANNED_PATTERNS = [
    re.compile(r"\badversarial[- ]proof\b", re.I),
    re.compile(r"\bprov(es|en|ing)\b", re.I),
    re.compile(r"\bproof\b", re.I),
    re.compile(r"\bzero predictive value\b", re.I),
    re.compile(r"\bbullet[- ]proof\b", re.I),
]

# A percentage or ratio with no sign of a denominator or interval nearby.
BARE_RATE = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d+)?)\s*%")
HAS_N = re.compile(r"\bn\s*=\s*\d+", re.I)
HAS_INTERVAL = re.compile(r"\[\s*[\d.]+\s*[–\-,]\s*[\d.]+\s*\]|\bCI\b|interval", re.I)


class ClaimCategory(Enum):
    """Categories exist so an availability result cannot be presented as a
    safety result (R7.3) — the v1 error with the LLM-outage anecdote."""

    ACCURACY = "accuracy"
    SAFETY = "safety"
    AVAILABILITY = "availability"
    ROBUSTNESS = "robustness"
    PERFORMANCE = "performance"
    SCALE = "scale"


class ClaimError(ValueError):
    pass


@dataclass(frozen=True)
class Claim:
    claim_id: str
    claim: str
    category: ClaimCategory
    evidence: str
    n: int | None
    does_not_establish: str
    interval: str | None = None

    def __post_init__(self) -> None:
        if not self.does_not_establish.strip():
            # R7.2
            raise ClaimError(
                f"claim {self.claim_id!r}: 'does_not_establish' is empty. "
                "A claim whose limits cannot be stated must not be published."
            )
        if not self.evidence.strip():
            raise ClaimError(f"claim {self.claim_id!r}: evidence reference is required")
        for pattern in BANNED_PATTERNS:
            if pattern.search(self.claim):
                raise ClaimError(
                    f"claim {self.claim_id!r}: banned phrasing {pattern.pattern!r} "
                    "applied to an empirical result"
                )
        if self.n is not None and self.n <= 0:
            raise ClaimError(f"claim {self.claim_id!r}: n must be positive if given")

    def describe(self) -> str:
        n = f"n={self.n}" if self.n is not None else "n=n/a"
        ci = f" {self.interval}" if self.interval else ""
        return (
            f"[{self.category.value}] {self.claim}\n"
            f"    evidence: {self.evidence}  {n}{ci}\n"
            f"    does not establish: {self.does_not_establish}"
        )


@dataclass
class ClaimsRegister:
    claims: dict[str, Claim]

    @classmethod
    def empty(cls) -> ClaimsRegister:
        return cls(claims={})

    def add(self, claim: Claim) -> None:
        if claim.claim_id in self.claims:
            raise ClaimError(f"duplicate claim id {claim.claim_id!r}")
        self.claims[claim.claim_id] = claim

    def get(self, claim_id: str) -> Claim:
        if claim_id not in self.claims:
            raise ClaimError(f"unregistered claim {claim_id!r}: refusing to display")
        return self.claims[claim_id]

    def by_category(self, category: ClaimCategory) -> list[Claim]:
        return [c for c in self.claims.values() if c.category is category]

    def render(self) -> str:
        out = []
        for category in ClaimCategory:
            group = self.by_category(category)
            if not group:
                continue
            out.append(f"== {category.value.upper()} ==")
            out.extend(c.describe() for c in group)
            out.append("")
        return "\n".join(out)


def lint_public_text(text: str, *, path: str = "<text>") -> list[str]:
    """Build-time lint for public prose (R7.5, R7.6).

    Returns a list of violations; empty means clean.
    """
    problems: list[str] = []

    for pattern in BANNED_PATTERNS:
        for m in pattern.finditer(text):
            problems.append(f"{path}: banned phrase {m.group(0)!r} (claims must be bounded)")

    for m in BARE_RATE.finditer(text):
        window = text[max(0, m.start() - 160) : m.end() + 160]
        if not (HAS_N.search(window) or HAS_INTERVAL.search(window)):
            problems.append(
                f"{path}: rate {m.group(0)!r} published without a visible n or interval"
            )

    return problems


def lint_paths(paths: list[Path]) -> list[str]:
    problems: list[str] = []
    for p in paths:
        if p.exists() and p.suffix in {".md", ".txt"}:
            problems.extend(lint_public_text(p.read_text(), path=str(p)))
    return problems
