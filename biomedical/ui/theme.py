"""The visual language (component 06 R2).

Component 00 R2.7 makes visual hierarchy a *correctness* concern: a derived fact
must not look like a curated fact. So the encoding lives here, once, and every
page uses it. Per-page variation is not permitted.

Each tier carries three independent encodings — border style, glyph and label —
so the distinction survives colour-blindness, greyscale printing and a screenshot
in a slide deck. Colour is the fourth channel, never the only one.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..foundation import Tier


@dataclass(frozen=True)
class TierStyle:
    glyph: str
    label: str
    border: str          # tailwind border utility - the non-colour channel
    colour: str          # fourth channel only
    weight: str
    description: str


TIER_STYLES: dict[str, TierStyle] = {
    "asserted": TierStyle(
        glyph="●", label="authored", border="border-l-4 border-solid",
        colour="border-slate-600", weight="font-semibold",
        description="human-authored, including all synthetic patient and policy data",
    ),
    "ingested": TierStyle(
        glyph="◆", label="ingested", border="border-l-4 border-solid",
        colour="border-sky-600", weight="font-semibold",
        description="from an external source at a pinned version",
    ),
    "derived": TierStyle(
        glyph="◇", label="derived", border="border-l-4 border-dashed",
        colour="border-amber-600", weight="font-normal opacity-80",
        description="inferred; carries method and evidence; reversible",
    ),
    "runtime": TierStyle(
        glyph="○", label="observed", border="border-l-4 border-dotted",
        colour="border-violet-500", weight="font-normal opacity-70",
        description="a record of system behaviour, not knowledge",
    ),
}

UNKNOWN = TierStyle("?", "unstamped", "border-l-4 border-double", "border-red-600",
                    "font-normal", "no provenance - should be impossible")


def tier_style(tier: Tier | str | None) -> TierStyle:
    if tier is None:
        return UNKNOWN
    key = tier.value if isinstance(tier, Tier) else str(tier)
    return TIER_STYLES.get(key, UNKNOWN)


def tier_classes(tier: Tier | str | None) -> str:
    s = tier_style(tier)
    return f"{s.border} {s.colour} {s.weight} pl-3"


# --- overlay states, composable with tier -----------------------------------

@dataclass(frozen=True)
class StateBadge:
    glyph: str
    text: str
    classes: str


STATE_BADGES = {
    "below_floor": StateBadge("⊘", "ineligible: below confidence floor",
                              "bg-red-100 text-red-900 line-through"),
    "stale": StateBadge("⏱", "stale", "bg-orange-100 text-orange-900"),
    "contested": StateBadge("⇄", "contested", "bg-fuchsia-100 text-fuchsia-900"),
    "needs_review": StateBadge("◔", "needs review", "bg-amber-100 text-amber-900"),
    "unlinked": StateBadge("∅", "unlinked", "bg-zinc-200 text-zinc-700"),
    "withdrawn": StateBadge("⤬", "withdrawn identifier", "bg-rose-100 text-rose-900"),
}

OUTCOME_BADGES = {
    "violation": StateBadge("✕", "VIOLATION", "bg-red-600 text-white"),
    "no_violation": StateBadge("✓", "no violation", "bg-emerald-600 text-white"),
    # deliberately NOT green, and deliberately not a tick
    "cannot_evaluate": StateBadge("?", "CANNOT EVALUATE", "bg-amber-500 text-black"),
}

DECISION_BADGES = {
    "BLOCKED": "bg-red-600 text-white",
    "INCONCLUSIVE": "bg-amber-500 text-black",
    "ALLOWED": "bg-emerald-600 text-white",
}


def confidence_classes(conf: float, floor: float) -> str:
    if conf < floor:
        return "text-red-700 line-through"
    if conf < 0.9:
        return "text-amber-700"
    return "text-emerald-800"
