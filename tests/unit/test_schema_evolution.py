"""Schema evolution: release diffing and rule-identifier integrity.

The critical test here is `test_moved_identifier_yields_cannot_evaluate_not_no_violation`.
Everything else is supporting detail.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from biomedical.foundation import RuleOutcome, Severity
from biomedical.graph.release_diff import (
    ChangeKind,
    ReleaseDiff,
    SymbolChange,
    diff_from_hgnc_records,
    diff_pair,
)
from biomedical.graph.rule_integrity import (
    EvaluationContext,
    check_integrity,
    evaluate_rule,
    load_rules,
    propose_remediations,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hgnc_release_diff.json"


def load_fixture_diff() -> ReleaseDiff:
    d = json.loads(FIXTURE.read_text())
    return ReleaseDiff(
        authority=d["authority"], release_from=d["release_from"], release_to=d["release_to"],
        changes=tuple(
            SymbolChange(
                authority=d["authority"], old_value=c["old_value"], new_value=c["new_value"],
                change_kind=ChangeKind(c["change_kind"]), stable_id=c.get("stable_id"),
                entity_name=c.get("entity_name"),
            )
            for c in d["changes"]
        ),
    )


class FakeGene:
    def __init__(self, symbol, hgnc_id, name, prev):
        self.symbol, self.hgnc_id, self.name, self.prev_symbols = symbol, hgnc_id, name, prev


# ============================= ChangeKind ===================================


def test_every_change_kind_except_unchanged_breaks_dependents():
    for kind in ChangeKind:
        assert kind.breaks_dependents is (kind is not ChangeKind.UNCHANGED), kind


def test_orphan_obsoletion_is_not_recoverable():
    orphan = SymbolChange("mondo", "MONDO:0000001", None, ChangeKind.OBSOLETED_ORPHAN)
    assert not orphan.recoverable


def test_rename_is_recoverable():
    rename = SymbolChange("hgnc-symbol", "CDKN2", "CDKN2A", ChangeKind.RENAMED)
    assert rename.recoverable


# ============================= diffing ======================================


def test_diff_from_hgnc_records_detects_renames():
    diff = diff_from_hgnc_records([
        FakeGene("CDKN2A", "HGNC:1787", "cyclin dependent kinase inhibitor 2A", ["CDKN2", "MLM"]),
        FakeGene("BRAF", "HGNC:1097", "B-Raf proto-oncogene", []),
    ])
    moved = diff.moved_identifiers()
    assert set(moved) == {"CDKN2", "MLM"}
    assert all(c.change_kind is ChangeKind.RENAMED for c in diff.changes)
    assert diff.counts() == {"renamed": 2}


def test_a_gene_with_no_previous_symbols_produces_no_change():
    diff = diff_from_hgnc_records([FakeGene("BRAF", "HGNC:1097", "B-Raf", [])])
    assert diff.changes == ()


def test_self_referential_previous_symbol_is_ignored():
    diff = diff_from_hgnc_records([FakeGene("KIT", "HGNC:6342", "KIT", ["KIT", "PBT"])])
    assert set(diff.moved_identifiers()) == {"PBT"}


def test_diff_is_deterministic():
    recs = [FakeGene("MITF", "HGNC:7105", "MITF", ["WS2A", "WS2"])]
    assert diff_from_hgnc_records(recs) == diff_from_hgnc_records(recs)


def test_generic_pair_diff_classifies_all_four_kinds():
    before = {"ID1": "OLD_A", "ID2": "OLD_B", "ID3": "SAME", "ID4": "OLD_D"}
    after = {"ID1": "NEW_A", "ID3": "SAME"}
    diff = diff_pair(
        before, after, authority="mondo", release_from="2026-09", release_to="2026-10",
        replacements={"ID2": "REPLACEMENT_B"},
    )
    kinds = {c.old_value: c.change_kind for c in diff.changes}
    assert kinds["OLD_A"] is ChangeKind.RENAMED
    assert kinds["OLD_B"] is ChangeKind.OBSOLETED_WITH_REPLACEMENT
    assert kinds["OLD_D"] is ChangeKind.OBSOLETED_ORPHAN
    assert "SAME" not in kinds
    assert len(diff.orphans()) == 1


# ======================== rule loading (static, no graph) ===================


def test_rules_load_without_a_graph_connection():
    rules = load_rules()
    assert rules, "expected declarative rule packs"
    ids = {r.rule_id for r in rules}
    assert "safety.cdkn2_loss_requires_genotyping" in ids
    assert "integrity.rule_cites_obsolete_identifier" in ids


def test_rule_dependencies_are_extracted_statically():
    rule = next(r for r in load_rules() if r.rule_id == "safety.cdkn2_loss_requires_genotyping")
    assert rule.dependencies() == (("hgnc-symbol", "CDKN2"),)
    assert rule.severity is Severity.BLOCK


def test_a_rule_with_no_identifier_dependency_reports_none():
    rule = next(r for r in load_rules() if r.rule_id == "governance.trial_still_recruiting")
    assert rule.dependencies() == ()


# ============================ integrity check ===============================


def test_integrity_flags_rules_citing_moved_identifiers():
    findings = check_integrity(load_rules(), load_fixture_diff())
    flagged = {f.rule_id for f in findings}
    assert "safety.cdkn2_loss_requires_genotyping" in flagged
    assert "safety.mitf_amplification_disclosure" in flagged


def test_integrity_does_not_flag_a_current_identifier():
    findings = check_integrity(load_rules(), load_fixture_diff())
    assert "safety.braf_mutation_requires_genotyping" not in {f.rule_id for f in findings}


def test_integrity_ignores_a_diff_for_another_authority():
    mondo_diff = ReleaseDiff(
        authority="mondo", release_from="a", release_to="b",
        changes=(SymbolChange("mondo", "CDKN2", "X", ChangeKind.RENAMED),),
    )
    assert check_integrity(load_rules(), mondo_diff) == []


# ==================== THE CRITICAL TEST =====================================


def _ctx(symbols: set[str]) -> EvaluationContext:
    return EvaluationContext(
        gene_symbols=symbols, trial_properties={"NCT1": {"phase": "PHASE2"}}
    )


def test_rule_finds_violation_while_its_identifier_is_current():
    rule = next(r for r in load_rules() if r.rule_id == "safety.cdkn2_loss_requires_genotyping")
    res = evaluate_rule(rule, _ctx({"CDKN2"}))
    assert res.outcome is RuleOutcome.VIOLATION


def test_the_fail_open_is_real_without_integrity_checking():
    """Reproduces the v1 defect: a moved identifier reads as 'checked and clean'."""
    rule = next(r for r in load_rules() if r.rule_id == "safety.cdkn2_loss_requires_genotyping")
    res = evaluate_rule(rule, _ctx({"CDKN2A"}))   # renamed; rule not updated
    assert res.outcome is RuleOutcome.NO_VIOLATION
    assert res.outcome.is_safe, "this is exactly why the defect is dangerous"


def test_moved_identifier_yields_cannot_evaluate_not_no_violation():
    """The fix. CANNOT_EVALUATE must never be reported as NO_VIOLATION."""
    rule = next(r for r in load_rules() if r.rule_id == "safety.cdkn2_loss_requires_genotyping")
    findings = check_integrity(load_rules(), load_fixture_diff())
    res = evaluate_rule(rule, _ctx({"CDKN2A"}), integrity=findings)

    assert res.outcome is RuleOutcome.CANNOT_EVALUATE
    assert res.outcome is not RuleOutcome.NO_VIOLATION
    assert not res.outcome.is_safe
    assert res.outcome.blocks
    assert res.missing_dependencies == ("hgnc-symbol:CDKN2",)
    assert "renamed" in res.message


def test_an_unaffected_rule_still_evaluates_normally():
    rule = next(r for r in load_rules() if r.rule_id == "safety.braf_mutation_requires_genotyping")
    findings = check_integrity(load_rules(), load_fixture_diff())
    res = evaluate_rule(rule, _ctx({"BRAF"}), integrity=findings)
    assert res.outcome is RuleOutcome.VIOLATION


# ========================== remediation, never applied ======================


def test_proposals_are_pending_and_never_applied():
    proposals = propose_remediations(check_integrity(load_rules(), load_fixture_diff()))
    assert proposals
    for p in proposals:
        assert p.status in ("pending", "requires_human")
        assert p.accepted_by is None
        assert p.accepted_at is None


def test_an_orphan_obsoletion_requires_a_human():
    orphan = SymbolChange("hgnc-symbol", "CDKN2", None, ChangeKind.OBSOLETED_ORPHAN)
    diff = ReleaseDiff("hgnc-symbol", "a", "b", (orphan,))
    proposals = propose_remediations(check_integrity(load_rules(), diff))
    assert proposals
    assert proposals[0].proposed_identifier is None
    assert proposals[0].status == "requires_human"
    assert "human resolution required" in proposals[0].describe()


def test_fixture_marks_its_synthetic_entry():
    """The committed fixture must not pretend its orphan came from HGNC."""
    d = json.loads(FIXTURE.read_text())
    assert "_note" in d
    assert "synthetic" in d["_note"].lower()
    orphans = [c for c in d["changes"] if c["change_kind"] == "obsoleted_orphan"]
    assert orphans and "FIXTURE" in orphans[0]["old_value"]
