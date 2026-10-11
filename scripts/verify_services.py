"""Verify connectivity and report what each service can actually do.

Prints no secrets. Exits non-zero if a configured service is unreachable.

    .venv/bin/python scripts/verify_services.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

FAIL = False


def ok(msg: str) -> None:
    print(f"  [ ok ] {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL = True
    print(f"  [FAIL] {msg}")


def skip(msg: str) -> None:
    print(f"  [skip] {msg}")


def check_neo4j(cfg: config.Neo4jConfig) -> None:
    print("neo4j")
    if not cfg.configured:
        skip("not configured")
        return
    try:
        from neo4j import GraphDatabase
    except ImportError:
        bad("neo4j driver not installed")
        return

    driver = None
    try:
        driver = GraphDatabase.driver(cfg.uri, auth=(cfg.username, cfg.password.reveal()))
        driver.verify_connectivity()
        ok("connectivity verified")

        with driver.session(database=cfg.database) as s:
            comp = s.run(
                "CALL dbms.components() YIELD name, versions, edition "
                "RETURN name, versions[0] AS version, edition"
            ).single()
            if comp:
                ok(f"{comp['name']} {comp['version']} ({comp['edition']})")

            counts = s.run("MATCH (n) RETURN count(n) AS nodes").single()
            rels = s.run("MATCH ()-[r]->() RETURN count(r) AS rels").single()
            ok(f"existing graph: {counts['nodes']} nodes, {rels['rels']} relationships")

            labels = s.run("CALL db.labels() YIELD label RETURN collect(label) AS l").single()["l"]
            ok(f"labels present: {len(labels)}" + (f" -> {sorted(labels)[:8]}" if labels else " (empty graph)"))

            # capabilities the specs depend on
            procs = s.run(
                "SHOW PROCEDURES YIELD name "
                "WHERE name IN ['db.index.vector.queryNodes','db.index.fulltext.queryNodes'] "
                "RETURN collect(name) AS p"
            ).single()["p"]
            ok(f"vector/fulltext procedures: {sorted(procs) or 'none'}")

            # write permission - component 01 needs append/supersede
            s.run("CREATE (n:_ConnCheck {at: timestamp()}) RETURN n").consume()
            s.run("MATCH (n:_ConnCheck) DELETE n").consume()
            ok("write permission confirmed (probe node created and removed)")

            # constraint support for the spec's natural keys
            cons = s.run("SHOW CONSTRAINTS YIELD name RETURN count(*) AS c").single()["c"]
            ok(f"existing constraints: {cons}")
    except Exception as exc:  # noqa: BLE001
        bad(f"{type(exc).__name__}: {str(exc)[:160]}")
    finally:
        if driver is not None:
            driver.close()


def check_qdrant(cfg: config.QdrantConfig) -> None:
    print("qdrant")
    if not cfg.configured:
        skip("not configured")
        return
    try:
        from qdrant_client import QdrantClient
    except ImportError:
        bad("qdrant-client not installed")
        return

    try:
        client = QdrantClient(url=cfg.url, api_key=cfg.api_key.reveal(), timeout=cfg.timeout)
        cols = client.get_collections().collections
        ok("connectivity verified")
        ok(f"existing collections: {len(cols)}" + (f" -> {[c.name for c in cols][:8]}" if cols else " (none)"))

        probe = "_conn_check"
        from qdrant_client.models import Distance, PointStruct, VectorParams

        if client.collection_exists(probe):
            client.delete_collection(probe)
        client.create_collection(probe, vectors_config=VectorParams(size=4, distance=Distance.COSINE))
        client.upsert(probe, points=[PointStruct(id=1, vector=[0.1, 0.2, 0.3, 0.4], payload={"t": "x"})])
        hits = client.query_points(probe, query=[0.1, 0.2, 0.3, 0.4], limit=1).points
        ok(f"write + query confirmed ({len(hits)} hit, score={hits[0].score:.4f})")
        client.delete_collection(probe)
        ok("probe collection removed")
    except Exception as exc:  # noqa: BLE001
        bad(f"{type(exc).__name__}: {str(exc)[:160]}")


def main() -> int:
    cfg = config.load()
    print(config.report())
    print()
    check_neo4j(cfg.neo4j)
    print()
    check_qdrant(cfg.qdrant)
    print()
    if FAIL:
        print("RESULT: one or more configured services failed")
        return 1
    print("RESULT: all configured services reachable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
