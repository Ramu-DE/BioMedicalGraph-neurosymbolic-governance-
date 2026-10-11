"""Disease resolution: normalisation and similarity scoring.

Every test here is credential-free and network-free. The normalisation rules are
tested individually because that is where silent resolution errors originate, and
the similarity function is tested because an earlier version of it fabricated its
scores.
"""

from __future__ import annotations

import pytest

from biomedical.foundation import ResolutionMethod
from biomedical.sources.mondo import MondoTerm, best_similarity, is_non_human
from biomedical.sources.resolver import (
    disease_variants,
    invert_comma_form,
    normalise_disease,
)


def term(label: str, *synonyms: str) -> MondoTerm:
    return MondoTerm(mondo_id="MONDO:0000001", label=label, synonyms=synonyms)


# ===================== comma inversion ======================================


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Diabetes Mellitus, Type 2", "Type 2 Diabetes Mellitus"),
        ("Arthritis, Rheumatoid", "Rheumatoid Arthritis"),
        ("Carcinoma, Non-Small-Cell Lung", "Non-Small-Cell Lung Carcinoma"),
    ],
)
def test_mesh_style_inversion(raw, expected):
    assert invert_comma_form(raw) == expected


def test_inversion_left_alone_without_a_comma():
    assert invert_comma_form("Type 2 Diabetes") == "Type 2 Diabetes"


def test_inversion_declines_when_the_tail_is_a_long_phrase():
    """Not every comma is a MeSH inversion."""
    raw = "Melanoma, with brain metastases and prior immunotherapy exposure"
    assert invert_comma_form(raw) == raw


# ===================== normalisation ========================================


def test_the_six_diabetes_variants_collapse_to_two_forms():
    variants = [
        "Diabetes Mellitus, Type 2",
        "Type 2 Diabetes Mellitus",
        "Type 2 Diabetes Mellitus (T2DM)",
        "Diabetes Mellitus Type 2",
        "Type2 Diabetes Mellitus",
    ]
    assert {normalise_disease(v) for v in variants} == {"type 2 diabetes mellitus"}


def test_missing_space_after_type_is_repaired():
    """Found by manual audit: 'Type2 Diabetes' failed while every spaced form worked."""
    assert normalise_disease("Type2 Diabetes") == "type 2 diabetes"
    assert normalise_disease("Stage3 Breast Cancer") == "breast cancer"


def test_trailing_abbreviation_is_stripped():
    assert normalise_disease("Atrial Fibrillation (AF)") == "atrial fibrillation"


def test_stage_and_severity_noise_is_stripped():
    assert normalise_disease("Metastatic Melanoma") == "melanoma"
    assert normalise_disease("Recurrent Breast Cancer") == "breast cancer"


def test_stage_stripping_is_lossy_and_that_is_a_known_trade_off():
    """Stripping 'metastatic' merges clinically distinct cohorts onto one concept.

    Defensible for *identity* resolution, wrong for cohort selection. The raw
    string is always retained on the Mention, so nothing is destroyed - but this
    test exists so the trade-off cannot be forgotten.
    """
    assert normalise_disease("Metastatic Melanoma") == normalise_disease("Melanoma")


def test_variants_are_ordered_and_deduplicated():
    v = disease_variants("Diabetes Mellitus, Type 2")
    assert v[0] == "Diabetes Mellitus, Type 2", "the raw form is tried first"
    assert len(v) == len(set(v))
    assert "type 2 diabetes mellitus" in v


def test_empty_input_normalises_to_empty():
    assert normalise_disease("   ") == ""


# ===================== similarity ===========================================


def test_identical_label_scores_one():
    assert best_similarity("chronic kidney disease", term("chronic kidney disease")) == 1.0


def test_similarity_uses_synonyms():
    t = term("type 2 diabetes mellitus", "NIDDM", "adult-onset diabetes")
    assert best_similarity("NIDDM", t) == 1.0


def test_similarity_is_bounded_and_deterministic():
    t = term("melanoma", "malignant melanoma")
    a = best_similarity("skin cancer", t)
    b = best_similarity("skin cancer", t)
    assert a == b
    assert 0.0 <= a <= 1.0


def test_empty_query_scores_zero():
    assert best_similarity("", term("melanoma")) == 0.0


def test_token_subset_bonus_requires_a_comparable_length():
    """The length guard that stopped 'solid tumor' matching broader cancers.

    Without it, any term whose synonym set contained both query tokens scored
    0.85 regardless of how much broader the term was.
    """
    close = term("osteoarthritis knee")                       # 2 tokens vs 2
    broad = term("solid tumor with NTRK fusion positive")     # 2 tokens vs 6
    assert best_similarity("knee osteoarthritis", close) >= 0.7
    assert best_similarity("solid tumor", broad) < 0.85


# ===================== species filtering ====================================


@pytest.mark.parametrize(
    "label",
    [
        "sinus bradycardia, non-human animal",
        "type 2 diabetes mellitus, pig",
        "atrial premature complexes, non-human animal",
        "dilated cardiomyopathy, dog",
    ],
)
def test_veterinary_terms_are_rejected(label):
    assert is_non_human(term(label))


@pytest.mark.parametrize(
    "label",
    ["type 2 diabetes mellitus", "melanoma", "catscratch disease", "pigmented villonodular synovitis"],
)
def test_human_terms_are_kept(label):
    """The species filter must not catch words that merely contain an animal name."""
    assert not is_non_human(term(label))


# ===================== method registry ======================================


def test_the_synonym_method_scores_below_a_label_match():
    assert (
        ResolutionMethod.EXACT_SYNONYM.default_confidence
        < ResolutionMethod.EXACT_IDENTIFIER.default_confidence
    )


def test_the_fuzzy_band_sits_below_the_trust_floor():
    """0.60 is below the 0.75 confidence floor, so a fuzzy match can never
    become a rule input without human review."""
    assert ResolutionMethod.SINGLE_FUZZY_ABOVE_TAU.default_confidence < 0.75
