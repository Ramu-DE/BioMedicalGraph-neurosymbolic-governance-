"""Real source adapters (component 03).

Five open sources, no API key required. Every record is emitted with a
`ProvenanceStamp`, so nothing can enter the graph unprovenanced.

    ClinicalTrials.gov v2  real trials: NCT, phase, status, free-text interventions
    openFDA FAERS          real adverse-event reports, human-entered drug names
    RxNav / RxNorm         the resolution target: RXCUIs, brand <-> generic
    HGNC                   gene symbol authority, including *previous* symbols
    ChEMBL                 molecules and mechanisms

The free-text intervention and drug-name fields are the point: that is messiness
we did not author, which is what makes entity resolution a real problem.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

UA = "BioMedicalContextLayer/0.1 (research; contact via repo)"
NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


def _get(url: str, *, timeout: int = 30, retries: int = 3) -> dict:
    """Stdlib JSON GET with polite retry. No extra dependency."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                return json.loads(r.read().decode())
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} attempts: {url[:90]} :: {last}")


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Mention:
    """A raw string as the source gave it. Never modified after capture."""

    source: str
    source_record_id: str
    raw_text: str
    mention_type: str
    source_version: str
    retrieved_at: datetime = field(default_factory=NOW)


@dataclass(frozen=True)
class TrialRecord:
    nct_id: str
    title: str
    phase: str
    status: str
    enrollment: int | None
    conditions: tuple[str, ...]
    interventions: tuple[str, ...]
    source_version: str
    retrieved_at: datetime = field(default_factory=NOW)


@dataclass(frozen=True)
class AeRecord:
    report_id: str
    drug_names: tuple[str, ...]
    reactions: tuple[str, ...]
    serious: bool
    source_version: str
    retrieved_at: datetime = field(default_factory=NOW)


@dataclass(frozen=True)
class RxConcept:
    rxcui: str
    name: str
    tty: str
    source_version: str
    retrieved_at: datetime = field(default_factory=NOW)


@dataclass(frozen=True)
class GeneRecord:
    hgnc_id: str
    symbol: str
    name: str
    prev_symbols: tuple[str, ...]
    source_version: str
    retrieved_at: datetime = field(default_factory=NOW)


# --------------------------------------------------------------------------- #
# ClinicalTrials.gov API v2  (no key)
# --------------------------------------------------------------------------- #

CT_BASE = "https://clinicaltrials.gov/api/v2/studies"


def fetch_trials(condition: str, *, limit: int = 60, source_version: str = "v2") -> list[TrialRecord]:
    out: list[TrialRecord] = []
    token: str | None = None
    while len(out) < limit:
        params = {
            "query.cond": condition,
            "pageSize": str(min(100, limit - len(out))),
            "fields": "NCTId|BriefTitle|Phase|OverallStatus|EnrollmentCount|Condition|InterventionName",
        }
        if token:
            params["pageToken"] = token
        data = _get(f"{CT_BASE}?{urllib.parse.urlencode(params)}")
        for s in data.get("studies", []):
            p = s.get("protocolSection", {})
            ident = p.get("identificationModule", {})
            design = p.get("designModule", {})
            status = p.get("statusModule", {})
            cond = p.get("conditionsModule", {})
            arms = p.get("armsInterventionsModule", {})
            phases = design.get("phases") or []
            out.append(
                TrialRecord(
                    nct_id=ident.get("nctId", ""),
                    title=(ident.get("briefTitle") or "")[:300],
                    phase=", ".join(phases) or "NA",
                    status=status.get("overallStatus", "UNKNOWN"),
                    enrollment=(design.get("enrollmentInfo") or {}).get("count"),
                    conditions=tuple((cond.get("conditions") or [])[:6]),
                    interventions=tuple(
                        i.get("name", "") for i in (arms.get("interventions") or [])[:6] if i.get("name")
                    ),
                    source_version=source_version,
                )
            )
        token = data.get("nextPageToken")
        if not token:
            break
    return [t for t in out if t.nct_id][:limit]


# --------------------------------------------------------------------------- #
# openFDA FAERS  (key optional; works unauthenticated at 1k/day)
# --------------------------------------------------------------------------- #

FDA_BASE = "https://api.fda.gov/drug/event.json"


def fetch_adverse_events(
    search: str, *, limit: int = 60, api_key: str | None = None, source_version: str = "faers"
) -> list[AeRecord]:
    params = {"search": search, "limit": str(min(100, limit))}
    if api_key:
        params["api_key"] = api_key
    data = _get(f"{FDA_BASE}?{urllib.parse.urlencode(params)}")
    out: list[AeRecord] = []
    for r in data.get("results", []):
        patient = r.get("patient", {}) or {}
        drugs = patient.get("drug", []) or []
        names: list[str] = []
        for d in drugs[:8]:
            # medicinalproduct is the human-entered field - brands, misspellings, doses
            nm = (d.get("medicinalproduct") or "").strip()
            if nm:
                names.append(nm)
        reactions = tuple(
            (x.get("reactionmeddrapt") or "").strip()
            for x in (patient.get("reaction") or [])[:6]
            if x.get("reactionmeddrapt")
        )
        rid = r.get("safetyreportid") or r.get("receiptdate", "") + str(len(out))
        if names:
            out.append(
                AeRecord(
                    report_id=str(rid),
                    drug_names=tuple(dict.fromkeys(names)),
                    reactions=reactions,
                    serious=str(r.get("serious", "")) == "1",
                    source_version=source_version,
                )
            )
    return out


