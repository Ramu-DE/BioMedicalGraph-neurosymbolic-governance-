"""CI gate: fail the build if any rule depends on a moved identifier.

    .venv/bin/python scripts/check_rule_identifiers.py            # uses the graph
    .venv/bin/python scripts/check_rule_identifiers.py --offline  # fixtures only

Exit codes
    0  no rule depends on a changed identifier
    1  at least one does  (schema-evolution spec R3.6)

The offline mode exists so this gate runs in CI with no credentials.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from biomedical.graph.release_diff import (  # noqa: E402
    ChangeKind,
    ReleaseDiff,
    SymbolChange,
    diff_from_graph,
)
from biomedical.graph.rule_integrity import (  # noqa: E402
    check_integrity,
    load_rules,
    propose_remediations,
)
from biomedical.graph.store import GraphClient  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "hgnc_release_diff.json"


def offline_diff() -> ReleaseDiff:
    data = json.loads(FIXTURE.read_text())
    return ReleaseDiff(
        authority=data["authority"],
        release_from=data["release_from"],
        release_to=data["release_to"],
        changes=tuple(
            SymbolChange(
                authority=data["authority"],
                old_value=c["old_value"],
                new_value=c["new_value"],
                change_kind=ChangeKind(c["change_kind"]),
                stable_id=c.get("stable_id"),
                entity_name=c.get("entity_name"),
            )
            for c in data["changes"]
        ),
    )


def live_diff() -> ReleaseDiff:
    cfg = config.load()
    with GraphClient(
        uri=cfg.neo4j.uri, username=cfg.neo4j.username,
        password=cfg.neo4j.password.reveal(), database=cfg.neo4j.database,
    ) as c:
        return diff_from_graph(c)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="use committed fixtures")
    args = ap.parse_args()

    rules = load_rules()
    diff = offline_diff() if args.offline else live_diff()

    print(f"rule-identifier integrity check  ({'offline' if args.offline else 'live'})")
    print(f"  rules checked:       {len(rules)}")
    print(f"  identifiers moved:   {len(diff.moved_identifiers())}  {diff.counts()}")

    findings = check_integrity(rules, diff)
    if not findings:
        print("\nPASS — no rule depends on a changed identifier")
        return 0

    print(f"\nFAIL — {len(findings)} rule(s) depend on a moved identifier:\n")
    for f in findings:
        print(f"  {f.rule_id}")
        print(f"      {f.authority}:{f.identifier} was {f.change.change_kind.value}"
              + (f" -> {f.change.new_value}" if f.change.new_value else " (no replacement)"))
        print(f"      rule authored {f.authored}; recoverable: {f.recoverable}")
    print("\nproposals (not applied):")
    for p in propose_remediations(findings):
        print(f"  {p.describe()}")
    print("\nThese rules cannot be evaluated. They return CANNOT_EVALUATE and block")
    print("the governed action rather than reporting 'no violation found'.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
