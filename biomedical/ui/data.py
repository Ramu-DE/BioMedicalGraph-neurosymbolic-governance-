"""Read-only data access for the UI, plus the one writing surface (review).

The UI computes nothing. Every statistic shown is read from a manifest or from
the graph; the UI may not derive one.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import config
from ..foundation import (
    Claim,
    ClaimCategory,
    ClaimsRegister,
)
from ..graph.store import GraphClient

ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = ROOT / "manifests"


def client() -> GraphClient:
    cfg = config.load()
    return GraphClient(
        uri=cfg.neo4j.uri,
        username=cfg.neo4j.username,
        password=cfg.neo4j.password.reveal(),
        database=cfg.neo4j.database,
    )


def graph_available() -> bool:
    return config.load().neo4j.configured


def q(cypher: str, **params) -> list[dict]:
    with client() as c:
        return c.run(cypher, **params)


# --------------------------------------------------------------------------- #
# overview
# --------------------------------------------------------------------------- #


def overview() -> dict:
    with client() as c:
        return {
            "nodes": c.run("MATCH (n) RETURN count(n) AS v")[0]["v"],
            "rels": c.run("MATCH ()-[r]->() RETURN count(r) AS v")[0]["v"],
            "labels": c.run(
                "MATCH (n) UNWIND labels(n) AS l WITH l, count(*) AS c "
                "WHERE NOT l IN ['Provenanced','ResolvedEntity'] "
                "RETURN l AS label, c AS count ORDER BY c DESC"
            ),
            "tiers": c.run(
                "MATCH (n:Provenanced) RETURN n.tier AS tier, count(*) AS count ORDER BY tier"
            ),
            "sources": c.run(
                "MATCH (n:Provenanced) WHERE n.source IS NOT NULL "
                "RETURN n.source AS source, n.source_version AS version, count(*) AS count "
                "ORDER BY count DESC"
            ),
            "rels_by_type": c.run(
                "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count ORDER BY count DESC"
            ),
        }


def resolution_bands() -> list[dict]:
    return q(
        "MATCH ()-[r:RESOLVES_TO]->() WHERE coalesce(r.superseded,false) = false "
        "RETURN r.method AS method, count(*) AS count, round(avg(r.confidence),2) AS confidence "
        "ORDER BY count DESC"
    )


# A superseded resolution keeps its historical review_state - that is the point of
# append-only storage. But the *backlog* is about current state, so every count
# below excludes superseded edges.
CURRENT = "coalesce(r.superseded, false) = false"


def backlog_counts() -> dict:
    with client() as c:
        total = c.run(f"MATCH ()-[r:RESOLVES_TO]->() WHERE {CURRENT} RETURN count(*) AS v")[0]["v"]
        review = c.run(
            f"MATCH ()-[r:RESOLVES_TO]->() WHERE {CURRENT} AND r.review_state='needs_review' "
            "RETURN count(*) AS v"
        )[0]["v"]
        unlinked = c.run("MATCH (m:Mention) WHERE m.unlinked RETURN count(*) AS v")[0]["v"]
        cleared = c.run(
            f"MATCH ()-[r:RESOLVES_TO]->() WHERE {CURRENT} AND r.review_state='cleared' "
            "RETURN count(*) AS v"
        )[0]["v"]
        rejected = c.run(
            f"MATCH ()-[r:RESOLVES_TO]->() WHERE {CURRENT} AND r.review_state='rejected' "
            "RETURN count(*) AS v"
        )[0]["v"]
        superseded = c.run(
            "MATCH ()-[r:RESOLVES_TO]->() WHERE r.superseded = true RETURN count(*) AS v"
        )[0]["v"]
    return {
        "resolved": total, "needs_review": review, "unlinked": unlinked,
        "cleared": cleared, "rejected": rejected, "superseded": superseded,
    }


# --------------------------------------------------------------------------- #
# review queue - the only writing surface
# --------------------------------------------------------------------------- #


def review_queue(limit: int = 50) -> list[dict]:
    return q(
        """
        MATCH (m:Mention)-[r:RESOLVES_TO]->(d:Drug)
        WHERE r.review_state = 'needs_review' AND coalesce(r.superseded,false) = false
        RETURN m.raw_text AS raw, m.source AS source, m.mention_type AS kind,
               r.confidence AS confidence, r.method AS method, r.evidence AS evidence,
               r.normalised AS normalised, d.entity_id AS entity_id,
               d.canonical_name AS canonical, d.rxcui AS rxcui
        ORDER BY r.confidence ASC, raw LIMIT $limit
        """,
        limit=limit,
    )


def candidates_for(raw: str) -> list[dict]:
    return q(
        """
        MATCH (m:Mention {raw_text:$raw})-[c:CANDIDATE_FOR]->(d:Drug)
        RETURN d.entity_id AS entity_id, d.canonical_name AS canonical,
               d.rxcui AS rxcui, c.score AS score, c.rank AS rank
        ORDER BY c.rank
        """,
        raw=raw,
    )


def candidates_bulk(raws: list[str]) -> dict[str, list[dict]]:
    """One round-trip for the whole queue. Per-row queries made /review
    unusably slow at 60 rows."""
    if not raws:
        return {}
    rows = q(
        """
        UNWIND $raws AS raw
        MATCH (m:Mention {raw_text: raw})-[c:CANDIDATE_FOR]->(d:Drug)
        RETURN raw AS raw, d.entity_id AS entity_id, d.canonical_name AS canonical,
               d.rxcui AS rxcui, c.score AS score, c.rank AS rank
        ORDER BY raw, c.rank
        """,
        raws=raws,
    )
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["raw"], []).append(r)
    return out


def record_review(raw: str, entity_id: str | None, decision: str, reviewer: str) -> None:
    """Supersede the prior resolution; never overwrite it (component 01 R2.6)."""
    now = datetime.now(timezone.utc).isoformat()
    if decision == "accept" and entity_id:
        q(
            """
            MATCH (m:Mention {raw_text:$raw})-[old:RESOLVES_TO]->()
              SET old.valid_to = $now, old.superseded = true
            WITH m
            MATCH (d:Drug {entity_id:$eid})
            MERGE (m)-[r:RESOLVES_TO {review_state:'cleared'}]->(d)
              SET r.method='human_review', r.confidence=1.0,
                  r.evidence='accepted by ' + $rev + ' at ' + $now,
                  r.valid_from=$now, r.tier='derived', r.reviewer=$rev
            """,
            raw=raw, eid=entity_id, now=now, rev=reviewer,
        )
    else:
        q(
            """
            MATCH (m:Mention {raw_text:$raw})-[r:RESOLVES_TO]->()
              SET r.review_state='rejected', r.reviewer=$rev,
                  r.evidence = r.evidence + ' | rejected by ' + $rev + ' at ' + $now
            """,
            raw=raw, rev=reviewer, now=now,
        )


# --------------------------------------------------------------------------- #
# provenance explorer
# --------------------------------------------------------------------------- #


def search_entities(term: str, limit: int = 40) -> list[dict]:
    return q(
        """
        MATCH (e:ResolvedEntity)
        WHERE toLower(coalesce(e.canonical_name,'') + coalesce(e.name,'') +
                      coalesce(e.symbol,'') + coalesce(e.nct_id,'') +
                      coalesce(e.title,'')) CONTAINS toLower($term)
        RETURN e.entity_id AS entity_id, labels(e) AS labels,
               coalesce(e.canonical_name, e.name, e.symbol, e.nct_id) AS name,
               e.source AS source, e.source_version AS version, e.tier AS tier
        LIMIT $limit
        """,
        term=term, limit=limit,
    )


def entity_detail(entity_id: str) -> dict:
    with client() as c:
        node = c.run(
            "MATCH (e:ResolvedEntity {entity_id:$id}) RETURN properties(e) AS p, labels(e) AS l",
            id=entity_id,
        )
        if not node:
            return {}
        return {
            "props": node[0]["p"],
            "labels": [x for x in node[0]["l"] if x not in ("Provenanced", "ResolvedEntity")],
            "identifiers": c.run(
                "MATCH (e:ResolvedEntity {entity_id:$id})-[:HAS_IDENTIFIER]->(i:Identifier) "
                "RETURN i.authority AS authority, i.value AS value, i.release AS release, "
                "i.tier AS tier",
                id=entity_id,
            ),
            "mentions": c.run(
                """
                MATCH (m:Mention)-[r:RESOLVES_TO]->(e:ResolvedEntity {entity_id:$id})
                RETURN m.raw_text AS raw, m.source AS source, r.method AS method,
                       r.confidence AS confidence, r.review_state AS review_state,
                       r.evidence AS evidence
                ORDER BY r.confidence DESC LIMIT 30
                """,
                id=entity_id,
            ),
            "out": c.run(
                "MATCH (e:ResolvedEntity {entity_id:$id})-[r]->(o) "
                "WHERE NOT type(r)='HAS_IDENTIFIER' "
                "RETURN type(r) AS type, coalesce(o.canonical_name,o.name,o.term,o.nct_id,o.symbol) "
                "AS target, r.tier AS tier, r.derivation AS derivation LIMIT 40",
                id=entity_id,
            ),
            "incoming": c.run(
                "MATCH (s)-[r]->(e:ResolvedEntity {entity_id:$id}) "
                "WHERE NOT s:Mention "
                "RETURN type(r) AS type, coalesce(s.nct_id,s.canonical_name,s.name,s.report_id) "
                "AS source_node, r.tier AS tier LIMIT 40",
                id=entity_id,
            ),
        }


# --------------------------------------------------------------------------- #
# schema evolution
# --------------------------------------------------------------------------- #


def rule_integrity() -> dict:
    """Live rule-identifier integrity: which rules a vocabulary change broke."""
    from ..graph.release_diff import diff_from_graph
    from ..graph.rule_integrity import (
        EvaluationContext,
        check_integrity,
        evaluate_rule,
        load_rules,
        propose_remediations,
    )

    rules = load_rules()
    with client() as c:
        diff = diff_from_graph(c)
        current = {r["s"] for r in c.run("MATCH (g:Gene) RETURN g.symbol AS s")}
        trials = {
            r["id"]: {"phase": r["phase"]}
            for r in c.run("MATCH (t:ClinicalTrial) RETURN t.nct_id AS id, t.phase AS phase LIMIT 50")
        }

    findings = check_integrity(rules, diff)
    historical = set(current) | set(diff.moved_identifiers())
    ctx_before = EvaluationContext(gene_symbols=historical, trial_properties=trials)
    ctx_after = EvaluationContext(gene_symbols=current, trial_properties=trials)

    runs = []
    for f in findings:
        rule = next(r for r in rules if r.rule_id == f.rule_id)
        runs.append({
            "rule_id": rule.rule_id,
            "authored": rule.authored,
            "severity": rule.severity.value,
            "identifier": f"{f.authority}:{f.identifier}",
            "change": f.change.describe(),
            "recoverable": f.recoverable,
            "before": evaluate_rule(rule, ctx_before).outcome.value,
            "after_unchecked": evaluate_rule(rule, ctx_after).outcome.value,
            "after_checked": evaluate_rule(rule, ctx_after, integrity=findings).outcome.value,
        })

    return {
        "rules_checked": len(rules),
        "diff_counts": diff.counts(),
        "moved": len(diff.moved_identifiers()),
        "orphans": len(diff.orphans()),
        "findings": runs,
        "proposals": [p.describe() for p in propose_remediations(findings)],
    }


def conflicts(limit: int = 60) -> dict:
    """Persisted cross-source contradictions."""
    with client() as c:
        rows = c.run(
            """
            MATCH (cf:Conflict)
            RETURN cf.conflict_id AS id, cf.kind AS kind, cf.subject AS subject,
                   cf.property AS property, cf.note AS note, cf.sides AS sides,
                   cf.requires_human AS requires_human,
                   cf.auto_resolvable AS auto_resolvable,
                   cf.chosen_source AS chosen_source,
                   cf.resolution_authority AS authority,
                   cf.detected_at AS detected_at
            ORDER BY cf.requires_human DESC, cf.kind, cf.subject LIMIT $limit
            """,
            limit=limit,
        )
        by_kind = c.run(
            "MATCH (cf:Conflict) RETURN cf.kind AS kind, count(*) AS count "
            "ORDER BY count DESC"
        )
        contested = c.run(
            "MATCH ()-[r:RESOLVES_TO]->() WHERE r.contested RETURN count(r) AS v"
        )[0]["v"]
        unresolved = c.run(
            "MATCH (cf:Conflict) WHERE cf.chosen_source IS NULL RETURN count(*) AS v"
        )[0]["v"]
    return {
        "rows": rows, "by_kind": by_kind,
        "contested_resolutions": contested, "unresolved": unresolved,
        "total": sum(k["count"] for k in by_kind),
    }


def symbol_renames() -> list[dict]:
    return q(
        """
        MATCH (old:Identifier {authority:'hgnc-symbol'})-[r:REPLACED_BY]->(g:Gene)
        RETURN old.value AS was, g.symbol AS now, g.name AS gene_name,
               g.hgnc_id AS hgnc_id, r.change_kind AS change_kind,
               g.source_version AS release
        ORDER BY now, was
        """
    )


# --------------------------------------------------------------------------- #
# evaluation reports
# --------------------------------------------------------------------------- #


def eval_reports() -> list[Path]:
    return sorted(MANIFESTS.glob("*_report.json"), reverse=True)


def load_eval(path: Path) -> dict:
    return json.loads(path.read_text())


# --------------------------------------------------------------------------- #
# claims register
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def claims() -> ClaimsRegister:
    reg = ClaimsRegister.empty()
    for c in (
        Claim(
            claim_id="agreement-illusion",
            claim="An unanchored LLM judge behaved as a constant-FAIL predictor on this set",
            category=ClaimCategory.ACCURACY,
            evidence="manifests/*_report.json (v1 module E, recomputed)",
            n=12,
            interval="TNR 0.000 [0.000, 0.434]",
            does_not_establish=(
                "That LLM judges lack predictive value in general. One domain, one model, "
                "one prompt style, and only 5 true-negative opportunities."
            ),
        ),
        Claim(
            claim_id="evidence-access",
            claim="Giving the judge the same graph facts the rules get raised kappa from 0.273 to 0.833",
            category=ClaimCategory.ACCURACY,
            evidence="manifests/run_20261011_093151_5b859c_report.json",
            n=12,
            interval="kappa 0.833 [0.437, 1.000] vs 0.273 [-0.286, 0.800]",
            does_not_establish=(
                "That rules and judges are equivalent. The blind arm's interval includes "
                "zero, so even its point estimate is not distinguishable from no skill."
            ),
        ),
        Claim(
            claim_id="governance-latency",
            claim="End-to-end governance latency against Aura was 389 ms",
            category=ClaimCategory.PERFORMANCE,
            evidence="scripts/demo_end_to_end.py, single run",
            n=1,
            does_not_establish=(
                "A latency distribution. Single run, shared infrastructure, one region. "
                "Rule evaluation alone was 0.05 ms, which is NOT the cost of a verdict."
            ),
        ),
        Claim(
            claim_id="resolution-backlog",
            claim="48% of drug-name resolutions from real sources required human review",
            category=ClaimCategory.ACCURACY,
            evidence="GRAPH.md, 189 resolutions over 7 condition slices",
            n=189,
            does_not_establish=(
                "A resolution accuracy figure. No gold standard exists for these strings; "
                "round-trip agreement ranged 57%-93% by slice and detects only definite errors."
            ),
        ),
        Claim(
            claim_id="faers-coreport",
            claim="FAERS co-reporting edges link drugs to reactions appearing in the same report",
            category=ClaimCategory.SAFETY,
            evidence="GRAPH.md, ASSOCIATED_WITH_REACTION edges",
            n=85,
            does_not_establish=(
                "Any causal association. FAERS is spontaneous reporting with no denominator, "
                "no control group and heavy reporting bias. These edges are not evidence of harm."
            ),
        ),
        Claim(
            claim_id="rules-no-prompt",
            claim="The rule engine reads graph facts and has no access to retrieved text",
            category=ClaimCategory.ROBUSTNESS,
            evidence="tests/unit/test_isolation_and_interface.py",
            n=None,
            does_not_establish=(
                "Robustness to attacks not tested. It shows the rules cannot be influenced "
                "by the prompt; it does not show the verdicts are correct."
            ),
        ),
        Claim(
            claim_id="schema-evolution",
            claim="Seven real HGNC symbol renames are recorded as REPLACED_BY edges",
            category=ClaimCategory.SCALE,
            evidence="GRAPH.md, HGNC current release",
            n=7,
            does_not_establish=(
                "A rate of governance breakage. It shows the mechanism exists for the eight "
                "genes ingested, not how often rules break in practice."
            ),
        ),
        Claim(
            claim_id="graph-scale",
            claim="The graph holds 6,854 nodes from four independent live sources",
            category=ClaimCategory.SCALE,
            evidence="GRAPH.md, manifests/run_20261011_094612_d8dfd9.json",
            n=6854,
            does_not_establish=(
                "That it meets the 100k-entity target in the spec, or that disease identity "
                "is resolved - it is not: 'Type 2 Diabetes' and 'Diabetes Mellitus, Type 2' "
                "are still two nodes."
            ),
        ),
    ):
        reg.add(c)
    return reg
