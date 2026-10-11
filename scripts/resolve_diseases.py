"""Resolve raw disease strings against MONDO, and collapse the duplicates.

Before: ClinicalTrials.gov condition strings were stored directly as Disease
nodes, so "Type 2 Diabetes", "Diabetes Mellitus, Type 2" and
"Type 2 Diabetes Mellitus (T2DM)" were three separate diseases.

After: each raw string becomes a `:Mention`, MONDO terms become the canonical
`:ResolvedEntity:Disease`, and `STUDIES` edges are rewired to the canonical node.
The raw string is never discarded.

    .venv/bin/python scripts/resolve_diseases.py --limit 120
    .venv/bin/python scripts/resolve_diseases.py --dry-run --limit 20
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from biomedical.foundation import RunManifest  # noqa: E402
from biomedical.graph.store import GraphClient  # noqa: E402
from biomedical.sources import mondo, resolver  # noqa: E402

NOW = datetime.now(timezone.utc)


def log(m: str) -> None:
    print(m, flush=True)


def unresolved_diseases(c: GraphClient, limit: int) -> list[dict]:
    return c.run(
        """
        MATCH (d:Disease) WHERE d.mondo_id IS NULL AND NOT d:Mention
        OPTIONAL MATCH (t:ClinicalTrial)-[:STUDIES]->(d)
        WITH d, count(t) AS trials
        RETURN d.entity_id AS entity_id, d.name AS name, trials
        ORDER BY trials DESC, name LIMIT $limit
        """,
        limit=limit,
    )


def apply_resolution(c: GraphClient, old_entity_id: str, raw: str,
                     res: resolver.DiseaseResolution, version: str) -> None:
    """Rewire a raw Disease node into a Mention pointing at a canonical MONDO term."""
    if not res.resolved:
        c.run(
            "MATCH (d:Disease {entity_id:$id}) "
            "SET d.unlinked = true, d.unlink_evidence = $ev",
            id=old_entity_id, ev=res.evidence,
        )
        return

    c.run(
        """
        MATCH (old:Disease {entity_id:$old_id})
        MERGE (canon:ResolvedEntity:Disease:Provenanced
               {entity_id:'disease:mondo:' + $mondo_id})
          ON CREATE SET canon.name = $label, canon.mondo_id = $mondo_id,
                        canon.source = 'mondo', canon.source_version = $ver,
                        canon.retrieved_at = $at, canon.tier = 'ingested'
        MERGE (i:Identifier:Provenanced
               {authority:'mondo', value:$mondo_id, release:$ver})
          ON CREATE SET i.source = 'mondo', i.source_version = $ver,
                        i.retrieved_at = $at, i.tier = 'ingested'
        MERGE (canon)-[:HAS_IDENTIFIER]->(i)

        // the raw string becomes a Mention and stops being an entity
        SET old:Mention,
            old.raw_text = $raw, old.mention_type = 'condition',
            old.source_record_id = coalesce(old.source_record_id, $old_id)
        REMOVE old:ResolvedEntity, old:Disease
        MERGE (old)-[r:RESOLVES_TO]->(canon)
          SET r.method = $method, r.confidence = $conf, r.evidence = $ev,
              r.normalised = $norm, r.valid_from = $at, r.tier = 'derived',
              r.review_state = CASE WHEN $nr THEN 'needs_review' ELSE 'not_required' END

        // rewire the trials to the canonical disease, keeping the raw link
        WITH old, canon
        MATCH (t:ClinicalTrial)-[s:STUDIES]->(old)
        MERGE (t)-[:STUDIES]->(canon)
        MERGE (t)-[:MENTIONS]->(old)
        DELETE s
        """,
        old_id=old_entity_id, mondo_id=res.mondo_id, label=res.label, raw=raw,
        method=res.method.label, conf=res.confidence, ev=res.evidence,
        norm=res.normalised, nr=res.needs_review, ver=version, at=NOW.isoformat(),
    )

    if res.candidates:
        c.run(
            """
            MATCH (m:Mention {entity_id:$old_id})
            UNWIND $cands AS cd
              MERGE (cn:ResolvedEntity:Disease:Provenanced
                     {entity_id:'disease:mondo:' + cd.id})
                ON CREATE SET cn.name = cd.name, cn.mondo_id = cd.id,
                              cn.source='mondo', cn.source_version=$ver,
                              cn.retrieved_at=$at, cn.tier='ingested'
              MERGE (m)-[cr:CANDIDATE_FOR]->(cn)
                SET cr.score = cd.score, cr.rank = cd.rank, cr.tier='derived'
            """,
            old_id=old_entity_id, ver=version, at=NOW.isoformat(),
            cands=[{"id": x.rxcui, "name": x.name, "score": x.score, "rank": x.rank}
                   for x in res.candidates],
        )


def report_duplicates(c: GraphClient, term: str = "diabet") -> None:
    rows = c.run(
        """
        MATCH (d:Disease) WHERE toLower(coalesce(d.name,'')) CONTAINS $t
        OPTIONAL MATCH (:ClinicalTrial)-[:STUDIES]->(d)
        WITH d, count(*) AS trials
        RETURN d.name AS name, d.mondo_id AS mondo_id, trials
        ORDER BY trials DESC LIMIT 10
        """,
        t=term,
    )
    for r in rows:
        mid = r["mondo_id"] or "-"
        log(f"    {str(r['name'])[:44]:<44} {mid:<16} {r['trials']} trials")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=120)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = config.load()
    if not cfg.neo4j.configured:
        log("neo4j not configured")
        return 1

    version = mondo.ols_version()
    log(f"MONDO via OLS4, recorded version: {version}\n")

    with GraphClient(
        uri=cfg.neo4j.uri, username=cfg.neo4j.username,
        password=cfg.neo4j.password.reveal(), database=cfg.neo4j.database,
    ) as c:
        log("BEFORE — diabetes variants in the graph:")
        report_duplicates(c)

        todo = unresolved_diseases(c, args.limit)
        log(f"\nresolving {len(todo)} disease strings against MONDO ...")

        results: list[resolver.DiseaseResolution] = []
        for i, row in enumerate(todo, 1):
            raw = row["name"]
            if not raw:
                continue
            res = resolver.resolve_disease(raw)
            results.append(res)
            if not args.dry_run:
                apply_resolution(c, row["entity_id"], raw, res, version)
            if i % 20 == 0:
                log(f"    {i}/{len(todo)}")

        log("\nsample resolutions:")
        for res in results[:16]:
            log(f"    {res.describe()}")

        bands = Counter(r.method.label for r in results)
        resolved = sum(1 for r in results if r.resolved)
        review = sum(1 for r in results if r.needs_review)
        log(f"\n  attempted {len(results)}   resolved {resolved}   "
            f"needs_review {review}   unlinked {len(results) - resolved}")
        log("  confidence bands:")
        for method, n in bands.most_common():
            log(f"    {method:<34} {n}")

        if args.dry_run:
            log("\n(dry run — nothing written)")
            return 0

        log("\nAFTER — diabetes variants:")
        report_duplicates(c)

        log("\n  canonical diseases by trial count:")
        for r in c.run(
            """
            MATCH (t:ClinicalTrial)-[:STUDIES]->(d:Disease) WHERE d.mondo_id IS NOT NULL
            RETURN d.name AS name, d.mondo_id AS mondo_id, count(DISTINCT t) AS trials
            ORDER BY trials DESC LIMIT 10
            """
        ):
            log(f"    {str(r['name'])[:40]:<40} {r['mondo_id']:<16} {r['trials']} trials")

        collapse = c.run(
            """
            MATCH (m:Mention {mention_type:'condition'})-[:RESOLVES_TO]->(d:Disease)
            WITH d, count(m) AS variants WHERE variants > 1
            RETURN d.name AS name, d.mondo_id AS mondo_id, variants
            ORDER BY variants DESC LIMIT 12
            """
        )
        log("\n  COLLAPSED — canonical diseases with multiple surface forms:")
        for r in collapse:
            log(f"    {str(r['name'])[:40]:<40} {r['mondo_id']:<16} {r['variants']} variants")

        totals = c.run(
            """
            MATCH (d:Disease) WITH count(d) AS diseases
            MATCH (m:Mention {mention_type:'condition'}) WITH diseases, count(m) AS conditions
            MATCH (d2:Disease) WHERE d2.mondo_id IS NOT NULL
            RETURN diseases, conditions, count(d2) AS canonical
            """
        )[0]
        log(f"\n  disease nodes now {totals['diseases']} "
            f"({totals['canonical']} canonical MONDO terms) "
            f"from {totals['conditions']} raw condition strings")

        manifest = RunManifest.create(
            design={"task": "disease_resolution", "limit": args.limit},
            source_versions={"mondo": version, "ols4": "api"},
            trust_policy_version=cfg.trust.policy_version,
            notes={"resolved": str(resolved), "needs_review": str(review),
                   "bands": str(dict(bands))},
        )
        p = manifest.write(ROOT / "manifests")
        log(f"\nmanifest: {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
