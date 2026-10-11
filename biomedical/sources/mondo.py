"""MONDO disease ontology via EBI OLS4 (no API key).

The resolution target for disease strings, which arrive from ClinicalTrials.gov
as free text with no identifier. Before this adapter existed the graph held six
separate nodes for type 2 diabetes.

OLS4 also publishes cross-references to DOID, MeSH, NCIT and others. Those
disagree with each other in places, so they are recorded rather than adjudicated.
"""

from __future__ import annotations

import difflib
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .adapters import _get

OLS = "https://www.ebi.ac.uk/ols4/api"
NOW = lambda: datetime.now(timezone.utc)  # noqa: E731

FIELDS = "obo_id,label,synonym,ontology_name,is_defining_ontology,short_form"

# MONDO carries veterinary variants of many human conditions, labelled
# "..., non-human animal". A clinical-trial condition string can never correctly
# resolve to one, and the OLS relevance score does not know that. Found by
# manual audit: "Bradycardia" was resolving to "sinus bradycardia, non-human animal".
# MONDO suffixes veterinary variants by species as well as with the generic
# "non-human animal" marker. Found by audit: "type 2 diabetes mellitus, pig"
# was scoring 0.906 against a human trial condition.
NON_HUMAN = re.compile(
    r"non-human animal|,\s*(pig|mouse|mice|rat|dog|cat|cattle|bovine|equine|"
    r"canine|feline|porcine|murine|sheep|horse|chicken|zebrafish)\b",
    re.I,
)


def is_non_human(term: "MondoTerm") -> bool:
    return bool(NON_HUMAN.search(term.label))


@dataclass(frozen=True)
class MondoTerm:
    mondo_id: str
    label: str
    synonyms: tuple[str, ...] = ()
    xrefs: dict[str, tuple[str, ...]] = field(default_factory=dict)
    source_version: str = "ols4"
    retrieved_at: datetime = field(default_factory=NOW)

    def all_labels(self) -> tuple[str, ...]:
        return (self.label,) + self.synonyms


_HYPHEN = re.compile(r"[-\u2010-\u2015/]+")


def _flatten(text: str) -> str:
    """Hyphen and slash variance costs real similarity.

    MONDO writes "diffuse large B-cell lymphoma"; ClinicalTrials.gov writes
    "Diffuse Large B Cell Lymphoma". Without this the general term scored below
    its own organ-specific subtypes. Found by conflict detection.
    """
    return _HYPHEN.sub(" ", text.strip().lower()).strip()


def best_similarity(query: str, term: MondoTerm) -> float:
    """Locally computed similarity in [0, 1].

    **Why this exists.** OLS4 does not return a relevance score - it is `None` on
    every document, with or without `score` in `fieldList`. An earlier version of
    this module defaulted the missing score to 1.0, which silently gave every
    candidate a perfect confidence and made the
    `single_fuzzy_above_threshold` band meaningless. Found by manual audit.

    So similarity is computed here, from the query against the term's label and
    every published synonym, taking the best match. Deterministic and testable.
    """
    q = _flatten(query)
    if not q:
        return 0.0
    best = 0.0
    for candidate in term.all_labels():
        c = _flatten(candidate)
        if not c:
            continue
        ratio = difflib.SequenceMatcher(None, q, c).ratio()
        # A full token-subset match is strong evidence, but only when the
        # candidate is not substantially broader than the query. Without the
        # length guard, "solid tumor" scored 0.85 against
        # "NTRK fusion positive cancer" (whose synonym set contains both tokens).
        qt, ct = set(q.split()), set(c.split())
        if qt and qt <= ct and len(ct) - len(qt) <= 1:
            ratio = max(ratio, 0.85)
        best = max(best, ratio)
    return round(min(1.0, best), 4)


