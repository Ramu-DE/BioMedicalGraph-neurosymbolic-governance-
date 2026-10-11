"""Render-time refusals (component 06 R5.2, R7.4).

The UI refuses to mislead. A bare proportion cannot reach the screen; an
unregistered claim does not render; a conformance result cannot be described as
truth.

This is the same enforcement-over-documentation pattern as the foundation lint
pass, applied at render time — it is what keeps the discipline alive after the
first month.
"""

from __future__ import annotations

from nicegui import ui

from ..foundation import ClaimsRegister


def error_panel(message: str) -> None:
    with ui.element("div").classes(
        "border-2 border-red-600 bg-red-50 text-red-900 p-3 rounded my-2 text-sm"
    ):
        ui.label("RENDER REFUSED").classes("font-bold")
        ui.label(message)


def render_rate(
    label: str,
    value: float | None,
    *,
    k: int | None = None,
    n: int | None = None,
    interval: tuple[float, float] | None = None,
    note: str = "",
) -> None:
    """A rate must arrive with its denominator and interval, or not at all."""
    if value is None or n is None or interval is None:
        error_panel(
            f"'{label}': refusing to render a rate without n and a confidence interval. "
            "A bare proportion hides whether the number means anything."
        )
        return

    lo, hi = interval
    with ui.row().classes("items-baseline gap-2 w-full"):
        ui.label(label).classes("text-sm text-zinc-600 w-44")
        ui.label(f"{value:.3f}").classes("text-lg font-mono font-semibold")
        ui.label(f"[{lo:.3f}, {hi:.3f}]").classes("text-sm font-mono text-zinc-500")
        kn = f"k={k}, n={n}" if k is not None else f"n={n}"
        ui.label(kn).classes("text-xs text-zinc-500")
        if note:
            ui.label(f"← {note}").classes("text-xs text-amber-800 italic")

    # the interval as a visible mark, not a tooltip (R5.8)
    with ui.element("div").classes("w-full h-2 bg-zinc-200 rounded relative mb-2"):
        ui.element("div").classes("absolute h-2 bg-sky-400 rounded").style(
            f"left:{lo * 100:.1f}%; width:{max(0.6, (hi - lo) * 100):.1f}%"
        )
        ui.element("div").classes("absolute h-2 w-0.5 bg-zinc-900").style(
            f"left:{value * 100:.1f}%"
        )


def render_claim_linked_number(
    register: ClaimsRegister, claim_id: str, text: str
) -> None:
    """A number tied to a published claim must link to its limits (R7.3)."""
    try:
        claim = register.get(claim_id)
    except Exception as exc:  # noqa: BLE001
        error_panel(f"{exc}")
        return
    with ui.row().classes("items-center gap-2"):
        ui.label(text).classes("font-mono")
        with ui.icon("info").classes("text-sky-700 cursor-help"):
            ui.tooltip(f"Does not establish: {claim.does_not_establish}")


def conformance_note() -> None:
    """SHACL conformance is never truth (component 00 R6.2)."""
    ui.label(
        "Shape conformance establishes structural conformance only. "
        "It does not establish that any value is factually correct."
    ).classes("text-xs text-zinc-600 italic")
