"""Detect and persist cross-source contradictions.

    .venv/bin/python scripts/detect_conflicts.py
    .venv/bin/python scripts/detect_conflicts.py --dry-run

Both sides of every contradiction are kept. Nothing is auto-resolved where
adjudicating would need clinical judgment rather than source precedence.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from biomedical.foundation import AuthorityPrecedence, RunManifest  # noqa: E402
from biomedical.graph.store import GraphClient  # noqa: E402
from biomedical.knowledge import conflicts as cf  # noqa: E402


def line(ch: str = "=") -> None:
    print(ch * 76)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--threshold", type=float, default=0.6)
    args = ap.parse_args()

    cfg = config.load()
    if not cfg.neo4j.configured:
        print("neo4j not configured")
        return 1

    precedence = AuthorityPrecedence.default()

    with GraphClient(
        uri=cfg.neo4j.uri, username=cfg.neo4j.username,
        password=cfg.neo4j.password.reveal(), database=cfg.neo4j.database,
    ) as c:
        print("detecting cross-source contradictions ...\n")
        indication = cf.detect_indication_vs_adverse_event(c)
        ambiguous = cf.detect_ambiguous_mentions(c, threshold=args.threshold)
        cross = cf.detect_cross_authority_identity(c)
        found = indication + ambiguous + cross

        line()
        print("1. INDICATION vs ADVERSE EVENT — needs clinical judgment, not precedence")
        line()
        if not indication:
            print("  none")
        for con in indication[:8]:
            print(f"  {con.describe()}\n")

        line()
        print("2. AMBIGUOUS MENTIONS — one surface form, several plausible targets")
        line()
        for con in ambiguous[:6]:
            print(f"  [{con.kind.value}] {con.subject}")
            for s in con.sides:
                print(f"      {s.claim[:56]:<56} {s.evidence}")
            print(f"      {con.note}\n")
        if len(ambiguous) > 6:
            print(f"  ... and {len(ambiguous) - 6} more\n")

        line()
        print("3. CROSS-AUTHORITY IDENTITY — auto-resolvable by declared precedence")
        line()
        if not cross:
            print("  none — canonical identity is currently unambiguous")
        for con in cross[:6]:
            print(f"  {con.describe()}\n")

        # resolution, only where precedence legitimately applies
        line()
        print("RESOLUTION")
        line()
        resolved: list[cf.Conflict] = []
        for con in found:
            if not con.kind.auto_resolvable:
                continue
            sources = tuple(s.source for s in con.sides)
            winner = precedence.prefer(sources)
            if winner is None:
                print(f"  {con.subject}: precedence tie — requires a human")
                continue
            resolved.append(
                cf.Conflict(
                    **{**con.__dict__,
                       "chosen_source": winner,
                       "resolution_authority": "vocab-precedence-v1",
                       "resolved_by": "detect_conflicts"}
                )
            )
        print(f"  auto-resolved by precedence: {len(resolved)}")
        human = [con for con in found if con.requires_human]
        print(f"  routed to a human:           {len(human)}")
        for con in human[:4]:
            print(f"     {con.kind.value}: {con.subject}")
        if len(human) > 4:
            print(f"     ... and {len(human) - 4} more")

        summary = cf.summarise(found)
        line()
        print("SUMMARY")
        line()
        print(f"  total conflicts     {summary['total']}")
        print(f"  by kind             {summary['by_kind']}")
        print(f"  requires human      {summary['requires_human']}")
        print(f"  auto-resolvable     {summary['auto_resolvable']}")

        if args.dry_run:
            print("\n(dry run — nothing written)")
            return 0

        final = resolved + [con for con in found if con.requires_human or
                            not con.kind.auto_resolvable]
        # keep unresolved auto-resolvable ones too (precedence ties)
        ids = {c.conflict_id for c in final}
        final += [c for c in found if c.conflict_id not in ids]

        written = cf.persist(c, final)
        contested = cf.mark_affected_ineligible(c)
        print(f"\n  persisted {written} Conflict node(s)")
        print(f"  marked {contested} resolution(s) contested -> ineligible as rule inputs")

        manifest = RunManifest.create(
            design={"task": "conflict_detection", "threshold": args.threshold},
            source_versions={"detector": "1.0", "precedence": "vocab-precedence-v1"},
            trust_policy_version=cfg.trust.policy_version,
            notes={"summary": str(summary)},
        )
        p = manifest.write(ROOT / "manifests")
        print(f"  manifest: {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