# --------------------------------------------------------------------------- #
# RxNav / RxNorm  (no key) - the resolution target
# --------------------------------------------------------------------------- #

RX_BASE = "https://rxnav.nlm.nih.gov/REST"


def rxnorm_version() -> str:
    try:
        return _get(f"{RX_BASE}/version.json").get("version", "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def rx_exact(name: str) -> list[RxConcept]:
    """Exact normalised name lookup."""
    q = urllib.parse.urlencode({"name": name, "search": "0"})
    data = _get(f"{RX_BASE}/rxcui.json?{q}")
    ids = ((data.get("idGroup") or {}).get("rxnormId")) or []
    return [RxConcept(rxcui=i, name=name, tty="EXACT", source_version="rxnav") for i in ids]


def rx_approximate(name: str, *, max_entries: int = 4) -> list[tuple[RxConcept, float]]:
    """Approximate match. Returns (concept, normalised score 0-1) candidates."""
    q = urllib.parse.urlencode({"term": name, "maxEntries": str(max_entries)})
    data = _get(f"{RX_BASE}/approximateTerm.json?{q}")
    cands = ((data.get("approximateGroup") or {}).get("candidate")) or []
    out: list[tuple[RxConcept, float]] = []
    seen: set[str] = set()
    for c in cands:
        rxcui = c.get("rxcui")
        if not rxcui or rxcui in seen:
            continue
        seen.add(rxcui)
        out.append(
            (
                RxConcept(
                    rxcui=rxcui,
                    name=c.get("name") or "",
                    tty=c.get("tty") or "",
                    source_version="rxnav",
                ),
                min(1.0, float(c.get("score", 0)) / 100.0),
            )
        )
    return out


def rx_canonical_name(rxcui: str) -> str:
    try:
        data = _get(f"{RX_BASE}/rxcui/{rxcui}/property.json?propName=RxNorm%20Name")
        props = ((data.get("propConceptGroup") or {}).get("propConcept")) or []
        return props[0].get("propValue", "") if props else ""
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------- #
# HGNC  (no key) - gene symbols, including PREVIOUS symbols (schema evolution)
# --------------------------------------------------------------------------- #

HGNC_BASE = "https://rest.genenames.org"


def fetch_gene(symbol: str, *, source_version: str = "hgnc-current") -> GeneRecord | None:
    data = _get(f"{HGNC_BASE}/fetch/symbol/{urllib.parse.quote(symbol)}")
    docs = ((data.get("response") or {}).get("docs")) or []
    if not docs:
        return None
    d = docs[0]
    return GeneRecord(
        hgnc_id=d.get("hgnc_id", ""),
        symbol=d.get("symbol", ""),
        name=d.get("name", ""),
        prev_symbols=tuple(d.get("prev_symbol") or []),
        source_version=source_version,
    )


def fetch_genes(symbols: list[str], *, source_version: str = "hgnc-current") -> list[GeneRecord]:
    out = []
    for s in symbols:
        try:
            g = fetch_gene(s, source_version=source_version)
            if g:
                out.append(g)
        except Exception:  # noqa: BLE001
            continue
    return out


# --------------------------------------------------------------------------- #
# ChEMBL  (no key) - mechanism of action
# --------------------------------------------------------------------------- #

CHEMBL_BASE = "https://www.ebi.ac.uk/chembl/api/data"


def fetch_chembl_mechanism(pref_name: str, *, source_version: str = "chembl-api") -> dict | None:
    q = urllib.parse.urlencode({"pref_name__iexact": pref_name, "limit": 1, "format": "json"})
    data = _get(f"{CHEMBL_BASE}/molecule.json?{q}")
    mols = data.get("molecules") or []
    if not mols:
        return None
    m = mols[0]
    chembl_id = m.get("molecule_chembl_id")
    mech = None
    try:
        md = _get(
            f"{CHEMBL_BASE}/mechanism.json?"
            + urllib.parse.urlencode({"molecule_chembl_id": chembl_id, "limit": 1, "format": "json"})
        )
        mechs = md.get("mechanisms") or []
        mech = mechs[0] if mechs else None
    except Exception:  # noqa: BLE001
        pass
    return {
        "chembl_id": chembl_id,
        "pref_name": m.get("pref_name"),
        "max_phase": m.get("max_phase"),
        "mechanism_of_action": (mech or {}).get("mechanism_of_action"),
        "target_chembl_id": (mech or {}).get("target_chembl_id"),
        "source_version": source_version,
    }
