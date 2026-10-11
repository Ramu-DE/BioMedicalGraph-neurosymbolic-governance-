"""Cross-source contradiction detection.

The load-bearing test is `test_indication_vs_adverse_event_is_not_auto_resolvable`:
some conflicts must not be settled by source precedence, because adjudicating
them needs domain knowledge rather than a ranking of sources.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from biomedical.foundation import AuthorityPrecedence
from biomedical.knowledge.conflicts import (
    Conflict,
    ConflictKind,
    ConflictSide,
    detect_ambiguous_mentions,
    detect_cross_authority_identity,
    detect_indication_vs_adverse_event,
    summarise,
)
from biomedical.sources.mondo import MondoTerm, best_similarity
from biomedical.sources.resolver import normalise_disease

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


class FakeClient:
    """Returns canned rows per query, so detectors are testable with no graph."""

    def __init__(self, rows_by_fragment: dict[str, list[dict]]):
        self._rows = rows_by_fragment
        self.queries: list[str] = []

    def run(self, cypher: str, **params):
        self.queries.append(cypher)
        for fragment, rows in self._rows.items():
            if fragment in cypher:
                return rows
        return []


def side(source: str, claim: str) -> ConflictSide:
    return ConflictSide(source=source, source_version="v", claim=claim, evidence="e")


# ===================== which kinds may be auto-resolved =====================


def test_indication_vs_adverse_event_is_not_auto_resolvable():
    """The central design decision of this module.

    Deciding whether a disease is the indication or the adverse event is a
    clinical judgment. Source precedence would produce a confident wrong answer.
    """
    assert not ConflictKind.INDICATION_VS_ADVERSE_EVENT.auto_resolvable


def test_identity_and_property_conflicts_are_auto_resolvable():
    assert ConflictKind.CROSS_AUTHORITY_IDENTITY.auto_resolvable
    assert ConflictKind.PROPERTY_DISAGREEMENT.auto_resolvable


def test_ambiguous_mention_requires_a_human():
    assert not ConflictKind.AMBIGUOUS_MENTION.auto_resolvable


def test_requires_human_is_the_inverse_of_auto_resolvable():
    for kind in ConflictKind:
        c = Conflict(
            conflict_id="x", kind=kind, subject="s", property="p",
            sides=(side("a", "1"), side("b", "2")), detected_at=NOW,
        )
        assert c.requires_human is (not kind.auto_resolvable)


# ===================== detector: indication vs adverse event ================


def test_detects_a_drug_investigated_for_a_disease_it_is_co_reported_with():
    client = FakeClient({
        "ASSOCIATED_WITH_REACTION": [{
            "drug_id": "drug:rxcui:3098", "drug": "dacarbazine",
            "indication": "cutaneous melanoma", "mondo_id": "MONDO:0005012",
            "reaction": "Malignant melanoma", "reports": 7,
        }],
    })
    conflicts = detect_indication_vs_adverse_event(client)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.kind is ConflictKind.INDICATION_VS_ADVERSE_EVENT
    assert c.subject == "dacarbazine"
    assert {s.source for s in c.sides} == {"clinicaltrials.gov", "openfda.faers"}
    assert c.requires_human


def test_the_conflict_note_names_confounding_by_indication():
    client = FakeClient({
        "ASSOCIATED_WITH_REACTION": [{
            "drug_id": "d", "drug": "dacarbazine", "indication": "melanoma",
            "mondo_id": "MONDO:1", "reaction": "Malignant melanoma", "reports": 7,
        }],
    })
    note = detect_indication_vs_adverse_event(client)[0].note.lower()
    assert "confounding by indication" in note
    assert "not evidence of causation" in note


def test_both_sides_are_always_retained():
    client = FakeClient({
        "ASSOCIATED_WITH_REACTION": [{
            "drug_id": "d", "drug": "Actos", "indication": "type 2 diabetes mellitus",
            "mondo_id": "MONDO:0005148", "reaction": "Type 2 diabetes mellitus",
            "reports": 4,
        }],
    })
    c = detect_indication_vs_adverse_event(client)[0]
    assert len(c.sides) == 2, "neither side may be dropped"
    assert not c.resolved


# ===================== detector: ambiguous mentions =========================


def test_detects_a_mention_with_several_above_threshold_candidates():
    client = FakeClient({
        "CANDIDATE_FOR": [{
            "raw": "Ovarian Neoplasms", "source": "clinicaltrials.gov",
            "cands": [
                {"name": "ovarian neoplasm", "id": "MONDO:0021068", "score": 0.97},
                {"name": "ovarian cancer", "id": "MONDO:0008170", "score": 0.97},
                {"name": "ovarian yolk sac tumor", "id": "MONDO:0006344", "score": 0.76},
            ],
            "chosen": "ovarian neoplasm", "method": "multiple_candidates",
            "confidence": 0.3,
        }],
    })
    conflicts = detect_ambiguous_mentions(client)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.subject == "Ovarian Neoplasms"
    assert len(c.sides) == 3
    assert "ineligible as a rule input" in c.note


def test_no_conflict_when_resolution_is_unambiguous():
    assert detect_ambiguous_mentions(FakeClient({})) == []


def test_cross_authority_detector_returns_nothing_when_identity_is_clean():
    assert detect_cross_authority_identity(FakeClient({})) == []


# ===================== resolution via precedence ============================


def test_precedence_settles_an_identity_conflict():
    p = AuthorityPrecedence.default()
    assert p.prefer(("openfda", "rxnorm")) == "rxnorm"


def test_a_precedence_tie_returns_none_so_a_human_decides():
    p = AuthorityPrecedence(order=("rxnorm",))
    assert p.prefer(("unlisted_a", "unlisted_b")) is None


def test_resolution_records_its_authority():
    c = Conflict(
        conflict_id="x", kind=ConflictKind.CROSS_AUTHORITY_IDENTITY,
        subject="s", property="p", sides=(side("rxnorm", "a"), side("openfda", "b")),
        detected_at=NOW, chosen_source="rxnorm",
        resolution_authority="vocab-precedence-v1", resolved_by="detector",
    )
    assert c.resolved
    assert "RESOLVED -> rxnorm (vocab-precedence-v1)" in c.describe()


def test_an_unresolved_human_conflict_says_so():
    c = Conflict(
        conflict_id="x", kind=ConflictKind.INDICATION_VS_ADVERSE_EVENT,
        subject="dacarbazine", property="p",
        sides=(side("a", "1"), side("b", "2")), detected_at=NOW,
    )
    assert "requires a human" in c.describe()


# ===================== summary ==============================================


def test_summary_counts_by_kind_and_routing():
    conflicts = [
        Conflict("a", ConflictKind.INDICATION_VS_ADVERSE_EVENT, "s", "p",
                 (side("x", "1"), side("y", "2")), NOW),
        Conflict("b", ConflictKind.AMBIGUOUS_MENTION, "s", "p",
                 (side("x", "1"), side("y", "2")), NOW),
        Conflict("c", ConflictKind.CROSS_AUTHORITY_IDENTITY, "s", "p",
                 (side("x", "1"), side("y", "2")), NOW),
    ]
    s = summarise(conflicts)
    assert s["total"] == 3
    assert s["requires_human"] == 2
    assert s["auto_resolvable"] == 1
    assert s["resolved"] == 0


# ============ resolver fixes that conflict detection surfaced ==============


def test_general_concept_beats_its_own_subtypes_on_a_tie():
    """MONDO subtypes contain the general concept's tokens and are longer, so
    similarity alone ranked 'inflammatory bowel disease 13' (a genetic locus)
    above 'inflammatory bowel disease'."""
    general = MondoTerm("MONDO:0005265", "inflammatory bowel disease")
    locus = MondoTerm("MONDO:0012831", "inflammatory bowel disease 13")
    q = "inflammatory bowel disease"
    assert best_similarity(q, general) > best_similarity(q, locus)


def test_hyphen_variance_does_not_cost_similarity():
    """MONDO writes 'B-cell'; ClinicalTrials.gov writes 'B cell'."""
    term = MondoTerm("MONDO:0018905", "diffuse large B-cell lymphoma")
    assert best_similarity("diffuse large B cell lymphoma", term) == 1.0


def test_leading_acronym_gloss_is_stripped():
    """'IBD - Inflammatory Bowel Disease' was resolving to a genetic locus."""
    assert normalise_disease("IBD - Inflammatory Bowel Disease") == "inflammatory bowel disease"
    assert normalise_disease("COPD: Chronic Obstructive Pulmonary Disease") == (
        "chronic obstructive pulmonary disease"
    )


@pytest.mark.parametrize("raw", ["Type 2 Diabetes", "Melanoma", "A-B syndrome"])
def test_leading_strip_does_not_damage_ordinary_strings(raw):
    assert normalise_disease(raw) != ""
