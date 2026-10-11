"""Vocabulary release differences (schema-evolution spec R2).

Classifies what changed between two states of an external vocabulary, so that a
rule depending on a moved identifier can be detected rather than silently
returning nothing.

**What the evidence actually is.** HGNC publishes, for each gene, the symbols it
was previously known by (`prev_symbol`). That field is the authoritative record
that a rename occurred. This module reconstructs the two states from it:

    release "historical"  the gene was called CDKN2
    release "current"     the gene is called CDKN2A

That is a real, authoritative rename from a live source. It is **not** a diff of
two downloaded dated release files, and this module does not claim to be one. A
true file-to-file diff would additionally establish *when* each change landed,
which `prev_symbol` alone does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ChangeKind(Enum):
    UNCHANGED = "unchanged"
    RENAMED = "renamed"
    WITHDRAWN = "withdrawn"
    OBSOLETED_WITH_REPLACEMENT = "obsoleted_with_replacement"
    OBSOLETED_ORPHAN = "obsoleted_orphan"

    @property
    def breaks_dependents(self) -> bool:
        """Does a rule citing the old identifier stop working?"""
        return self is not ChangeKind.UNCHANGED


@dataclass(frozen=True)
class SymbolChange:
    authority: str
    old_value: str
    new_value: str | None
    change_kind: ChangeKind
    stable_id: str | None = None
    entity_name: str | None = None
    release_from: str = "historical"
    release_to: str = "current"

    @property
    def recoverable(self) -> bool:
        """An orphan obsoletion has no automatic path forward and needs a human."""
        return self.new_value is not None

    def describe(self) -> str:
        arrow = f"-> {self.new_value}" if self.new_value else "-> (no replacement)"
        return (
            f"{self.authority}:{self.old_value} {arrow} "
            f"[{self.change_kind.value}] {self.stable_id or ''}"
        )


@dataclass(frozen=True)
class ReleaseDiff:
    authority: str
    release_from: str
    release_to: str
    changes: tuple[SymbolChange, ...]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.changes:
            out[c.change_kind.value] = out.get(c.change_kind.value, 0) + 1
        return out

    def moved_identifiers(self) -> dict[str, SymbolChange]:
        """old identifier -> the change that moved it."""
        return {c.old_value: c for c in self.changes if c.change_kind.breaks_dependents}

    def orphans(self) -> tuple[SymbolChange, ...]:
        return tuple(c for c in self.changes if not c.recoverable)

    def describe(self) -> str:
        lines = [
            f"{self.authority}: {self.release_from} -> {self.release_to}",
            f"  {len(self.changes)} changed identifier(s)  {self.counts()}",
        ]
        lines.extend(f"  {c.describe()}" for c in self.changes)
        return "\n".join(lines)


def diff_from_hgnc_records(records) -> ReleaseDiff:
    """Build a diff from live HGNC records carrying `prev_symbols`."""
    changes: list[SymbolChange] = []
    for g in records:
        for prev in getattr(g, "prev_symbols", ()) or ():
            if not prev or prev == g.symbol:
                continue
            changes.append(
                SymbolChange(
                    authority="hgnc-symbol",
                    old_value=prev,
                    new_value=g.symbol,
                    change_kind=ChangeKind.RENAMED,
                    stable_id=g.hgnc_id,
                    entity_name=g.name,
                )
            )
    return ReleaseDiff(
        authority="hgnc-symbol", release_from="historical", release_to="current",
        changes=tuple(sorted(changes, key=lambda c: (c.new_value or "", c.old_value))),
    )


def diff_from_graph(client) -> ReleaseDiff:
    """Read the diff back out of the persisted graph."""
    rows = client.run(
        """
        MATCH (old:Identifier {authority:'hgnc-symbol'})-[r:REPLACED_BY]->(g:Gene)
        RETURN old.value AS old_value, g.symbol AS new_value,
               r.change_kind AS kind, g.hgnc_id AS stable_id,
               g.name AS entity_name, g.source_version AS release
        ORDER BY new_value, old_value
        """
    )
    changes = tuple(
        SymbolChange(
            authority="hgnc-symbol",
            old_value=r["old_value"],
            new_value=r["new_value"],
            change_kind=ChangeKind(r["kind"] or "renamed"),
            stable_id=r["stable_id"],
            entity_name=r["entity_name"],
            release_to=r["release"] or "current",
        )
        for r in rows
    )
    return ReleaseDiff(
        authority="hgnc-symbol", release_from="historical",
        release_to="current", changes=changes,
    )


def diff_pair(
    before: dict[str, str], after: dict[str, str], *, authority: str,
    release_from: str, release_to: str, replacements: dict[str, str] | None = None,
) -> ReleaseDiff:
    """Generic two-state diff: {stable_id: symbol} before and after.

    This is the path a real file-to-file release diff would take, and it is what
    the MONDO adapter will use once built.
    """
    replacements = replacements or {}
    changes: list[SymbolChange] = []
    for stable_id, old_symbol in before.items():
        if stable_id not in after:
            repl = replacements.get(stable_id)
            changes.append(
                SymbolChange(
                    authority=authority, old_value=old_symbol, new_value=repl,
                    change_kind=(
                        ChangeKind.OBSOLETED_WITH_REPLACEMENT if repl
                        else ChangeKind.OBSOLETED_ORPHAN
                    ),
                    stable_id=stable_id, release_from=release_from, release_to=release_to,
                )
            )
        elif after[stable_id] != old_symbol:
            changes.append(
                SymbolChange(
                    authority=authority, old_value=old_symbol,
                    new_value=after[stable_id], change_kind=ChangeKind.RENAMED,
                    stable_id=stable_id, release_from=release_from, release_to=release_to,
                )
            )
    return ReleaseDiff(
        authority=authority, release_from=release_from, release_to=release_to,
        changes=tuple(changes),
    )
