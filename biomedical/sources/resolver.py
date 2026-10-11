"""Entity resolution with derived confidence (component 01 R2, component 03 R3).

This replaces v1's `external_mappings.csv`, where all 30 rows carried a
hand-asserted confidence (`1.0` for RxNorm, `0.95` for GS1) with no derivation
recorded. Here confidence is a property *of the method*, and every mapping
records the method and the evidence that produced it.

Input is genuinely messy: human-entered FAERS `medicinalproduct` strings and
free-text ClinicalTrials.gov intervention names. There is no gold standard, so
resolution quality is evaluated by consistency and round-trip rather than by
accuracy — see `evaluate_consistency`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..foundation import ResolutionMethod
from . import adapters

# dose / strength / form tokens that must be stripped before matching
DOSE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|l|iu|unit|units|%)\b|"
    r"\b\d+\s*/\s*\d+\s*(?:mg|ml)?\b|\b\d+(?:\.\d+)?\b",
    re.I,
)
FORMS = re.compile(
    r"\b(tablet|tablets|capsule|capsules|injection|injectable|solution|suspension|"
    r"cream|ointment|patch|spray|inhaler|syrup|powder|vial|oral|iv|intravenous|"
    r"subcutaneous|sc|film|coated|extended|release|er|xr|sr)\b",
    re.I,
)
PUNCT = re.compile(r"[^\w\s\-]")
SPACE = re.compile(r"\s+")

# combination products: "A/B" or "A AND B"
COMBO = re.compile(r"\s*(?:/|\\|\+|\band\b|\bwith\b)\s*", re.I)


def normalise(raw: str) -> str:
    """Documented, individually testable normalisation.

    Silent resolution errors originate here more often than in matching, which
    is why each rule is tested separately.
    """
    s = raw.strip().lower()
    s = PUNCT.sub(" ", s)
    s = DOSE.sub(" ", s)
    s = FORMS.sub(" ", s)
    s = SPACE.sub(" ", s).strip()
    return s


def split_combination(raw: str) -> list[str]:
    parts = [p.strip() for p in COMBO.split(raw) if p.strip()]
    return parts if len(parts) > 1 else [raw]


@dataclass(frozen=True)
class Candidate:
    rxcui: str
    name: str
    score: float
    rank: int


@dataclass(frozen=True)
class Resolution:
    """One resolution decision, fully auditable."""

    raw: str
    normalised: str
    method: ResolutionMethod
    confidence: float
    rxcui: str | None
    canonical_name: str | None
    evidence: str
    candidates: tuple[Candidate, ...] = ()
    needs_review: bool = False

    @property
    def resolved(self) -> bool:
        return self.rxcui is not None

    def describe(self) -> str:
        target = f"{self.canonical_name} ({self.rxcui})" if self.resolved else "UNLINKED"
        flag = "  [NEEDS_REVIEW]" if self.needs_review else ""
        return (
            f"{self.raw[:42]:<42} -> {target[:40]:<40} "
            f"{self.confidence:.2f} via {self.method.label}{flag}"
        )


def resolve_drug(raw: str, *, fuzzy_threshold: float = 0.60) -> Resolution:
    """Five-stage cascade. Each stage declares its own confidence; nothing is
    authored by the caller."""
    norm = normalise(raw)
    if not norm:
        return Resolution(
            raw=raw, normalised=norm, method=ResolutionMethod.MULTI_CANDIDATE,
            confidence=0.0, rxcui=None, canonical_name=None,
            evidence="empty after normalisation", needs_review=True,
        )

    # stage 1 - exact RXCUI on the raw string
    try:
        exact = adapters.rx_exact(raw)
        if len(exact) == 1:
            return Resolution(
                raw=raw, normalised=norm, method=ResolutionMethod.EXACT_IDENTIFIER,
                confidence=ResolutionMethod.EXACT_IDENTIFIER.default_confidence,
                rxcui=exact[0].rxcui, canonical_name=adapters.rx_canonical_name(exact[0].rxcui),
                evidence=f"exact RxNorm name match on {raw!r}",
            )
    except Exception:  # noqa: BLE001
        pass

    # stage 2 - exact on the normalised string
    try:
        if norm != raw.lower():
            exact = adapters.rx_exact(norm)
            if len(exact) == 1:
                return Resolution(
                    raw=raw, normalised=norm, method=ResolutionMethod.NORMALISED_EXACT,
                    confidence=ResolutionMethod.NORMALISED_EXACT.default_confidence,
                    rxcui=exact[0].rxcui,
                    canonical_name=adapters.rx_canonical_name(exact[0].rxcui),
                    evidence=f"exact match after normalisation: {raw!r} -> {norm!r}",
                )
    except Exception:  # noqa: BLE001
        pass

    # stage 3 - single ingredient after splitting a combination product
    parts = split_combination(norm)
    if len(parts) > 1:
        hits: list[tuple[str, str]] = []
        for p in parts:
            try:
                e = adapters.rx_exact(p)
                if len(e) == 1:
                    hits.append((p, e[0].rxcui))
            except Exception:  # noqa: BLE001
                continue
        if len(hits) == 1:
            p, rxcui = hits[0]
            return Resolution(
                raw=raw, normalised=norm, method=ResolutionMethod.UNIQUE_INGREDIENT,
                confidence=ResolutionMethod.UNIQUE_INGREDIENT.default_confidence,
                rxcui=rxcui, canonical_name=adapters.rx_canonical_name(rxcui),
                evidence=f"combination {raw!r} -> single resolvable ingredient {p!r}",
            )

    # stages 4 / 5 - approximate match
    try:
        approx = adapters.rx_approximate(norm, max_entries=4)
    except Exception:  # noqa: BLE001
        approx = []

    cands = tuple(
        Candidate(rxcui=c.rxcui, name=c.name, score=s, rank=i + 1)
        for i, (c, s) in enumerate(approx)
    )

    strong = [c for c in cands if c.score >= fuzzy_threshold]
    if len(strong) == 1:
        c = strong[0]
        return Resolution(
            raw=raw, normalised=norm, method=ResolutionMethod.SINGLE_FUZZY_ABOVE_TAU,
            confidence=ResolutionMethod.SINGLE_FUZZY_ABOVE_TAU.default_confidence,
            rxcui=c.rxcui, canonical_name=c.name,
            evidence=f"single approximate candidate at score {c.score:.2f} >= {fuzzy_threshold}",
            candidates=cands,
        )

    if cands:
        # record ALL candidates; do not silently pick one
        return Resolution(
            raw=raw, normalised=norm, method=ResolutionMethod.MULTI_CANDIDATE,
            confidence=ResolutionMethod.MULTI_CANDIDATE.default_confidence,
            rxcui=cands[0].rxcui, canonical_name=cands[0].name,
            evidence=(
                f"{len(cands)} candidates, best {cands[0].score:.2f}: "
                + ", ".join(f"{c.name}({c.score:.2f})" for c in cands[:3])
            ),
            candidates=cands, needs_review=True,
        )

    return Resolution(
        raw=raw, normalised=norm, method=ResolutionMethod.MULTI_CANDIDATE,
        confidence=0.0, rxcui=None, canonical_name=None,
        evidence="no RxNorm candidate found", needs_review=True,
    )


# --------------------------------------------------------------------------- #
# evaluating a matcher with no gold standard (component 03 R3.6)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ConsistencyReport:
    """What can be established without an answer key — and what cannot."""

    round_trip_attempted: int
    round_trip_agreed: int
    band_counts: dict[str, int]

    @property
    def round_trip_rate(self) -> float:
        return self.round_trip_agreed / self.round_trip_attempted if self.round_trip_attempted else 0.0

    def describe(self) -> str:
        lines = [
            "resolution quality, evaluated WITHOUT a gold standard:",
            f"  round-trip agreement: {self.round_trip_agreed}/{self.round_trip_attempted}"
            f" = {self.round_trip_rate:.0%}",
            "  confidence bands:",
        ]
        for band, n in sorted(self.band_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {band:<34} {n}")
        lines.append("  establishes: definite errors (a disagreeing round-trip is wrong).")
        lines.append("  does NOT establish: accuracy. No answer key exists for these strings.")
        return "\n".join(lines)


def evaluate_consistency(resolutions: list[Resolution]) -> ConsistencyReport:
    """Round-trip: RXCUI -> canonical name -> re-resolve -> same RXCUI?

    A disagreement is a definite error even without knowing the right answer.
    Agreement proves nothing about correctness, and the report says so.
    """
    attempted = agreed = 0
    for r in resolutions:
        if not r.resolved or not r.canonical_name:
            continue
        attempted += 1
        try:
            back = adapters.rx_exact(r.canonical_name)
            if any(b.rxcui == r.rxcui for b in back):
                agreed += 1
        except Exception:  # noqa: BLE001
            pass

    bands: dict[str, int] = {}
    for r in resolutions:
        bands[r.method.label] = bands.get(r.method.label, 0) + 1

    return ConsistencyReport(
        round_trip_attempted=attempted, round_trip_agreed=agreed, band_counts=bands
    )


# --------------------------------------------------------------------------- #
# disease resolution against MONDO (component 03 R3.7)
# --------------------------------------------------------------------------- #

# Clinical trial condition strings arrive in several house styles. These rules
# are individually tested, because this is where silent resolution errors come
# from far more often than the matching stage.

_PAREN_ABBREV = re.compile(r"\s*\([^)]{1,12}\)\s*$")       # "... (T2DM)"
# "IBD - Inflammatory Bowel Disease" -> "Inflammatory Bowel Disease".
# Leading acronym gloss, the mirror image of the trailing "(AF)" form.
_LEAD_ABBREV = re.compile(r"^[A-Z][A-Za-z0-9]{1,8}\s*[-–:]\s*(?=[A-Za-z])")
# "Type2 Diabetes" -> "Type 2 Diabetes". Found by manual audit: the no-space form
# failed to resolve while every spaced variant succeeded.
_MISSING_SPACE = re.compile(r"\b(type|stage|phase|grade)(\d+)\b", re.I)

# "Diabetes Mellitus Type 2" -> "Type 2 Diabetes Mellitus". The comma-inverted
# form is MeSH style; this is the same inversion without the comma, which
# invert_comma_form cannot see. Found by a unit test, not in production.
_TRAILING_QUALIFIER = re.compile(
    r"^(?P<head>.+?)\s+(?P<qual>(?:type|stage|grade|phase)\s+[0-9ivx]+)$", re.I
)


def front_trailing_qualifier(raw: str) -> str:
    m = _TRAILING_QUALIFIER.match(raw.strip())
    if not m:
        return raw
    return f"{m.group('qual')} {m.group('head')}"


_STAGE_NOISE = re.compile(
    r"\b(unspecified|nos|not otherwise specified|adult|paediatric|pediatric|"
    r"newly diagnosed|recurrent|refractory|advanced|metastatic|stage\s+[ivx0-9]+)\b",
    re.I,
)


def invert_comma_form(raw: str) -> str:
    """'Diabetes Mellitus, Type 2' -> 'Type 2 Diabetes Mellitus'.

    MeSH-derived condition strings use the inverted form; MONDO labels do not.
    This single rule collapses most of the duplication.
    """
    if "," not in raw:
        return raw
    head, _, tail = raw.partition(",")
    head, tail = head.strip(), tail.strip()
    if not head or not tail or len(tail.split()) > 4:
        return raw
    return f"{tail} {head}"


def normalise_disease(raw: str) -> str:
    s = raw.strip()
    s = _MISSING_SPACE.sub(r"\1 \2", s)
    s = _LEAD_ABBREV.sub("", s)
    s = _PAREN_ABBREV.sub("", s)
    s = invert_comma_form(s)
    s = front_trailing_qualifier(s)
    s = PUNCT.sub(" ", s.lower())
    s = _STAGE_NOISE.sub(" ", s)
    s = SPACE.sub(" ", s).strip()
    return s


def disease_variants(raw: str) -> list[str]:
    """Surface forms to try, best first, de-duplicated."""
    spaced = _MISSING_SPACE.sub(r"\1 \2", raw.strip())
    forms = [
        raw.strip(),
        spaced,
        _PAREN_ABBREV.sub("", raw).strip(),
        invert_comma_form(raw).strip(),
        invert_comma_form(_PAREN_ABBREV.sub("", raw)).strip(),
        _LEAD_ABBREV.sub("", spaced).strip(),
        front_trailing_qualifier(_PAREN_ABBREV.sub("", spaced)).strip(),
        normalise_disease(raw),
    ]
    return list(dict.fromkeys(f for f in forms if f))


@dataclass(frozen=True)
class DiseaseResolution:
    raw: str
    normalised: str
    method: ResolutionMethod
    confidence: float
    mondo_id: str | None
    label: str | None
    evidence: str
    candidates: tuple[Candidate, ...] = ()
    needs_review: bool = False

    @property
    def resolved(self) -> bool:
        return self.mondo_id is not None

    def describe(self) -> str:
        target = f"{self.label} ({self.mondo_id})" if self.resolved else "UNLINKED"
        flag = "  [NEEDS_REVIEW]" if self.needs_review else ""
        return (
            f"{self.raw[:40]:<40} -> {target[:42]:<42} "
            f"{self.confidence:.2f} via {self.method.label}{flag}"
        )


def resolve_disease(raw: str, *, fuzzy_threshold: float = 0.60) -> DiseaseResolution:
    """Five-stage cascade against MONDO. Confidence comes from the method."""
    from . import mondo

    norm = normalise_disease(raw)
    if not norm:
        return DiseaseResolution(
            raw=raw, normalised=norm, method=ResolutionMethod.MULTI_CANDIDATE,
            confidence=0.0, mondo_id=None, label=None,
            evidence="empty after normalisation", needs_review=True,
        )

    variants = disease_variants(raw)

    # stages 1-3: exact label, then exact label on a normalised variant, then synonym
    for i, form in enumerate(variants):
        try:
            hits = mondo.search(form, rows=25, exact=True)
        except Exception:  # noqa: BLE001
            continue
        terms = [t for t, _ in hits]

        labels = mondo.label_matches(form, terms)
        if len(labels) == 1:
            m = labels[0]
            method = (
                ResolutionMethod.EXACT_IDENTIFIER if i == 0
                else ResolutionMethod.NORMALISED_EXACT
            )
            return DiseaseResolution(
                raw=raw, normalised=norm, method=method,
                confidence=method.default_confidence,
                mondo_id=m.mondo_id, label=m.label,
                evidence=(
                    f"exact MONDO label match on {form!r}"
                    + ("" if i == 0 else f" (normalised from {raw!r})")
                ),
            )

        syns = mondo.synonym_matches(form, terms)
        if len(syns) == 1:
            m = syns[0]
            return DiseaseResolution(
                raw=raw, normalised=norm, method=ResolutionMethod.EXACT_SYNONYM,
                confidence=ResolutionMethod.EXACT_SYNONYM.default_confidence,
                mondo_id=m.mondo_id, label=m.label,
                evidence=f"exact match on a published MONDO synonym of {m.label!r} via {form!r}",
            )

    # stages 4-5: approximate
    try:
        # rows=25: OLS4 ranks subtypes above the general concept, so a
        # shallow window excludes the right answer entirely. Measured:
        # 'diffuse large B cell lymphoma' surfaces its general term at
        # rank >8 with similarity 1.000.
        approx = mondo.search(norm, rows=25)
    except Exception:  # noqa: BLE001
        approx = []

    cands = tuple(
        Candidate(rxcui=t.mondo_id, name=t.label, score=s, rank=i + 1)
        for i, (t, s) in enumerate(approx)
    )
    strong = [c for c in cands if c.score >= fuzzy_threshold]

    if len(strong) == 1:
        c = strong[0]
        return DiseaseResolution(
            raw=raw, normalised=norm, method=ResolutionMethod.SINGLE_FUZZY_ABOVE_TAU,
            confidence=ResolutionMethod.SINGLE_FUZZY_ABOVE_TAU.default_confidence,
            mondo_id=c.rxcui, label=c.name,
            evidence=(
                f"single MONDO candidate at similarity {c.score:.2f} >= {fuzzy_threshold}; "
                "flagged for review - a manual audit found this band unreliable for "
                "condition strings that name a phenotype rather than a disease"
            ),
            candidates=cands,
            # 0.60 is below the trust-boundary floor (0.75), so this is already
            # ineligible as a rule input. Flagging it keeps that visible in the UI
            # rather than presenting it as settled.
            needs_review=True,
        )

    if cands:
        return DiseaseResolution(
            raw=raw, normalised=norm, method=ResolutionMethod.MULTI_CANDIDATE,
            confidence=ResolutionMethod.MULTI_CANDIDATE.default_confidence,
            mondo_id=cands[0].rxcui, label=cands[0].name,
            evidence=(
                f"{len(cands)} MONDO candidates, best {cands[0].score:.2f}: "
                + ", ".join(f"{c.name}({c.score:.2f})" for c in cands[:3])
            ),
            candidates=cands, needs_review=True,
        )

    return DiseaseResolution(
        raw=raw, normalised=norm, method=ResolutionMethod.MULTI_CANDIDATE,
        confidence=0.0, mondo_id=None, label=None,
        evidence="no MONDO candidate found", needs_review=True,
    )
