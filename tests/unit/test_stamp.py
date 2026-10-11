"""Component 00 R1, R2 — a fact cannot exist without provenance."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from biomedical.foundation import (
    Confidence,
    Fact,
    ProvenanceStamp,
    ResolutionMethod,
    Tier,
    ValidityInterval,
    asserted,
    derived,
    ingested,
)

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def _interval() -> ValidityInterval:
    return ValidityInterval(valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc))


# -- R1.3 : unstamped construction is impossible ---------------------------------


def test_fact_requires_a_stamp():
    with pytest.raises(TypeError):
        Fact(key="consent_status", value="Withdrawn")  # type: ignore[call-arg]


def test_fact_rejects_a_non_stamp():
    with pytest.raises(TypeError, match="requires a ProvenanceStamp"):
        Fact(
            key="consent_status",
            value="Withdrawn",
            stamp="trust me",  # type: ignore[arg-type]
            validity=_interval(),
            recorded_at=NOW,
        )


# -- R1.5 : source_version is explicit, never "latest" --------------------------


def test_stamp_requires_explicit_source_version():
    with pytest.raises(ValueError, match="source_version is required"):
        ProvenanceStamp(source="openfda", source_version="", retrieved_at=NOW, tier=Tier.INGESTED)


def test_stamp_requires_a_source():
    with pytest.raises(ValueError, match="source is required"):
        ProvenanceStamp(source="", source_version="2026-09", retrieved_at=NOW, tier=Tier.INGESTED)


# -- R1.4, R1.6 : confidence cannot be authored ---------------------------------


def test_confidence_cannot_be_built_from_a_bare_float():
    with pytest.raises(TypeError, match="registered ResolutionMethod"):
        Confidence(method=0.95)  # type: ignore[arg-type]


def test_confidence_takes_its_value_from_the_method():
    c = Confidence.of(ResolutionMethod.NORMALISED_EXACT)
    assert c.value == pytest.approx(0.95)
    assert c.method is ResolutionMethod.NORMALISED_EXACT


def test_confidence_without_a_method_name_is_rejected_on_the_stamp():
    with pytest.raises(ValueError, match="confidence requires a named derivation method"):
        ProvenanceStamp(
            source="rxnorm",
            source_version="2026-09",
            retrieved_at=NOW,
            tier=Tier.INGESTED,
            confidence=Confidence.of(ResolutionMethod.EXACT_IDENTIFIER),
            method=None,
        )


def test_every_resolution_method_has_a_confidence_in_range():
    for m in ResolutionMethod:
        assert 0.0 <= m.default_confidence <= 1.0, m


# -- R2.4 : derived facts need method and evidence ------------------------------


def test_derived_stamp_requires_method_and_evidence():
    with pytest.raises(ValueError, match="derived facts require both method and evidence"):
        ProvenanceStamp(
            source="resolver", source_version="1.0", retrieved_at=NOW, tier=Tier.DERIVED
        )


def test_derived_helper_populates_method_evidence_and_confidence():
    s = derived(
        "resolver",
        "1.0",
        NOW,
        ResolutionMethod.UNIQUE_INGREDIENT,
        evidence="'KEYTRUDA 100MG/4ML' -> ingredient pembrolizumab",
    )
    assert s.tier is Tier.DERIVED
    assert s.method == "unique_ingredient_after_dose_strip"
    assert s.evidence
    assert s.confidence_value == pytest.approx(0.90)


# -- R2.1, R2.6 : four tiers, individually orderable ---------------------------


def test_exactly_four_tiers():
    assert {t.value for t in Tier} == {"asserted", "ingested", "derived", "runtime"}


def test_tier_strength_orders_runtime_below_asserted():
    assert Tier.RUNTIME.strength < Tier.DERIVED.strength < Tier.INGESTED.strength < Tier.ASSERTED.strength


def test_helpers_produce_the_right_tiers():
    assert asserted("authored", "v1", NOW).tier is Tier.ASSERTED
    assert ingested("openfda:faers", "2026-09", NOW).tier is Tier.INGESTED


def test_unstamped_confidence_defaults_to_full_for_plain_assertions():
    s = ingested("rxnorm", "2026-09", NOW)
    assert s.confidence_value == 1.0
