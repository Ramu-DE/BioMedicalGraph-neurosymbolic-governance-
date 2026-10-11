"""Cross-source contradiction detection (component 00 R5, component 03 R4).

Four sources with incompatible models will disagree. Having four sources is not
the same as reconciling four sources, and the reviewer's objection was about the
*problems* a practitioner faces — of which this is the central one.

Both sides of every contradiction are retained. Resolution is a recorded act
citing a declared authority, never a silent MERGE that picks a winner. Some
conflicts are deliberately **not** auto-resolvable: where adjudicating requires
domain knowledge rather than source precedence, the detector says so and routes
to a human.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


class ConflictKind(Enum):
    INDICATION_VS_ADVERSE_EVENT = "indication_vs_adverse_event"
    AMBIGUOUS_MENTION = "ambiguous_mention"
    CROSS_AUTHORITY_IDENTITY = "cross_authority_identity"
    PROPERTY_DISAGREEMENT = "property_disagreement"

    @property
    def auto_resolvable(self) -> bool:
        """Can a declared source precedence settle this?

        INDICATION_VS_ADVERSE_EVENT cannot: deciding whether a disease is the
        indication or the adverse event requires clinical knowledge, not a
        ranking of sources. Precedence would produce a confident wrong answer.
        """
        return self in (
            ConflictKind.CROSS_AUTHORITY_IDENTITY,
            ConflictKind.PROPERTY_DISAGREEMENT,
        )


@dataclass(frozen=True)
class ConflictSide:
    source: str
    source_version: str
    claim: str
    evidence: str

    def describe(self) -> str:
        return f"{self.source}@{self.source_version}: {self.claim}"


@dataclass(frozen=True)
class Conflict:
    conflict_id: str
    kind: ConflictKind
    subject: str
    property: str
    sides: tuple[ConflictSide, ...]
    detected_at: datetime
    note: str = ""
    resolution_authority: str | None = None
    resolved_by: str | None = None
    chosen_source: str | None = None

    @property
    def resolved(self) -> bool:
        return self.chosen_source is not None

    @property
    def requires_human(self) -> bool:
        return not self.kind.auto_resolvable

    def describe(self) -> str:
        state = (
            f"RESOLVED -> {self.chosen_source} ({self.resolution_authority})"
            if self.resolved
            else ("UNRESOLVED - requires a human" if self.requires_human else "UNRESOLVED")
        )
        lines = [f"[{self.kind.value}] {self.subject}.{self.property}  {state}"]
        lines.extend(f"    {s.describe()}" for s in self.sides)
        if self.note:
            lines.append(f"    note: {self.note}")
        return "\n".join(lines)


def _cid(kind: ConflictKind, subject: str, prop: str) -> str:
    h = hashlib.sha256(f"{kind.value}|{subject}|{prop}".encode()).hexdigest()[:12]
    return f"cf_{h}"


# --------------------------------------------------------------------------- #
# detector 1 - indication vs adverse event  (the interesting one)
# --------------------------------------------------------------------------- #

INDICATION_VS_AE = """
MATCH (t:ClinicalTrial)-[:INVESTIGATES]->(d:Drug)
MATCH (t)-[:STUDIES]->(dis:Disease)
MATCH (d)-[a:ASSOCIATED_WITH_REACTION]->(rx:Reaction)
WITH d, dis, rx, a,
     toLower(coalesce(dis.name,'')) AS dn, toLower(coalesce(rx.term,'')) AS rn
WHERE dn <> '' AND rn <> ''
  AND (dn CONTAINS rn OR rn CONTAINS dn
       OR last(split(dn,' ')) = last(split(rn,' ')))
RETURN DISTINCT d.entity_id AS drug_id, d.canonical_name AS drug,
       dis.name AS indication, dis.mondo_id AS mondo_id,
       rx.term AS reaction, a.report_count AS reports
