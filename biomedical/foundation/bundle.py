"""FactBundle — the immutable, content-hashed evidence set.

Serves two purposes with one mechanism:

1. Component 01 R7 — rules receive facts only through the trust boundary, and
   hold no store client, so no code path reaches an unfiltered query.
2. Component 05 R1 — the evaluation framework needs to *prove* two arms received
   identical evidence. The content hash is that proof.

The second use is why the confound that sank v1 cannot recur: parity is
structural rather than asserted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .fact import Fact, FactReference
from .trust import (
    EligibilityVerdict,
    TrustPolicy,
    WithheldFact,
    is_eligible_rule_input,
)


@dataclass(frozen=True)
class FactBundle:
    """Eligible facts, withheld facts with reasons, and a content hash."""

    facts: dict[str, Fact]
    withheld: tuple[WithheldFact, ...]
    content_hash: str
    trust_policy_version: str
    assembled_at: datetime

    def get(self, key: str) -> Fact | None:
        return self.facts.get(key)

    def require(self, *keys: str) -> tuple[list[Fact], list[str]]:
        """Return (found, missing). A rule uses `missing` to decide whether it
        must return CANNOT_EVALUATE rather than NO_VIOLATION."""
        found, missing = [], []
        for k in keys:
            f = self.facts.get(k)
            if f is None:
                missing.append(k)
            else:
                found.append(f)
        return found, missing

    def withheld_keys(self) -> tuple[str, ...]:
        return tuple(w.reference.key for w in self.withheld)

    def references(self) -> tuple[FactReference, ...]:
        return tuple(FactReference.of(f) for f in self.facts.values())

    def source_versions(self) -> dict[str, str]:
        return {f.stamp.source: f.stamp.source_version for f in self.facts.values()}

    def describe(self) -> str:
        return (
            f"FactBundle({len(self.facts)} eligible, {len(self.withheld)} withheld, "
            f"hash={self.content_hash[:12]}, policy=v{self.trust_policy_version})"
        )


def _hash_facts(facts: Iterable[Fact]) -> str:
    """Deterministic hash over the epistemically relevant content of each fact.

    Deliberately excludes assembly time and object identity, so two independent
    assemblies of the same evidence hash identically.
    """
    payload = sorted(
        (
            f.key,
            repr(f.value),
            f.tier.value,
            round(f.confidence, 6),
            f.stamp.source,
            f.stamp.source_version,
            f.validity.describe(),
            f.contested,
            f.review_state.value,
        )
        for f in facts
    )
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def assemble(
    candidates: Iterable[Fact],
    policy: TrustPolicy,
    now: datetime,
) -> FactBundle:
    """Filter candidates through the trust boundary and bundle the survivors."""
    eligible: dict[str, Fact] = {}
    withheld: list[WithheldFact] = []

    for fact in candidates:
        verdict: EligibilityVerdict = is_eligible_rule_input(fact, policy, now)
        if verdict.eligible:
            eligible[fact.key] = fact
        else:
            withheld.append(
                WithheldFact(reference=FactReference.of(fact), reasons=verdict.reasons)
            )

    return FactBundle(
        facts=eligible,
        withheld=tuple(withheld),
        content_hash=_hash_facts(eligible.values()),
        trust_policy_version=policy.version,
        assembled_at=now,
    )


class ParityError(RuntimeError):
    """Raised when two arms would not receive identical evidence (comp. 05 R1.5)."""


def assert_parity(*bundles: FactBundle) -> str:
    """Abort rather than emit results from a confounded comparison."""
    if not bundles:
        raise ParityError("no bundles supplied")
    hashes = {b.content_hash for b in bundles}
    if len(hashes) != 1:
        raise ParityError(
            "evidence parity violated: arms would receive different fact sets "
            f"({sorted(h[:12] for h in hashes)}). Refusing to produce results."
        )
    return hashes.pop()
