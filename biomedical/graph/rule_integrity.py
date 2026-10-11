"""Rule identifier integrity (schema-evolution spec R3).

The defect this module exists to remove:

    release N:    rule matches gene symbol "CDKN2"  -> finds violation -> BLOCK   correct
                  (HGNC renames CDKN2 -> CDKN2A)
    release N+1:  rule matches "CDKN2"              -> matches nothing -> ALLOW   WRONG
                                                                          ^
                                          indistinguishable from "checked and clean"

The rename is not the defect. The defect is that **"found no violation" and
"could not evaluate" return the same value**. `RuleOutcome.CANNOT_EVALUATE` is
the separation, and this module is what detects when it applies.

Rule dependency extraction is static analysis over the declarative YAML, so it
needs no graph connection and runs in CI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..foundation import RuleOutcome, RuleResult, Severity
from .release_diff import ReleaseDiff, SymbolChange

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"

# Rule fields that name an external identifier, and the authority each belongs to.
IDENTIFIER_FIELDS = {
    "gene_symbol": "hgnc-symbol",
    "disease_id": "mondo",
    "rxcui": "rxnorm",
    "chembl_id": "chembl",
}


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    severity: Severity
    description: str
    authored: str | None
    when: dict
    source_file: str

    def dependencies(self) -> tuple[tuple[str, str], ...]:
        """((authority, identifier), ...) this rule depends on."""
        deps: list[tuple[str, str]] = []
        for field_name, authority in IDENTIFIER_FIELDS.items():
            value = self.when.get(field_name)
            if value:
                deps.append((authority, str(value)))
        return tuple(deps)


@dataclass(frozen=True)
class IntegrityFinding:
    rule_id: str
    authority: str
    identifier: str
    change: SymbolChange
    authored: str | None = None

    @property
    def recoverable(self) -> bool:
        return self.change.recoverable

    def describe(self) -> str:
        return (
            f"{self.rule_id} depends on {self.authority}:{self.identifier}, "
            f"which was {self.change.change_kind.value}"
            + (f" -> {self.change.new_value}" if self.change.new_value else " with no replacement")
        )


@dataclass
class RemediationProposal:
    """Proposed, never applied (spec R6). Following a rename is a judgment."""

    rule_id: str
    field_authority: str
    old_identifier: str
    proposed_identifier: str | None
    evidence: str
    status: str = "pending"
    accepted_by: str | None = None
    accepted_at: str | None = None

    def describe(self) -> str:
        if self.proposed_identifier is None:
            return (
                f"{self.rule_id}: {self.old_identifier} has no published replacement — "
                "human resolution required"
            )
        return (
            f"{self.rule_id}: propose {self.old_identifier} -> "
            f"{self.proposed_identifier} [{self.status}]"
        )


def load_rules(directory: Path | None = None) -> list[RuleSpec]:
    """Static load of the declarative packs. No graph, no credentials."""
    directory = directory or RULES_DIR
    specs: list[RuleSpec] = []
    for path in sorted(directory.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for raw in doc.get("rules", []):
            specs.append(
                RuleSpec(
                    rule_id=raw["id"],
                    severity=Severity(raw.get("severity", "warn")),
                    description=(raw.get("description") or "").strip(),
                    authored=raw.get("authored"),
                    when=raw.get("when") or {},
                    source_file=path.name,
                )
            )
    return specs


def check_integrity(rules: list[RuleSpec], diff: ReleaseDiff) -> list[IntegrityFinding]:
    """Join rule dependencies against the release diff."""
    moved = diff.moved_identifiers()
    findings: list[IntegrityFinding] = []
    for rule in rules:
        for authority, identifier in rule.dependencies():
            if authority != diff.authority:
                continue
            change = moved.get(identifier)
            if change is not None:
                findings.append(
                    IntegrityFinding(
                        rule_id=rule.rule_id, authority=authority,
                        identifier=identifier, change=change, authored=rule.authored,
                    )
                )
    return findings


def propose_remediations(findings: list[IntegrityFinding]) -> list[RemediationProposal]:
    """Propose. Never rewrite a rule automatically (spec R6.1)."""
    return [
        RemediationProposal(
            rule_id=f.rule_id,
            field_authority=f.authority,
            old_identifier=f.identifier,
            proposed_identifier=f.change.new_value,
            evidence=f.change.describe(),
            status="pending" if f.recoverable else "requires_human",
        )
        for f in findings
    ]


# --------------------------------------------------------------------------- #
# evaluation with and without integrity checking
# --------------------------------------------------------------------------- #


@dataclass
class EvaluationContext:
    """Facts available to rule evaluation, keyed by identifier."""

    gene_symbols: set[str] = field(default_factory=set)
    trial_properties: dict[str, dict] = field(default_factory=dict)


def evaluate_rule(
    rule: RuleSpec,
    ctx: EvaluationContext,
    *,
    integrity: list[IntegrityFinding] | None = None,
) -> RuleResult:
    """Evaluate one rule.

    `integrity=None` reproduces the v1 behaviour: a rule whose identifier has
    moved matches nothing and reports NO_VIOLATION. That is the fail-open.

    `integrity=[...]` enables the fix: the rule returns CANNOT_EVALUATE instead.
    """
    blocked_by = [f for f in (integrity or []) if f.rule_id == rule.rule_id]
    if blocked_by:
        f = blocked_by[0]
        return RuleResult(
            rule_id=rule.rule_id,
            outcome=RuleOutcome.CANNOT_EVALUATE,
            severity=rule.severity,
            message=(
                f"dependency {f.authority}:{f.identifier} was "
                f"{f.change.change_kind.value}"
                + (f" to {f.change.new_value}" if f.change.new_value else "")
            ),
            missing_dependencies=(f"{f.authority}:{f.identifier}",),
        )

    deps = rule.dependencies()
    gene_deps = [v for a, v in deps if a == "hgnc-symbol"]

    if gene_deps:
        symbol = gene_deps[0]
        if symbol not in ctx.gene_symbols:
            # the v1 fail-open: no match, so "nothing to see here"
            return RuleResult(
                rule_id=rule.rule_id,
                outcome=RuleOutcome.NO_VIOLATION,
                severity=rule.severity,
                message=f"no entity matched gene_symbol={symbol}",
            )
        required = rule.when.get("without_property")
        missing_everywhere = all(
            not props.get(required)
            for props in ctx.trial_properties.values()
        ) if required else False
        return RuleResult(
            rule_id=rule.rule_id,
            outcome=RuleOutcome.VIOLATION if missing_everywhere else RuleOutcome.NO_VIOLATION,
            severity=rule.severity,
            message=(
                f"gene_symbol={symbol} present and '{required}' absent"
                if missing_everywhere else f"gene_symbol={symbol} present, requirement met"
            ),
        )

    return RuleResult(
        rule_id=rule.rule_id, outcome=RuleOutcome.NO_VIOLATION,
        severity=rule.severity, message="no identifier dependency",
    )