"""


def detect_indication_vs_adverse_event(client) -> list[Conflict]:
    """A drug investigated *for* a disease, and co-reported *with* it as a reaction.

    Read naively, the FAERS edge says the drug caused the disease. But a melanoma
    chemotherapy appears alongside "Malignant melanoma" in adverse-event reports
    because the patients *have* melanoma — that is confounding by indication, and
    no amount of source precedence detects it. Clinical judgment does.
    """
    out: list[Conflict] = []
    for r in client.run(INDICATION_VS_AE):
        subject = r["drug"] or r["drug_id"]
        prop = f"relationship_to::{r['reaction']}"
        out.append(
            Conflict(
                conflict_id=_cid(ConflictKind.INDICATION_VS_ADVERSE_EVENT, subject, prop),
                kind=ConflictKind.INDICATION_VS_ADVERSE_EVENT,
                subject=subject,
                property=prop,
                sides=(
                    ConflictSide(
                        source="clinicaltrials.gov", source_version="v2",
                        claim=f"indication: the drug is investigated FOR {r['indication']!r}",
                        evidence=f"INVESTIGATES + STUDIES via trials; MONDO {r['mondo_id']}",
                    ),
                    ConflictSide(
                        source="openfda.faers", source_version="faers",
                        claim=f"co-reported WITH reaction {r['reaction']!r}",
                        evidence=f"{r['reports']} co-reports in FAERS",
                    ),
                ),
                detected_at=NOW(),
                note=(
                    "Probable confounding by indication. FAERS has no denominator and no "
                    "control group, so co-occurrence is not evidence of causation. Source "
                    "precedence cannot settle this — it needs clinical review."
                ),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# detector 2 - ambiguous mention
# --------------------------------------------------------------------------- #

AMBIGUOUS = """
MATCH (m:Mention)-[c:CANDIDATE_FOR]->(e)
WHERE c.score >= $threshold
WITH m, collect({name: coalesce(e.canonical_name, e.name),
                 id: coalesce(e.mondo_id, e.rxcui, e.entity_id),
                 score: c.score}) AS cands
WHERE size(cands) > 1
OPTIONAL MATCH (m)-[r:RESOLVES_TO]->(chosen)
RETURN m.raw_text AS raw, m.source AS source, cands,
       coalesce(chosen.canonical_name, chosen.name) AS chosen,
       r.method AS method, r.confidence AS confidence
ORDER BY size(cands) DESC
"""


def detect_ambiguous_mentions(client, *, threshold: float = 0.6) -> list[Conflict]:
    """One surface form with several plausible targets.

    The resolver already retains every candidate and flags the mention for
    review. Promoting it to a Conflict makes the ambiguity countable and visible
    on the board rather than buried in an edge property.
    """
    out: list[Conflict] = []
    for r in client.run(AMBIGUOUS, threshold=threshold):
        cands = sorted(r["cands"], key=lambda c: -(c["score"] or 0))[:4]
        out.append(
            Conflict(
                conflict_id=_cid(ConflictKind.AMBIGUOUS_MENTION, r["raw"], "identity"),
                kind=ConflictKind.AMBIGUOUS_MENTION,
                subject=r["raw"],
                property="identity",
                sides=tuple(
                    ConflictSide(
                        source=r["source"] or "unknown", source_version="-",
                        claim=f"{c['name']} ({c['id']})",
                        evidence=f"similarity {c['score']:.2f}",
                    )
                    for c in cands
                ),
                detected_at=NOW(),
                note=(
                    f"{len(r['cands'])} candidates at or above {threshold}. "
                    f"Currently resolved to {r['chosen']!r} via {r['method']} "
                    f"at confidence {r['confidence']} — below the trust floor, so "
                    "ineligible as a rule input until reviewed."
                ),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# detector 3 - cross-authority identity
# --------------------------------------------------------------------------- #

CROSS_AUTHORITY = """
MATCH (a:Disease)-[:HAS_IDENTIFIER]->(i:Identifier)<-[:HAS_IDENTIFIER]-(b:Disease)
WHERE a.entity_id < b.entity_id
RETURN i.authority AS authority, i.value AS value,
       a.name AS a_name, a.mondo_id AS a_id,
       b.name AS b_name, b.mondo_id AS b_id
