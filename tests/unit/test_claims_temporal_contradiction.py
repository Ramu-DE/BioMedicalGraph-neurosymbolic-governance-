"""Component 00 R4, R5, R7 — temporal, contradiction, claims discipline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from biomedical.foundation import (
    AuthorityPrecedence,
    Claim,
    ClaimCategory,
    ClaimError,
    ClaimsRegister,
    Contradiction,
    Fact,
    Resolution,
    RunManifest,
    ValidityInterval,
    design_hash,
    ingested,
    lint_public_text,
)

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


# ============================ temporal (R4) ==================================


def test_interval_rejects_inverted_bounds():
    with pytest.raises(ValueError, match="valid_to must be after valid_from"):
        ValidityInterval(valid_from=NOW, valid_to=NOW - timedelta(days=1))


def test_open_interval_contains_now():
    i = ValidityInterval(valid_from=NOW - timedelta(days=5))
    assert i.open and i.contains(NOW)


def test_closed_interval_is_half_open():
    end = NOW
    i = ValidityInterval(valid_from=NOW - timedelta(days=5), valid_to=end)
    assert i.contains(end - timedelta(seconds=1))
    assert not i.contains(end), "valid_to is exclusive"


def test_close_returns_a_new_interval_and_never_mutates():
    original = ValidityInterval(valid_from=NOW - timedelta(days=5))
    closed = original.close(NOW)
    assert original.open, "supersession must not mutate the prior version"
    assert closed.valid_to == NOW


def test_fact_close_supersedes_without_mutation():
    f: Fact = Fact(
        key="consent_status",
        value="Active",
        stamp=ingested("catalog", "2026-09", NOW),
        validity=ValidityInterval(valid_from=NOW - timedelta(days=10)),
        recorded_at=NOW,
    )
    closed = f.close(NOW)
    assert f.validity.open
    assert closed.validity.valid_to == NOW
    assert closed.value == "Active"


# ========================= contradiction (R5) ================================


def _contradiction(resolution: Resolution | None = None) -> Contradiction:
    return Contradiction(
        contradiction_id="c1",
        entity_id="drug:D001",
        property="approval_status",
        sources=("rxnorm", "catalog"),
        values=("Approved", "Investigational"),
        detected_at=NOW,
        resolution=resolution,
    )


def test_contradiction_needs_at_least_two_sources():
    with pytest.raises(ValueError, match="at least two sources"):
        Contradiction(
            contradiction_id="c",
            entity_id="e",
            property="p",
            sources=("rxnorm",),
            values=("x",),
            detected_at=NOW,
        )


def test_both_values_are_retained():
    c = _contradiction()
    assert c.values == ("Approved", "Investigational")
    assert not c.resolved


def test_resolution_requires_a_cited_authority():
    with pytest.raises(ValueError, match="requires a cited authority"):
        Resolution(chosen_source="rxnorm", authority="", applied_by="rg", applied_at=NOW)


def test_resolution_requires_an_accountable_person():
    with pytest.raises(ValueError, match="accountable applied_by"):
        Resolution(chosen_source="rxnorm", authority="vocab-precedence-v1", applied_by="", applied_at=NOW)


def test_resolved_contradiction_records_who_and_why():
    r = Resolution(
        chosen_source="rxnorm", authority="vocab-precedence-v1", applied_by="rg", applied_at=NOW
    )
    c = _contradiction(r)
    assert c.resolved
    assert "resolved -> rxnorm by vocab-precedence-v1" in c.describe()


def test_authority_precedence_is_explicit_and_ordered():
    p = AuthorityPrecedence.default()
    assert p.prefer(("openfda", "rxnorm")) == "rxnorm"
    assert p.rank("rxnorm") < p.rank("catalog")


def test_unlisted_sources_rank_last_and_never_win_by_accident():
    p = AuthorityPrecedence.default()
    assert p.prefer(("mystery_source", "rxnorm")) == "rxnorm"


def test_a_tie_returns_none_so_a_human_decides():
    p = AuthorityPrecedence(order=("a",))
    assert p.prefer(("unlisted_x", "unlisted_y")) is None


# ============================ claims (R7) ====================================


def _claim(**kw) -> Claim:
    base = dict(
        claim_id="agreement-illusion",
        claim="An unanchored LLM judge was a constant-FAIL predictor on this set",
        category=ClaimCategory.ACCURACY,
        evidence="manifests/eval-20261011.json",
        n=12,
        does_not_establish="That LLM judges lack predictive value in general.",
        interval="TNR 0.000 [0.00, 0.43]",
    )
    base.update(kw)
    return Claim(**base)  # type: ignore[arg-type]


def test_a_valid_claim_is_accepted():
    assert _claim().n == 12


def test_claim_without_limits_is_rejected():
    with pytest.raises(ClaimError, match="does_not_establish"):
        _claim(does_not_establish="   ")


def test_claim_without_evidence_is_rejected():
    with pytest.raises(ClaimError, match="evidence reference is required"):
        _claim(evidence="")


@pytest.mark.parametrize(
    "text",
    [
        "Adversarial-proof by architecture, not tuning",
        "Measured proof that LLM-as-Judge has zero predictive value",
        "This proves the approach is sound",
    ],
)
def test_banned_phrasing_is_rejected(text):
    with pytest.raises(ClaimError, match="banned phrasing"):
        _claim(claim=text)


def test_availability_cannot_masquerade_as_safety():
    reg = ClaimsRegister.empty()
    reg.add(
        _claim(
            claim_id="llm-outage",
            claim="Rules continued to evaluate while the model was unavailable",
            category=ClaimCategory.AVAILABILITY,
            n=1,
            does_not_establish="That decisions made during the outage were correct.",
            interval=None,
        )
    )
    assert reg.by_category(ClaimCategory.SAFETY) == []
    assert len(reg.by_category(ClaimCategory.AVAILABILITY)) == 1


def test_unregistered_claim_refuses_to_render():
    with pytest.raises(ClaimError, match="unregistered claim"):
        ClaimsRegister.empty().get("nonexistent")


def test_duplicate_claim_ids_are_rejected():
    reg = ClaimsRegister.empty()
    reg.add(_claim())
    with pytest.raises(ClaimError, match="duplicate claim id"):
        reg.add(_claim())


# ============================== lint (R7.5, R7.6) ============================


def test_lint_flags_a_bare_rate():
    problems = lint_public_text("The judge scored 58% accuracy on governance cases.")
    assert any("without a visible n or interval" in p for p in problems)


def test_lint_accepts_a_rate_with_n_and_interval():
    text = "The judge scored 58% accuracy (n=12, TNR 0.000 [0.00, 0.43])."
    assert lint_public_text(text) == []


def test_lint_accepts_a_rate_with_n_alone():
    assert lint_public_text("Accuracy was 58% across n=12 governance cases.") == []


def test_lint_flags_banned_words():
    problems = lint_public_text("The system is adversarial-proof.")
    assert any("banned phrase" in p for p in problems)


def test_lint_flags_the_v1_headline():
    problems = lint_public_text(
        "Neurosymbolic AI evaluation framework proving that LLM-as-Judge is worse than no evals."
    )
    assert problems, "the rejected submission's own headline must fail lint"


# ============================ manifest (R8) ==================================


def test_design_hash_is_stable_and_order_independent():
    a = design_hash({"n": 12, "arms": ["det", "judge_blind"]})
    b = design_hash({"arms": ["det", "judge_blind"], "n": 12})
    assert a == b


def test_different_designs_hash_differently():
    assert design_hash({"n": 12}) != design_hash({"n": 53})


def test_manifest_records_everything_needed_to_reproduce():
    m = RunManifest.create(
        design={"n": 12, "arms": ["deterministic", "judge_blind", "judge_grounded"]},
        source_versions={"openfda:faers": "2026-09", "hgnc": "2026-10-01"},
        slice_params={"therapeutic_area": "oncology"},
        seeds={"bootstrap": 20261011},
        trust_policy_version="1.0",
    )
    assert m.run_id.startswith("run_")
    assert len(m.design_hash) == 16
    assert m.source_versions["hgnc"] == "2026-10-01"
    assert m.seeds["bootstrap"] == 20261011
    assert "design_hash" in m.to_json()