def ols_version() -> str:
    """Pin what we queried. OLS4 does not expose a MONDO release directly, so the
    recorded version is the service plus the date of retrieval."""
    try:
        d = _get(f"{OLS}/ontologies/mondo")
        cfg = d.get("config") or {}
        return str(cfg.get("version") or cfg.get("versionIri") or "ols4-unversioned")
    except Exception:  # noqa: BLE001
        return "ols4-unversioned"


def search(term: str, *, rows: int = 8, exact: bool = False) -> list[tuple[MondoTerm, float]]:
    """Search MONDO. Returns (term, normalised score 0-1) pairs."""
    params = {
        "q": term, "ontology": "mondo", "rows": str(rows),
        "fieldList": FIELDS, "queryFields": "label,synonym",
    }
    if exact:
        params["exact"] = "true"
    data = _get(f"{OLS}/search?{urllib.parse.urlencode(params)}")
    docs = (data.get("response") or {}).get("docs") or []
    if not docs:
        return []
    term_query = term

    out: list[tuple[MondoTerm, float]] = []
    seen: set[str] = set()
    for doc in docs:
        obo = doc.get("obo_id")
        # `ontology=mondo` is not reliably applied by OLS4 - HPO terms come back
        # too - so the prefix check is the real filter.
        if not obo or not obo.startswith("MONDO:") or obo in seen:
            continue
        seen.add(obo)
        syn = doc.get("synonym") or []
        term = MondoTerm(
            mondo_id=obo,
            label=doc.get("label") or "",
            synonyms=tuple(x for x in syn if x),
        )
        if is_non_human(term):
            continue
        out.append((term, best_similarity(term_query, term)))

    # Tie-break toward the LEAST specific term that still covers the query.
    #
    # Found by conflict detection: MONDO's subtypes contain the general concept's
    # tokens and are longer, so similarity alone ranked
    #   "Inflammatory Bowel Disease"    -> "inflammatory bowel disease 13"  (a locus)
    #   "Diffuse Large B Cell Lymphoma" -> "primary pulmonary DLBCL"        (a subtype)
    # above the general term. Among candidates scoring within 0.02, prefer the
    # one with the fewest tokens.
    out.sort(key=lambda pair: (-round(pair[1], 2), len(pair[0].label.split())))
    return out


def exact_label(term: str) -> list[MondoTerm]:
    """Terms whose label or a synonym matches exactly (case-insensitive)."""
    hits = search(term, rows=12, exact=True)
    t = term.strip().lower()
    return [
        m for m, _ in hits
        if m.label.strip().lower() == t
        or any(s.strip().lower() == t for s in m.synonyms)
    ]


def label_matches(term: str, candidates: list[MondoTerm]) -> list[MondoTerm]:
    t = term.strip().lower()
    return [m for m in candidates if m.label.strip().lower() == t]


def synonym_matches(term: str, candidates: list[MondoTerm]) -> list[MondoTerm]:
    t = term.strip().lower()
    return [
        m for m in candidates
        if m.label.strip().lower() != t
        and any(s.strip().lower() == t for s in m.synonyms)
    ]


def fetch_xrefs(mondo_id: str) -> dict[str, tuple[str, ...]]:
    """Cross-references to other disease authorities.

    These are published mappings and they do not always agree with each other.
    We record them; we do not adjudicate between them.
    """
    short = mondo_id.replace(":", "_")
    iri = urllib.parse.quote_plus(f"http://purl.obolibrary.org/obo/{short}")
    try:
        d = _get(f"{OLS}/ontologies/mondo/terms/{iri}")
    except Exception:  # noqa: BLE001
        return {}
    annos = (d.get("annotation") or {})
    raw: list[str] = []
    for key in ("database_cross_reference", "hasDbXref", "xref"):
        vals = annos.get(key) or []
        raw.extend(v for v in vals if isinstance(v, str))

    out: dict[str, list[str]] = {}
    for x in raw:
        if ":" not in x:
            continue
        authority, _, value = x.partition(":")
        out.setdefault(authority.strip().lower(), []).append(value.strip())
    return {k: tuple(dict.fromkeys(v)) for k, v in out.items()}
