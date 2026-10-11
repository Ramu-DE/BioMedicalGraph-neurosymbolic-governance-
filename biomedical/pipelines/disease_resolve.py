"""Disease-resolution pipeline, extracted from the driver.

Rewires a raw ClinicalTrials.gov condition string into a `:Mention` pointing at
a canonical MONDO `:ResolvedEntity:Disease`, and moves the `STUDIES` edges with
it. The raw string is never discarded.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..graph.store import GraphClient
from ..sources import mondo, resolver

NOW = datetime.now(timezone.utc)


def _log(msg: str) -> None:
    print(msg, flush=True)


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
        _log(f"    {str(r['name'])[:44]:<44} {mid:<16} {r['trials']} trials")


