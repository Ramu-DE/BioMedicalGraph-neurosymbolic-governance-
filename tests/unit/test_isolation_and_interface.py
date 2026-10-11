"""Structural invariants that decay silently without a test.

1. The fact path and the retrieval path must not meet (component 02 R1).
2. The fact store must offer no way to mutate or delete (component 01 R3.3).

Both are properties a reviewer can check in one file, which is the point.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from biomedical.foundation import bundle as bundle_mod
from biomedical.graph.store import FactStore

PKG = Path(__file__).resolve().parents[2] / "biomedical"


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _module_files(subpackage: str) -> list[Path]:
    return sorted((PKG / subpackage).rglob("*.py"))


# ================= component 02 R1 : fact path vs retrieval path =============


def test_foundation_imports_no_vector_code():
    """The epistemic model must not know retrieval exists."""
    for f in _module_files("foundation"):
        for name in _imports_of(f):
            assert "vectors" not in name, f"{f.name} imports {name}"
            assert "qdrant" not in name.lower(), f"{f.name} imports {name}"


def test_graph_layer_imports_no_vector_code():
    """Facts come from the graph; the graph must not reach into retrieval."""
    for f in _module_files("graph"):
        for name in _imports_of(f):
            assert "vectors" not in name, f"{f.name} imports {name}"
            assert "qdrant" not in name.lower(), f"{f.name} imports {name}"


def test_vector_layer_cannot_produce_facts():
    """The retrieval layer must not construct Facts or FactBundles."""
    for f in _module_files("vectors"):
        src = f.read_text()
        for forbidden in ("FactBundle", "assemble(", "is_eligible_rule_input"):
            assert forbidden not in src, f"{f.name} references {forbidden}"


def test_bundle_is_the_only_route_to_eligible_facts():
    """`assemble` is the single entry point, and it requires a TrustPolicy."""
    sig = inspect.signature(bundle_mod.assemble)
    assert list(sig.parameters) == ["candidates", "policy", "now"]
    assert "policy" in sig.parameters, "facts cannot be bundled without a trust policy"


# ============ component 01 R3.3 : append-only store interface ===============

MUTATING_VERBS = {
    "update", "delete", "remove", "set", "set_value", "overwrite",
    "put", "patch", "upsert", "merge", "drop_fact", "edit",
}


def test_fact_store_exposes_no_mutating_verb():
    public = {
        name for name, _ in inspect.getmembers(FactStore, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    offending = public & MUTATING_VERBS
    assert not offending, f"FactStore exposes mutating verb(s): {sorted(offending)}"


def test_fact_store_exposes_the_expected_append_only_surface():
    public = {
        name for name, _ in inspect.getmembers(FactStore, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    assert {"append", "supersede", "at", "history", "candidates"} <= public


def test_supersede_requires_an_explicit_instant():
    """Closing a validity interval is a dated act, never implicit `now()`."""
    sig = inspect.signature(FactStore.supersede)
    assert "at" in sig.parameters
    assert sig.parameters["at"].default is inspect.Parameter.empty


def test_purge_is_scoped_to_the_derived_tier_only():
    src = inspect.getsource(FactStore.purge_derived)
    assert "tier:'derived'" in src.replace(" ", ""), "purge must not be able to remove other tiers"


# ===================== no raw string interpolation in Cypher ================


@pytest.mark.parametrize("subpackage", ["graph", "foundation"])
def test_cypher_is_always_parameterised(subpackage):
    """An f-string containing a Cypher clause is an injection waiting to happen."""
    for f in _module_files(subpackage):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                flat = "".join(
                    v.value for v in node.values if isinstance(v, ast.Constant)
                ).upper()
                for clause in ("MATCH ", "CREATE ", "MERGE ", "DELETE ", "SET "):
                    assert clause not in flat, f"{f.name}: interpolated Cypher {flat[:60]!r}"
