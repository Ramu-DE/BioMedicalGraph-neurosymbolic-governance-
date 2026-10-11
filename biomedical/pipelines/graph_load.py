"""Graph loading pipelines, extracted from the ingest driver.

These were inline in `scripts/ingest.py`. They live here so notebooks can import
them instead of restating them, and so they are importable and testable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..graph.store import GraphClient
from ..sources import adapters

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731

GENES = ["BRAF", "NRAS", "KIT", "PTEN", "TP53", "CDKN2A", "MITF", "TERT"]

def _log(msg: str) -> None:
    print(msg, flush=True)


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

def ensure_schema(c: GraphClient) -> None:
    for stmt in SCHEMA:
        c.run(stmt)
    _log(f"  schema: {c.constraint_count()} constraints")


def wipe(c: GraphClient) -> None:
    n = c.run("MATCH (n) RETURN count(n) AS c")[0]["c"]
    _log(f"  wiping {n} existing nodes ...")
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
    _log(f"  resolving {len(raws)} distinct mention strings against RxNorm ...")

    resolutions = []
    for i, raw in enumerate(raws, 1):
        from ..sources import resolver
        res = resolver.resolve_drug(raw)
        resolutions.append(res)
        if i % 10 == 0:
            _log(f"    {i}/{len(raws)}")

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

    _log("\n  sample resolutions:")
    for res in resolutions[:12]:
        _log(f"    {res.describe()}")

    from ..sources import resolver as _r
    report = _r.evaluate_consistency(resolutions[:25])
    _log("\n  " + report.describe().replace("\n", "\n  "))
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


