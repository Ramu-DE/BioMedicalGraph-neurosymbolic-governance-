"""Demonstrate a governance rule failing open because an identifier moved.

This is the demonstration the Connected Data reviewer's third objection asked
for: not that identifiers change — that is obvious — but that a rule depending on
one **stops protecting anything, silently**, and that the system can now tell.

    .venv/bin/python scripts/demo_fail_open.py

No code and no rule changes between the two runs. Only the vocabulary release.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from biomedical.foundation import RuleOutcome, Severity  # noqa: E402
from biomedical.graph.release_diff import diff_from_graph  # noqa: E402
from biomedical.graph.rule_integrity import (  # noqa: E402
    EvaluationContext,
    check_integrity,
    evaluate_rule,
    load_rules,
    propose_remediations,
)
from biomedical.graph.store import GraphClient  # noqa: E402

RULE_UNDER_TEST = "safety.cdkn2_loss_requires_genotyping"


def line(ch: str = "=") -> None:
    print(ch * 76)


def main() -> int:
    cfg = config.load()
    if not cfg.neo4j.configured:
        print("neo4j not configured")
        return 1

    rules = load_rules()
    print(f"loaded {len(rules)} declarative rules from biomedical/rules/")
    for r in rules:
        deps = ", ".join(f"{a}:{v}" for a, v in r.dependencies()) or "-"
        print(f"  {r.rule_id:<46} authored {r.authored}  depends on {deps}")

    with GraphClient(
        uri=cfg.neo4j.uri, username=cfg.neo4j.username,
        password=cfg.neo4j.password.reveal(), database=cfg.neo4j.database,
    ) as c:
        diff = diff_from_graph(c)
        gene_rows = c.run("MATCH (g:Gene) RETURN g.symbol AS s")
        current_symbols = {r["s"] for r in gene_rows}
        trial_rows = c.run(
            "MATCH (t:ClinicalTrial) RETURN t.nct_id AS id, t.phase AS phase LIMIT 50"
        )

    print()
    line()
    print("VOCABULARY DIFF (live HGNC, read back from the graph)")
    line()
    print(diff.describe())
    print(f"\n  orphan obsoletions (no automatic path forward): {len(diff.orphans())}")

    # the two states. Nothing else differs between them.
    historical_symbols = set(current_symbols) | set(diff.moved_identifiers().keys())
    trial_props = {r["id"]: {"phase": r["phase"]} for r in trial_rows}

    ctx_before = EvaluationContext(gene_symbols=historical_symbols, trial_properties=trial_props)
    ctx_after = EvaluationContext(gene_symbols=current_symbols, trial_properties=trial_props)

    rule = next(r for r in rules if r.rule_id == RULE_UNDER_TEST)
    findings = check_integrity(rules, diff)

    print()
    line()
    print(f"RULE UNDER TEST: {rule.rule_id}")
    line()
    print(f"  authored:    {rule.authored}")
    print(f"  severity:    {rule.severity.value}")
    print(f"  depends on:  {', '.join(f'{a}:{v}' for a, v in rule.dependencies())}")
    print(f"  description: {rule.description}")
    print("\n  NOT MODIFIED between the three runs below.")

    # ---------------------------------------------------------------- run 1
    print()
    line("-")
    print("RUN 1 — release 'historical' (CDKN2 is a current symbol)")
    line("-")
    r1 = evaluate_rule(rule, ctx_before)
    print(f"  outcome: {r1.outcome.value.upper()}   severity {r1.severity.value}")
    print(f"  message: {r1.message}")
    print(f"  -> governed action: {'BLOCKED' if r1.outcome.blocks and r1.severity is Severity.BLOCK else 'ALLOWED'}")
    print("  correct: the rule found what it was written to find.")

    # ---------------------------------------------------------------- run 2
    print()
    line("-")
    print("RUN 2 — release 'current', integrity checking OFF  (the v1 behaviour)")
    line("-")
    r2 = evaluate_rule(rule, ctx_after)
    print(f"  outcome: {r2.outcome.value.upper()}   severity {r2.severity.value}")
    print(f"  message: {r2.message}")
    allowed = not (r2.outcome.blocks and r2.severity is Severity.BLOCK)
    print(f"  -> governed action: {'ALLOWED' if allowed else 'BLOCKED'}")
    print()
    print("  *** FAIL-OPEN ***")
    print("  HGNC renamed CDKN2 to CDKN2A. The rule matched nothing and reported")
    print("  NO_VIOLATION, which is indistinguishable from 'checked and clean'.")
    print("  No error was raised. No alert fired. The control simply stopped.")

    # ---------------------------------------------------------------- run 3
    print()
    line("-")
    print("RUN 3 — release 'current', integrity checking ON  (the fix)")
    line("-")
    r3 = evaluate_rule(rule, ctx_after, integrity=findings)
    print(f"  outcome: {r3.outcome.value.upper()}   severity {r3.severity.value}")
    print(f"  message: {r3.message}")
    print(f"  missing dependency: {', '.join(r3.missing_dependencies)}")
    blocked = r3.outcome.blocks
    print(f"  -> governed action: {'BLOCKED' if blocked else 'ALLOWED'}")
    print()
    print("  CANNOT_EVALUATE is not NO_VIOLATION. The action does not proceed on")
    print("  the rule's silence.")

    # ---------------------------------------------------------------- summary
    print()
    line()
    print("WHAT CHANGED BETWEEN THE RUNS")
    line()
    print("  rule definition ........ unchanged")
    print("  code ................... unchanged")
    print("  graph facts ............ unchanged")
    print("  vocabulary release ..... historical -> current")
    print()
    print(f"  run 1  {r1.outcome.value:<16} correct")
    print(f"  run 2  {r2.outcome.value:<16} WRONG - silently permissive")
    print(f"  run 3  {r3.outcome.value:<16} correct - refuses to guess")

    assert r1.outcome is RuleOutcome.VIOLATION, "run 1 should find the violation"
    assert r2.outcome is RuleOutcome.NO_VIOLATION, "run 2 should reproduce the fail-open"
    assert r3.outcome is RuleOutcome.CANNOT_EVALUATE, "run 3 should refuse to evaluate"

    # ---------------------------------------------------------- all findings
    print()
    line()
    print("ALL RULES AFFECTED BY THE CURRENT RELEASE")
    line()
    if not findings:
        print("  none")
    for f in findings:
        print(f"  {f.describe()}")
        print(f"     rule authored {f.authored}; recoverable: {f.recoverable}")

    print()
    print("REMEDIATION PROPOSALS (proposed, never applied)")
    for p in propose_remediations(findings):
        print(f"  {p.describe()}")
    print()
    print("  Nothing above has been applied. Following a rename is a judgment, and")
    print("  applying it automatically would be exactly the unaudited inference this")
    print("  layer exists to prevent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