LIMIT 50
"""


def detect_cross_authority_identity(client) -> list[Conflict]:
    """Two distinct canonical entities claiming the same external identifier.

    Published cross-references between disease authorities disagree, so this is
    expected rather than exceptional. It is auto-resolvable by precedence because
    the question is which authority to trust, not what is clinically true.
    """
    out: list[Conflict] = []
    for r in client.run(CROSS_AUTHORITY):
        subject = f"{r['authority']}:{r['value']}"
        out.append(
            Conflict(
                conflict_id=_cid(ConflictKind.CROSS_AUTHORITY_IDENTITY, subject, "maps_to"),
                kind=ConflictKind.CROSS_AUTHORITY_IDENTITY,
                subject=subject,
                property="maps_to",
                sides=(
                    ConflictSide("mondo", "ols4", f"{r['a_name']} ({r['a_id']})",
                                 f"shares {subject}"),
                    ConflictSide("mondo", "ols4", f"{r['b_name']} ({r['b_id']})",
                                 f"shares {subject}"),
                ),
                detected_at=NOW(),
                note="two canonical entities claim one external identifier",
            )
        )
    return out


# --------------------------------------------------------------------------- #
# persistence
# --------------------------------------------------------------------------- #

WRITE = """
UNWIND $rows AS row
MERGE (c:Conflict:Provenanced {conflict_id: row.conflict_id})
  SET c.kind = row.kind, c.subject = row.subject, c.property = row.property,
      c.note = row.note, c.detected_at = row.detected_at,
      c.requires_human = row.requires_human,
      c.auto_resolvable = row.auto_resolvable,
      c.sides = row.sides,
      c.source = 'conflict-detector', c.source_version = '1.0',
      c.retrieved_at = row.detected_at, c.tier = 'derived',
      c.resolution_authority = row.resolution_authority,
      c.chosen_source = row.chosen_source
"""


def persist(client, conflicts: list[Conflict]) -> int:
    if not conflicts:
        return 0
    rows = [
        {
            "conflict_id": c.conflict_id, "kind": c.kind.value, "subject": c.subject,
            "property": c.property, "note": c.note,
            "detected_at": c.detected_at.isoformat(),
            "requires_human": c.requires_human,
            "auto_resolvable": c.kind.auto_resolvable,
            "sides": [s.describe() for s in c.sides],
            "resolution_authority": c.resolution_authority,
            "chosen_source": c.chosen_source,
        }
        for c in conflicts
    ]
    client.run(WRITE, rows=rows)
    return len(rows)


def mark_affected_ineligible(client) -> int:
    """A fact under unresolved contradiction defaults to ineligible as a rule
    input (component 00 R5.4)."""
    rows = client.run(
        """
        MATCH (c:Conflict) WHERE c.chosen_source IS NULL
        MATCH (m:Mention) WHERE m.raw_text = c.subject
        MATCH (m)-[r:RESOLVES_TO]->()
          SET r.contested = true
        RETURN count(r) AS v
        """
    )
    return rows[0]["v"] if rows else 0


def summarise(conflicts: list[Conflict]) -> dict:
    by_kind: dict[str, int] = {}
    for c in conflicts:
        by_kind[c.kind.value] = by_kind.get(c.kind.value, 0) + 1
    return {
        "total": len(conflicts),
        "by_kind": by_kind,
        "requires_human": sum(1 for c in conflicts if c.requires_human),
        "auto_resolvable": sum(1 for c in conflicts if c.kind.auto_resolvable),
        "resolved": sum(1 for c in conflicts if c.resolved),
    }
