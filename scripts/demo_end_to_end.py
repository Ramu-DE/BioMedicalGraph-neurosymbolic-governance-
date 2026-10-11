"""End-to-end demonstration against live Neo4j.

Reproduces v1's strongest case — the catalog asserts `certified: true` while the
governing policy is violated — but now through the component 00 trust boundary,
so the verdict records what it relied on *and what it was denied*.

    .venv/bin/python scripts/demo_end_to_end.py

Writes to a `demo:` entity namespace and cleans up after itself.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from biomedical.foundation import (  # noqa: E402
    Fact,
    ResolutionMethod,
    ReviewState,
    RuleOutcome,
    RuleResult,
    Severity,
    Tier,
    Timings,
    TrustPolicy,
    ValidityInterval,
    Verdict,
    asserted,
    assemble,
    derived,
    ingested,
)
from biomedical.graph.store import FactStore, GraphClient  # noqa: E402

NOW = datetime.now(timezone.utc)
ENTITY = "demo:table:CustomerMaster"
PATIENT = "demo:patient:PAT015"
QUESTION = (
    "Certify the Customer Master dataset so I can publish PAT015's "
    "Pembrolizumab response for Melanoma in the Q3 regulatory report."
)


def seed_facts() -> list[Fact]:
    """Four facts spanning three tiers and three eligibility outcomes."""
    return [
        # the catalog's own assertion — eligible, and it says everything is fine
        Fact(
            key="certified", value="true", entity_id=ENTITY,
            stamp=ingested("catalog", "2026-09", NOW),
            validity=ValidityInterval(valid_from=NOW - timedelta(days=5)),
            recorded_at=NOW,
        ),
        # the fact the catalog does not know about — eligible, and it contradicts the above
        Fact(
            key="dq_rule_count", value="0", entity_id=ENTITY,
            stamp=ingested("catalog", "2026-09", NOW),
            validity=ValidityInterval(valid_from=NOW - timedelta(days=5)),
            recorded_at=NOW,
        ),
        # authored synthetic consent — eligible
        Fact(
            key="consent_status", value="Withdrawn", entity_id=PATIENT,
            stamp=asserted("authored", "synthetic-v1", NOW),
            validity=ValidityInterval(valid_from=NOW - timedelta(days=200)),
            recorded_at=NOW,
        ),
        # a low-confidence derived mapping — INELIGIBLE, awaiting review
        Fact(
            key="rxcui", value="1657993", entity_id=PATIENT,
            stamp=derived(
                "resolver", "1.0", NOW, ResolutionMethod.MULTI_CANDIDATE,
                evidence="'pembrolizumab 100mg' matched 3 RxNorm candidates",
            ),
            validity=ValidityInterval(valid_from=NOW - timedelta(days=1)),
            recorded_at=NOW,
            review_state=ReviewState.NEEDS_REVIEW,
        ),
    ]


def evaluate_rules(bundle) -> tuple[RuleResult, ...]:
    """Three rules. Each reads only the bundle — none holds a store client."""
    results: list[RuleResult] = []

    # 1. POL_HUB_DQ : a hub table must carry at least one critical DQ rule
    found, missing = bundle.require("dq_rule_count")
    if missing:
        results.append(RuleResult(
            "governance.hub_table_without_dq_rule", RuleOutcome.CANNOT_EVALUATE,
            Severity.BLOCK, missing_dependencies=tuple(missing)))
    else:
        violated = int(found[0].value) == 0
        results.append(RuleResult(
            "governance.hub_table_without_dq_rule",
            RuleOutcome.VIOLATION if violated else RuleOutcome.NO_VIOLATION,
            Severity.BLOCK,
            message="zero DQ rules exist for a hub-layer table",
            policy_iri="graph:Policy/POL_HUB_DQ",
            facts_relied_on=tuple(__import__("biomedical.foundation", fromlist=["FactReference"])
                                  .FactReference.of(f) for f in found),
        ))

    # 2. POL001 : withdrawn consent excludes a patient from analysis
    found, missing = bundle.require("consent_status")
    if missing:
        results.append(RuleResult(
            "consent.withdrawn_patient_data", RuleOutcome.CANNOT_EVALUATE,
            Severity.BLOCK, missing_dependencies=tuple(missing)))
    else:
        violated = str(found[0].value).lower() == "withdrawn"
        results.append(RuleResult(
            "consent.withdrawn_patient_data",
            RuleOutcome.VIOLATION if violated else RuleOutcome.NO_VIOLATION,
            Severity.BLOCK,
            message="consent withdrawn; data must be excluded",
            policy_iri="bio:DataGovernancePolicy/POL001",
        ))

    # 3. a rule whose dependency the trust boundary withheld
    found, missing = bundle.require("rxcui")
    if missing:
        results.append(RuleResult(
            "safety.drug_without_external_identifier", RuleOutcome.CANNOT_EVALUATE,
            Severity.WARN,
            message="drug identifier unavailable at the required confidence",
            missing_dependencies=tuple(missing)))
    else:
        results.append(RuleResult(
            "safety.drug_without_external_identifier",
            RuleOutcome.NO_VIOLATION, Severity.WARN))

    return tuple(results)


def main() -> int:
    cfg = config.load()
    if not cfg.neo4j.configured:
        print("neo4j not configured; nothing to demonstrate")
        return 1

    policy = TrustPolicy(
        version=cfg.trust.policy_version,
        confidence_floor=cfg.trust.confidence_floor,
        min_tier=Tier(cfg.trust.min_tier),
    )

    with GraphClient(
        uri=cfg.neo4j.uri,
        username=cfg.neo4j.username,
        password=cfg.neo4j.password.reveal(),
        database=cfg.neo4j.database,
    ) as client:
        print("migrating schema ...")
        applied = client.migrate()
        print(f"  {len(applied)} constraints/indexes ensured; "
              f"{client.constraint_count()} total constraints\n")

        store = FactStore(client)

        # --- write -------------------------------------------------------
        print("appending facts ...")
        for fact in seed_facts():
            vid = store.append(fact)
            print(f"  {vid}  {fact.describe()}")

        # --- supersession demo -------------------------------------------
        print("\nsuperseding the catalog's certification claim ...")
        hist_before = store.history(ENTITY, "certified")
        new_id = store.supersede(
            hist_before[0].version_id,
            Fact(
                key="certified", value="false", entity_id=ENTITY,
                stamp=ingested("catalog", "2026-10", NOW),
                validity=ValidityInterval(valid_from=NOW),
                recorded_at=NOW,
            ),
            at=NOW,
        )
        hist_after = store.history(ENTITY, "certified")
        print(f"  versions now: {len(hist_after)} (both retained, prior closed)")
        for h in hist_after:
            print(f"    {h.value!r:8} {h.validity.describe()}")
        past = store.at(ENTITY, "certified", NOW - timedelta(days=1))
        print(f"  point-in-time yesterday: certified={past.value!r}" if past else "  (none)")

        # --- read through the trust boundary -----------------------------
        print("\nassembling the fact bundle through the trust boundary ...")
        t0 = time.perf_counter()
        candidates = store.candidates(ENTITY) + store.candidates(PATIENT)
        facts_ms = (time.perf_counter() - t0) * 1000

        bundle = assemble(candidates, policy, NOW)
        print(f"  {bundle.describe()}")

        # --- evaluate ----------------------------------------------------
        t1 = time.perf_counter()
        results = evaluate_rules(bundle)
        rules_ms = (time.perf_counter() - t1) * 1000

        verdict = Verdict(
            question=QUESTION,
            results=results,
            facts_relied_on=bundle.references(),
            facts_withheld=bundle.withheld,
            trust_policy_version=bundle.trust_policy_version,
            source_versions=bundle.source_versions(),
            timings=Timings.of(facts_ms=facts_ms, rules_ms=rules_ms),
            decided_at=NOW,
            bundle_hash=bundle.content_hash,
        )

        print("\n" + "=" * 72)
        print(verdict.describe())
        print("=" * 72)
        print(f"\ntier counts in graph: {store.tier_counts()}")

        # --- cleanup -----------------------------------------------------
        removed = client.run(
            "MATCH (e:ResolvedEntity) WHERE e.entity_id STARTS WITH 'demo:' "
            "OPTIONAL MATCH (e)-[:ASSERTS]->(f:FactVersion) "
            "DETACH DELETE e, f RETURN count(*) AS c"
        )
        print(f"cleanup: removed {removed[0]['c']} demo nodes")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
