"""Ingest real multi-source data into Neo4j — and LEAVE IT THERE.

Unlike the demos, this script does not clean up. It builds a persistent graph you
can browse in the Neo4j console.

    .venv/bin/python scripts/ingest.py                 # default slice
    .venv/bin/python scripts/ingest.py --condition melanoma --trials 80 --ae 80
    .venv/bin/python scripts/ingest.py --wipe          # clear and rebuild

Sources (all open, no API key needed):
    ClinicalTrials.gov v2 · openFDA FAERS · RxNav/RxNorm · HGNC · ChEMBL
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from biomedical.foundation import RunManifest  # noqa: E402
from biomedical.graph.store import GraphClient  # noqa: E402
from biomedical.sources import adapters, resolver  # noqa: E402

NOW = datetime.now(timezone.utc)
GENES = ["BRAF", "NRAS", "KIT", "PTEN", "TP53", "CDKN2A", "MITF", "TERT"]

SCHEMA = [
    "CREATE CONSTRAINT re_id IF NOT EXISTS FOR (e:ResolvedEntity) REQUIRE e.entity_id IS UNIQUE",
    "CREATE CONSTRAINT men_nat IF NOT EXISTS FOR (m:Mention) REQUIRE (m.source, m.source_record_id, m.raw_text) IS UNIQUE",
    "CREATE CONSTRAINT ident_nat IF NOT EXISTS FOR (i:Identifier) REQUIRE (i.authority, i.value, i.release) IS UNIQUE",
    "CREATE CONSTRAINT trial_nct IF NOT EXISTS FOR (t:ClinicalTrial) REQUIRE t.nct_id IS UNIQUE",
    "CREATE CONSTRAINT gene_hgnc IF NOT EXISTS FOR (g:Gene) REQUIRE g.hgnc_id IS UNIQUE",
    "CREATE CONSTRAINT aer_id IF NOT EXISTS FOR (a:AdverseEventReport) REQUIRE a.report_id IS UNIQUE",
    # Disease.name uniqueness was dropped: once raw condition strings resolve to
    # canonical MONDO terms, a canonical label can legitimately equal a raw
    # string. Canonical identity is entity_id (resolved_entity_id constraint).
    "CREATE CONSTRAINT rxn_term IF NOT EXISTS FOR (r:Reaction) REQUIRE r.term IS UNIQUE",
    "CREATE INDEX men_text IF NOT EXISTS FOR (m:Mention) ON (m.raw_text)",
]

PROV = "n.source = $source, n.source_version = $sv, n.retrieved_at = $at, n.tier = $tier"


def log(msg: str) -> None:
    print(msg, flush=True)


def ensure_schema(c: GraphClient) -> None:
    for stmt in SCHEMA:
        c.run(stmt)
    log(f"  schema: {c.constraint_count()} constraints")


def wipe(c: GraphClient) -> None:
    n = c.run("MATCH (n) RETURN count(n) AS c")[0]["c"]
    log(f"  wiping {n} existing nodes ...")
    while True:
        res = c.run("MATCH (n) WITH n LIMIT 10000 DETACH DELETE n RETURN count(n) AS c")
        if not res or res[0]["c"] == 0:
            break


# --------------------------------------------------------------------------- #


def load_trials(c: GraphClient, trials: list[adapters.TrialRecord]) -> int:
    rows = [
        {
            "nct_id": t.nct_id, "title": t.title, "phase": t.phase, "status": t.status,
            "enrollment": t.enrollment, "conditions": list(t.conditions),
            "interventions": list(t.interventions), "sv": t.source_version,
            "at": t.retrieved_at.isoformat(),
        }
        for t in trials
    ]
    c.run(
        """
        UNWIND $rows AS r
        MERGE (t:ResolvedEntity:ClinicalTrial:Provenanced {entity_id: 'trial:' + r.nct_id})
          SET t.nct_id = r.nct_id, t.title = r.title, t.phase = r.phase,
              t.status = r.status, t.enrollment = r.enrollment,
              t.source = 'clinicaltrials.gov', t.source_version = r.sv,
              t.retrieved_at = r.at, t.tier = 'ingested'
        WITH t, r
        UNWIND r.conditions AS cond
          MERGE (d:ResolvedEntity:Disease:Provenanced {entity_id: 'disease:' + toLower(cond)})
            SET d.name = cond, d.source = 'clinicaltrials.gov', d.source_version = r.sv,
                d.retrieved_at = r.at, d.tier = 'ingested'
          MERGE (t)-[:STUDIES]->(d)
        """,
        rows=rows,
    )
    # intervention strings become Mentions - raw, unmodified, awaiting resolution
    c.run(
        """
        UNWIND $rows AS r
        MATCH (t:ClinicalTrial {nct_id: r.nct_id})
        UNWIND r.interventions AS iv
          MERGE (m:Mention:Provenanced {source: 'clinicaltrials.gov',
                                        source_record_id: r.nct_id, raw_text: iv})
            SET m.mention_type = 'intervention', m.source_version = r.sv,
                m.retrieved_at = r.at, m.tier = 'ingested'
          MERGE (t)-[:MENTIONS]->(m)
        """,
        rows=rows,
    )
    return len(rows)


def load_adverse_events(c: GraphClient, aes: list[adapters.AeRecord]) -> int:
    rows = [
        {
            "report_id": a.report_id, "serious": a.serious,
            "drug_names": list(a.drug_names), "reactions": list(a.reactions),
            "sv": a.source_version, "at": a.retrieved_at.isoformat(),
        }
        for a in aes
    ]
    c.run(
        """
        UNWIND $rows AS r
        MERGE (a:AdverseEventReport:Provenanced {report_id: r.report_id})
          SET a.serious = r.serious, a.source = 'openfda.faers',
              a.source_version = r.sv, a.retrieved_at = r.at, a.tier = 'ingested'
        WITH a, r
        UNWIND r.reactions AS rx
          MERGE (x:Reaction:Provenanced {term: rx})
            SET x.source = 'openfda.faers', x.source_version = r.sv,
                x.retrieved_at = r.at, x.tier = 'ingested'
          MERGE (a)-[:REPORTS_REACTION]->(x)
        """,
        rows=rows,
    )
    c.run(
        """
        UNWIND $rows AS r
        MATCH (a:AdverseEventReport {report_id: r.report_id})
        UNWIND r.drug_names AS dn
          MERGE (m:Mention:Provenanced {source: 'openfda.faers',
                                        source_record_id: r.report_id, raw_text: dn})
            SET m.mention_type = 'medicinalproduct', m.source_version = r.sv,
                m.retrieved_at = r.at, m.tier = 'ingested'
          MERGE (a)-[:MENTIONS]->(m)
        """,
        rows=rows,
    )
    return len(rows)


def load_genes(c: GraphClient, genes: list[adapters.GeneRecord]) -> int:
    rows = [
        {
            "hgnc_id": g.hgnc_id, "symbol": g.symbol, "name": g.name,
            "prev": list(g.prev_symbols), "sv": g.source_version,
            "at": g.retrieved_at.isoformat(),
        }
        for g in genes
    ]
    c.run(
        """
        UNWIND $rows AS r
        MERGE (g:ResolvedEntity:Gene:Provenanced {entity_id: 'gene:' + r.symbol})
          SET g.hgnc_id = r.hgnc_id, g.symbol = r.symbol, g.name = r.name,
              g.source = 'hgnc', g.source_version = r.sv,
              g.retrieved_at = r.at, g.tier = 'ingested'
        MERGE (i:Identifier:Provenanced {authority:'hgnc', value:r.hgnc_id, release:r.sv})
          SET i.source = 'hgnc', i.source_version = r.sv, i.retrieved_at = r.at,
              i.tier = 'ingested'
        MERGE (g)-[:HAS_IDENTIFIER]->(i)
        WITH g, r
        UNWIND r.prev AS ps
          MERGE (old:Identifier:Provenanced {authority:'hgnc-symbol', value: ps, release:'historical'})
            SET old.source = 'hgnc', old.source_version = r.sv, old.retrieved_at = r.at,
                old.tier = 'ingested', old.withdrawn = true
          MERGE (old)-[:REPLACED_BY {change_kind:'renamed'}]->(g)
        """,
        rows=rows,
    )
    return len(rows)


def resolve_mentions(c: GraphClient, limit: int) -> dict:
    """Resolve raw drug mentions to RxNorm with method-derived confidence."""
    rows = c.run(
        """
        MATCH (m:Mention) WHERE NOT (m)-[:RESOLVES_TO]->()
        RETURN DISTINCT m.raw_text AS raw ORDER BY raw LIMIT $limit
        """,
        limit=limit,
    )
    raws = [r["raw"] for r in rows]
    log(f"  resolving {len(raws)} distinct mention strings against RxNorm ...")

    resolutions: list[resolver.Resolution] = []
    for i, raw in enumerate(raws, 1):
        res = resolver.resolve_drug(raw)
        resolutions.append(res)
        if i % 10 == 0:
            log(f"    {i}/{len(raws)}")

        payload = {
            "raw": res.raw, "norm": res.normalised, "method": res.method.label,
            "conf": res.confidence, "evidence": res.evidence,
            "needs_review": res.needs_review, "rxcui": res.rxcui,
            "canon": res.canonical_name, "at": NOW.isoformat(),
            "cands": [
                {"rxcui": x.rxcui, "name": x.name, "score": x.score, "rank": x.rank}
                for x in res.candidates
            ],
        }

        if res.rxcui:
            c.run(
                """
                MATCH (m:Mention {raw_text: $raw})
                MERGE (d:ResolvedEntity:Drug:Provenanced {entity_id: 'drug:rxcui:' + $rxcui})
                  SET d.canonical_name = coalesce($canon, d.canonical_name),
                      d.rxcui = $rxcui, d.source = 'rxnorm', d.source_version = 'rxnav',
                      d.retrieved_at = $at, d.tier = 'ingested'
                MERGE (i:Identifier:Provenanced {authority:'rxnorm', value:$rxcui, release:'rxnav'})
                  SET i.source = 'rxnorm', i.source_version = 'rxnav',
                      i.retrieved_at = $at, i.tier = 'ingested'
                MERGE (d)-[:HAS_IDENTIFIER]->(i)
                MERGE (m)-[r:RESOLVES_TO]->(d)
                  SET r.method = $method, r.confidence = $conf, r.evidence = $evidence,
                      r.review_state = CASE WHEN $needs_review THEN 'needs_review' ELSE 'not_required' END,
                      r.normalised = $norm, r.valid_from = $at, r.tier = 'derived'
                """,
                **payload,
            )
        else:
            c.run(
                "MATCH (m:Mention {raw_text: $raw}) "
                "SET m.unlinked = true, m.unlink_evidence = $evidence",
                raw=res.raw, evidence=res.evidence,
            )

        if res.candidates:
            c.run(
                """
                MATCH (m:Mention {raw_text: $raw})
                UNWIND $cands AS cd
                  MERGE (d:ResolvedEntity:Drug:Provenanced {entity_id:'drug:rxcui:' + cd.rxcui})
                    SET d.canonical_name = coalesce(d.canonical_name, cd.name),
                        d.rxcui = cd.rxcui, d.source='rxnorm', d.source_version='rxnav',
                        d.retrieved_at=$at, d.tier='ingested'
                  MERGE (m)-[cr:CANDIDATE_FOR]->(d)
                    SET cr.score = cd.score, cr.rank = cd.rank, cr.tier='derived'
                """,
                raw=res.raw, cands=payload["cands"], at=NOW.isoformat(),
            )

    log("\n  sample resolutions:")
    for res in resolutions[:12]:
        log(f"    {res.describe()}")

    report = resolver.evaluate_consistency(resolutions[:25])
    log("\n  " + report.describe().replace("\n", "\n  "))
    return {
        "attempted": len(resolutions),
        "resolved": sum(1 for r in resolutions if r.resolved),
        "needs_review": sum(1 for r in resolutions if r.needs_review),
        "bands": report.band_counts,
        "round_trip": f"{report.round_trip_agreed}/{report.round_trip_attempted}",
    }


def link_drugs_to_trials(c: GraphClient) -> int:
    rows = c.run(
        """
        MATCH (t:ClinicalTrial)-[:MENTIONS]->(m:Mention)-[:RESOLVES_TO]->(d:Drug)
        MERGE (t)-[:INVESTIGATES {tier:'derived', derivation:'trial_mention_resolution'}]->(d)
        RETURN count(*) AS c
        """
    )
    return rows[0]["c"] if rows else 0


def link_drugs_to_reactions(c: GraphClient) -> int:
    rows = c.run(
        """
        MATCH (a:AdverseEventReport)-[:MENTIONS]->(m:Mention)-[:RESOLVES_TO]->(d:Drug)
        MATCH (a)-[:REPORTS_REACTION]->(x:Reaction)
        MERGE (d)-[r:ASSOCIATED_WITH_REACTION {tier:'derived',
               derivation:'faers_coreport'}]->(x)
          ON CREATE SET r.report_count = 1
          ON MATCH  SET r.report_count = r.report_count + 1
        RETURN count(*) AS c
        """
    )
    return rows[0]["c"] if rows else 0


def summarise(c: GraphClient) -> None:
    log("\n" + "=" * 70)
    log("GRAPH SUMMARY")
    log("=" * 70)
    for q, title in [
        ("MATCH (n) RETURN count(n) AS v", "total nodes"),
        ("MATCH ()-[r]->() RETURN count(r) AS v", "total relationships"),
    ]:
        log(f"  {title:<28} {c.run(q)[0]['v']}")

    log("\n  nodes by label:")
    for r in c.run(
        "MATCH (n) UNWIND labels(n) AS l "
        "WITH l, count(*) AS c WHERE NOT l IN ['Provenanced','ResolvedEntity'] "
        "RETURN l, c ORDER BY c DESC"
    ):
        log(f"    {r['l']:<24} {r['c']}")

    log("\n  relationships by type:")
    for r in c.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC"):
        log(f"    {r['t']:<24} {r['c']}")

    log("\n  provenance tiers:")
    for r in c.run(
        "MATCH (n:Provenanced) RETURN n.tier AS tier, count(*) AS c ORDER BY tier"
    ):
        log(f"    {r['tier']:<24} {r['c']}")

    log("\n  sources:")
    for r in c.run(
        "MATCH (n:Provenanced) WHERE n.source IS NOT NULL "
        "RETURN n.source AS s, n.source_version AS v, count(*) AS c ORDER BY c DESC"
    ):
        log(f"    {r['s']:<24} {str(r['v'])[:12]:<12} {r['c']}")

    log("\n  resolution confidence bands:")
    for r in c.run(
        "MATCH ()-[r:RESOLVES_TO]->() RETURN r.method AS m, count(*) AS c, "
        "round(avg(r.confidence),2) AS conf ORDER BY c DESC"
    ):
        log(f"    {str(r['m']):<34} n={r['c']:<4} conf={r['conf']}")

    backlog = c.run(
        "MATCH ()-[r:RESOLVES_TO]->() WHERE r.review_state='needs_review' RETURN count(*) AS c"
    )[0]["c"]
    unlinked = c.run("MATCH (m:Mention) WHERE m.unlinked RETURN count(*) AS c")[0]["c"]
    log(f"\n  review backlog (needs_review)  {backlog}")
    log(f"  unlinked mentions              {unlinked}")
    log("  (both published deliberately - a visible backlog is more credible than none)")

    log("\n  try these in the Neo4j console:")
    log("    MATCH (t:ClinicalTrial)-[:INVESTIGATES]->(d:Drug) RETURN t,d LIMIT 25")
    log("    MATCH (m:Mention)-[r:RESOLVES_TO]->(d:Drug) "
        "WHERE r.review_state='needs_review' RETURN m,r,d LIMIT 25")
    log("    MATCH (m:Mention)-[c:CANDIDATE_FOR]->(d:Drug) RETURN m,c,d LIMIT 50")
    log("    MATCH (old:Identifier {authority:'hgnc-symbol'})-[:REPLACED_BY]->(g:Gene) RETURN old,g")
    log("    MATCH (d:Drug)-[a:ASSOCIATED_WITH_REACTION]->(x:Reaction) "
        "RETURN d.canonical_name, x.term, a.report_count ORDER BY a.report_count DESC LIMIT 20")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", default="melanoma")
    ap.add_argument("--trials", type=int, default=60)
    ap.add_argument("--ae", type=int, default=60)
    ap.add_argument("--resolve", type=int, default=45)
    ap.add_argument("--wipe", action="store_true")
    args = ap.parse_args()

    cfg = config.load()
    if not cfg.neo4j.configured:
        log("neo4j not configured")
        return 1

    log(f"ingesting slice: condition={args.condition!r} trials<={args.trials} ae<={args.ae}")
    log(f"rxnorm version: {adapters.rxnorm_version()}\n")

    log("fetching from live sources ...")
    trials = adapters.fetch_trials(args.condition, limit=args.trials)
    log(f"  clinicaltrials.gov: {len(trials)} trials")
    aes = adapters.fetch_adverse_events(
        f'patient.drug.openfda.pharm_class_epc:"antineoplastic*"', limit=args.ae
    ) if False else adapters.fetch_adverse_events(
        f'patient.reaction.reactionmeddrapt:"{args.condition}"', limit=args.ae
    )
    log(f"  openfda faers:      {len(aes)} reports")
    genes = adapters.fetch_genes(GENES)
    log(f"  hgnc:               {len(genes)} genes")

    with GraphClient(
        uri=cfg.neo4j.uri, username=cfg.neo4j.username,
        password=cfg.neo4j.password.reveal(), database=cfg.neo4j.database,
    ) as c:
        log("\npreparing graph ...")
        if args.wipe:
            wipe(c)
        ensure_schema(c)

        log("\nloading ...")
        log(f"  trials:  {load_trials(c, trials)}")
        log(f"  reports: {load_adverse_events(c, aes)}")
        log(f"  genes:   {load_genes(c, genes)}")

        log("\nentity resolution (RxNorm) ...")
        stats = resolve_mentions(c, args.resolve)

        log("\nderiving relationships ...")
        log(f"  trial -> drug:    {link_drugs_to_trials(c)}")
        log(f"  drug  -> reaction:{link_drugs_to_reactions(c)}")

        summarise(c)

        manifest = RunManifest.create(
            design={"condition": args.condition, "trials": args.trials, "ae": args.ae},
            source_versions={
                "clinicaltrials.gov": "v2",
                "openfda.faers": "faers",
                "rxnorm": adapters.rxnorm_version(),
                "hgnc": "hgnc-current",
            },
            slice_params={"condition": args.condition},
            trust_policy_version=cfg.trust.policy_version,
            notes={"resolution": str(stats)},
        )
        p = manifest.write(ROOT / "manifests")
        log(f"\nmanifest: {p.relative_to(ROOT)}")
        log("graph PERSISTED - not cleaned up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
