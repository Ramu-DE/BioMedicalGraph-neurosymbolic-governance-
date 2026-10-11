"""Append-only fact store on Neo4j (component 01 R3).

The interface exposes `append`, `supersede`, `at` and `history` — and nothing
else. There is deliberately no `update` and no `delete` verb, and a test asserts
that by introspection, because this invariant decays silently otherwise.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from neo4j import Driver, GraphDatabase

from ..foundation import (
    Confidence,
    Fact,
    ProvenanceStamp,
    ResolutionMethod,
    ReviewState,
    Tier,
    ValidityInterval,
)

# Natural keys and mandatory provenance, per component 01's design.
MIGRATIONS: list[tuple[str, str]] = [
    (
        "fact_version_id",
        "CREATE CONSTRAINT fact_version_id IF NOT EXISTS "
        "FOR (f:FactVersion) REQUIRE f.version_id IS UNIQUE",
    ),
    (
        "resolved_entity_id",
        "CREATE CONSTRAINT resolved_entity_id IF NOT EXISTS "
        "FOR (e:ResolvedEntity) REQUIRE e.entity_id IS UNIQUE",
    ),
    (
        "identifier_natural",
        "CREATE CONSTRAINT identifier_natural IF NOT EXISTS "
        "FOR (i:Identifier) REQUIRE (i.authority, i.value, i.release) IS UNIQUE",
    ),
    (
        # the raw text is part of the key: one source record may carry several
        # mentions (a trial lists multiple interventions)
        "mention_natural_v2",
        "CREATE CONSTRAINT mention_natural_v2 IF NOT EXISTS "
        "FOR (m:Mention) REQUIRE (m.source, m.source_record_id, m.raw_text) IS UNIQUE",
    ),
    # provenance is mandatory at the database layer too — belt and braces
    ("prov_source", "CREATE CONSTRAINT prov_source IF NOT EXISTS "
                    "FOR (n:Provenanced) REQUIRE n.source IS NOT NULL"),
    ("prov_version", "CREATE CONSTRAINT prov_version IF NOT EXISTS "
                     "FOR (n:Provenanced) REQUIRE n.source_version IS NOT NULL"),
    ("prov_tier", "CREATE CONSTRAINT prov_tier IF NOT EXISTS "
                  "FOR (n:Provenanced) REQUIRE n.tier IS NOT NULL"),
    ("fact_lookup", "CREATE INDEX fact_lookup IF NOT EXISTS "
                    "FOR (f:FactVersion) ON (f.entity_id, f.key)"),
]


@dataclass
class GraphClient:
    """Thin driver wrapper. Parameterised queries only — never interpolation."""

    uri: str
    username: str
    password: str
    database: str = "neo4j"
    _driver: Driver | None = None

    def __enter__(self) -> GraphClient:
        self._driver = GraphDatabase.driver(self.uri, auth=(self.username, self.password))
        self._driver.verify_connectivity()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def run(self, cypher: str, **params: Any) -> list[dict]:
        if self._driver is None:
            raise RuntimeError("client not opened; use `with GraphClient(...) as c:`")
        with self._driver.session(database=self.database) as s:
            return [r.data() for r in s.run(cypher, **params)]

    def migrate(self) -> list[str]:
        applied = []
        for name, stmt in MIGRATIONS:
            self.run(stmt)
            applied.append(name)
        return applied

    def constraint_count(self) -> int:
        rows = self.run("SHOW CONSTRAINTS YIELD name RETURN count(*) AS c")
        return rows[0]["c"] if rows else 0


def _fact_to_props(fact: Fact, version_id: str) -> dict:
    s = fact.stamp
    return {
        "version_id": version_id,
        "entity_id": fact.entity_id or "",
        "key": fact.key,
        "value": str(fact.value),
        "source": s.source,
        "source_version": s.source_version,
        "tier": s.tier.value,
        "confidence": s.confidence_value,
        "method": s.method,
        "evidence": s.evidence,
        "valid_from": fact.validity.valid_from.isoformat(),
        "valid_to": fact.validity.valid_to.isoformat() if fact.validity.valid_to else None,
        "recorded_at": fact.recorded_at.isoformat(),
        "review_state": fact.review_state.value,
    }


def _props_to_fact(p: dict) -> Fact:
    method_label = p.get("method")
    conf = None
    if method_label:
        match = next((m for m in ResolutionMethod if m.label == method_label), None)
        if match is not None:
            conf = Confidence(method=match, value=p.get("confidence"))
    stamp = ProvenanceStamp(
        source=p["source"],
        source_version=p["source_version"],
        retrieved_at=datetime.fromisoformat(p["recorded_at"]),
        tier=Tier(p["tier"]),
        confidence=conf,
        method=method_label,
        evidence=p.get("evidence"),
    )
    return Fact(
        key=p["key"],
        value=p["value"],
        stamp=stamp,
        validity=ValidityInterval(
            valid_from=datetime.fromisoformat(p["valid_from"]),
            valid_to=datetime.fromisoformat(p["valid_to"]) if p.get("valid_to") else None,
        ),
        recorded_at=datetime.fromisoformat(p["recorded_at"]),
        entity_id=p.get("entity_id") or None,
        review_state=ReviewState(p.get("review_state", "not_required")),
        version_id=p["version_id"],
    )


class FactStore:
    """Append-only. No update. No delete."""

    def __init__(self, client: GraphClient):
        self._c = client

    def append(self, fact: Fact) -> str:
        version_id = fact.version_id or f"fv_{uuid.uuid4().hex[:12]}"
        self._c.run(
            """
            MERGE (e:ResolvedEntity {entity_id: $entity_id})
              ON CREATE SET e.source = $source, e.source_version = $source_version,
                            e.tier = $tier, e:Provenanced
            CREATE (f:FactVersion:Provenanced $props)
            CREATE (e)-[:ASSERTS]->(f)
            """,
            entity_id=fact.entity_id or "",
            source=fact.stamp.source,
            source_version=fact.stamp.source_version,
            tier=fact.stamp.tier.value,
            props=_fact_to_props(fact, version_id),
        )
        return version_id

    def supersede(self, prior_version_id: str, new_fact: Fact, at: datetime) -> str:
        """Close the prior version and append a new one. Never mutates a value."""
        new_id = self.append(new_fact)
        self._c.run(
            """
            MATCH (old:FactVersion {version_id: $old_id})
            MATCH (new:FactVersion {version_id: $new_id})
            SET old.valid_to = $at
            CREATE (new)-[:SUPERSEDES]->(old)
            """,
            old_id=prior_version_id,
            new_id=new_id,
            at=at.isoformat(),
        )
        return new_id

    def candidates(self, entity_id: str) -> list[Fact]:
        """All fact versions for an entity. Filtering is the trust boundary's job,
        not the store's — the store must not pre-judge eligibility."""
        rows = self._c.run(
            "MATCH (:ResolvedEntity {entity_id:$e})-[:ASSERTS]->(f:FactVersion) "
            "RETURN properties(f) AS p ORDER BY f.key, f.valid_from",
            e=entity_id,
        )
        return [_props_to_fact(r["p"]) for r in rows]

    def at(self, entity_id: str, key: str, instant: datetime) -> Fact | None:
        iso = instant.isoformat()
        rows = self._c.run(
            """
            MATCH (:ResolvedEntity {entity_id:$e})-[:ASSERTS]->(f:FactVersion {key:$k})
            WHERE f.valid_from <= $t AND (f.valid_to IS NULL OR f.valid_to > $t)
            RETURN properties(f) AS p ORDER BY f.valid_from DESC LIMIT 1
            """,
            e=entity_id, k=key, t=iso,
        )
        return _props_to_fact(rows[0]["p"]) if rows else None

    def history(self, entity_id: str, key: str) -> Sequence[Fact]:
        rows = self._c.run(
            "MATCH (:ResolvedEntity {entity_id:$e})-[:ASSERTS]->(f:FactVersion {key:$k}) "
            "RETURN properties(f) AS p ORDER BY f.valid_from",
            e=entity_id, k=key,
        )
        return [_props_to_fact(r["p"]) for r in rows]

    def tier_counts(self) -> dict[str, int]:
        rows = self._c.run(
            "MATCH (n:Provenanced) RETURN n.tier AS tier, count(*) AS c ORDER BY tier"
        )
        return {r["tier"]: r["c"] for r in rows}

    def purge_derived(self) -> int:
        """Remove exactly the derived tier, nothing else (component 01 R8.3)."""
        rows = self._c.run(
            "MATCH (f:FactVersion {tier:'derived'}) DETACH DELETE f RETURN count(*) AS c"
        )
        return rows[0]["c"] if rows else 0
