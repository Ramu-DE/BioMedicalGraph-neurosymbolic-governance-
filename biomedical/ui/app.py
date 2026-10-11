"""Inspection surface (component 06).

Seven surfaces, one visual language, no business logic.

    .venv/bin/python -m biomedical.ui            # then open http://localhost:8080
"""

from __future__ import annotations

import sys
from pathlib import Path

from nicegui import ui

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402

from ..foundation import (  # noqa: E402
    ClaimCategory,
    Fact,
    ProvenanceStamp,
    ResolutionMethod,
    ReviewState,
    RuleOutcome,
    RuleResult,
    Severity,
    Tier,
    Timings,
    TrustPolicy,
    ValidityInterval,
    Verdict,
    assemble,
)
from . import data, guards
from .theme import (
    DECISION_BADGES,
    OUTCOME_BADGES,
    STATE_BADGES,
    TIER_STYLES,
    confidence_classes,
    tier_classes,
    tier_style,
)

PAGES = [
    ("/", "Overview", "dashboard"),
    ("/review", "Review queue", "fact_check"),
    ("/entities", "Provenance", "account_tree"),
    ("/decision", "Decision trace", "gavel"),
    ("/eval", "Evaluation", "science"),
    ("/conflicts", "Contradictions", "compare_arrows"),
    ("/releases", "Schema evolution", "update"),
    ("/claims", "Claims register", "balance"),
]


def shell(active: str, title: str):
    ui.colors(primary="#0369a1")
    with ui.header().classes("items-center justify-between bg-slate-800 px-4 py-2"):
        with ui.row().classes("items-center gap-3"):
            ui.label("BioMedical Context Layer").classes("text-lg font-semibold text-white")
            ui.label("inspection surface").classes("text-xs text-slate-300 italic")
        cfg = config.load()
        mode = "live graph" if cfg.neo4j.configured else "FIXTURE MODE"
        ui.label(mode).classes(
            "text-xs px-2 py-1 rounded "
            + ("bg-emerald-600 text-white" if cfg.neo4j.configured else "bg-amber-500 text-black")
        )
    with ui.left_drawer(value=True).classes("bg-slate-100 p-2 w-60"):
        for path, label, icon in PAGES:
            cls = "w-full justify-start " + (
                "bg-sky-200 font-semibold" if path == active else "bg-transparent"
            )
            ui.button(label, icon=icon, on_click=lambda p=path: ui.navigate.to(p)).props(
                "flat no-caps align=left"
            ).classes(cls)
        ui.separator()
        ui.label("tier legend").classes("text-xs font-semibold mt-2 text-zinc-600")
        for key, st in TIER_STYLES.items():
            with ui.element("div").classes(f"{tier_classes(key)} my-1 text-xs"):
                ui.label(f"{st.glyph} {st.label}").classes("font-mono")
                ui.label(st.description).classes("text-[10px] text-zinc-500 leading-tight")
    ui.label(title).classes("text-2xl font-bold mt-2 mb-1")


def kpi(label: str, value, hint: str = "") -> None:
    with ui.card().classes("p-3 min-w-36"):
        ui.label(str(value)).classes("text-2xl font-bold font-mono")
        ui.label(label).classes("text-xs text-zinc-600")
        if hint:
            ui.label(hint).classes("text-[10px] text-zinc-500 italic")


def badge(spec, extra: str = "") -> None:
    ui.label(f"{spec.glyph} {spec.text}").classes(
        f"text-xs px-2 py-0.5 rounded {spec.classes} {extra}"
    )


# =========================================================================== #
# 1. overview
# =========================================================================== #


@ui.page("/")
def page_overview():
    shell("/", "Overview")
    if not data.graph_available():
        guards.error_panel("Neo4j is not configured; cannot render the live graph.")
        return
    try:
        o = data.overview()
        bl = data.backlog_counts()
        bands = data.resolution_bands()
    except Exception as exc:  # noqa: BLE001
        guards.error_panel(f"could not load the graph: {exc}")
        return

    with ui.row().classes("gap-3 flex-wrap"):
        kpi("nodes", f"{o['nodes']:,}")
        kpi("relationships", f"{o['rels']:,}")
        kpi("sources", len(o["sources"]), "independent, live")
        kpi("resolutions", bl["resolved"])
        kpi("needs review", bl["needs_review"],
            f"{bl['needs_review'] / max(1, bl['resolved']):.0%} of resolutions")
        kpi("unlinked", bl["unlinked"])

    ui.label("A visible backlog is published deliberately — it is more credible than none.") \
        .classes("text-xs text-zinc-600 italic mt-1")

    with ui.row().classes("gap-4 w-full mt-4 items-start flex-wrap"):
        with ui.card().classes("p-3 grow"):
            ui.label("nodes by label").classes("font-semibold text-sm mb-1")
            ui.table(
                columns=[{"name": "label", "label": "label", "field": "label", "align": "left"},
                         {"name": "count", "label": "count", "field": "count"}],
                rows=o["labels"], row_key="label",
            ).props("dense flat").classes("w-full")
        with ui.card().classes("p-3 grow"):
            ui.label("relationships by type").classes("font-semibold text-sm mb-1")
            ui.table(
                columns=[{"name": "type", "label": "type", "field": "type", "align": "left"},
                         {"name": "count", "label": "count", "field": "count"}],
                rows=o["rels_by_type"], row_key="type",
            ).props("dense flat").classes("w-full")

    with ui.card().classes("p-3 w-full mt-3"):
        ui.label("provenance tiers — every node carries one").classes("font-semibold text-sm")
        for t in o["tiers"]:
            with ui.element("div").classes(f"{tier_classes(t['tier'])} my-1"):
                st = tier_style(t["tier"])
                ui.label(f"{st.glyph} {st.label}  —  {t['count']:,} nodes").classes("text-sm")

    with ui.card().classes("p-3 w-full mt-3"):
        ui.label("sources").classes("font-semibold text-sm mb-1")
        ui.table(
            columns=[
                {"name": "source", "label": "source", "field": "source", "align": "left"},
                {"name": "version", "label": "pinned version", "field": "version", "align": "left"},
                {"name": "count", "label": "nodes", "field": "count"},
            ],
            rows=o["sources"], row_key="source",
        ).props("dense flat").classes("w-full")

    with ui.card().classes("p-3 w-full mt-3"):
        ui.label("entity resolution — confidence derived from method, never authored") \
            .classes("font-semibold text-sm mb-1")
        for b in bands:
            with ui.row().classes("items-center gap-3 w-full"):
                ui.label(str(b["method"])).classes("font-mono text-sm w-72")
                ui.label(f"n={b['count']}").classes("text-sm w-16")
                ui.label(f"conf={b['confidence']}").classes(
                    "text-sm " + confidence_classes(float(b["confidence"] or 0), 0.75)
                )
                if float(b["confidence"] or 0) < 0.75:
                    badge(STATE_BADGES["below_floor"])

    with ui.card().classes("p-3 w-full mt-3 border-l-4 border-amber-600"):
        ui.label("two problems the real data exposed").classes("font-semibold text-sm")
        ui.markdown(
            "**1. Disease identity is unresolved.** `Type 2 Diabetes` and "
            "`Diabetes Mellitus, Type 2` are two nodes for one disease. Drugs were "
            "resolved against RxNorm; disease strings were not. Visible in the data "
            "rather than argued about.\n\n"
            "**2. `ASSOCIATED_WITH_REACTION` is co-reporting, not causation.** FAERS has "
            "no denominator, no controls and heavy reporting bias. These edges are "
            "`tier: derived` so they cannot be mistaken for evidence of harm. "
            "**No causal claim may be made from them.**"
        ).classes("text-xs")


# =========================================================================== #
# 2. review queue - the only writing surface
# =========================================================================== #


@ui.page("/review")
def page_review():
    shell("/review", "Resolution review queue")
    reviewer = {"name": "reviewer"}
    try:
        bl = data.backlog_counts()
        rows = data.review_queue(limit=60)
    except Exception as exc:  # noqa: BLE001
        guards.error_panel(f"could not load the queue: {exc}")
        return

    with ui.row().classes("gap-3 items-end"):
        kpi("awaiting review", bl["needs_review"])
        kpi("cleared", bl["cleared"])
        kpi("rejected", bl["rejected"])
        kpi("superseded", bl.get("superseded", 0), "retained, never deleted")
        ui.input("reviewer", value="reviewer").bind_value(reviewer, "name").props("dense outlined")

    ui.label(
        "Accepting supersedes the prior resolution — it never overwrites it. "
        "Low-confidence mappings stay ineligible as rule inputs until cleared."
    ).classes("text-xs text-zinc-600 italic my-2")

    container = ui.column().classes("w-full gap-2")
    cand_cache: dict[str, list[dict]] = {}

    def refresh():
        container.clear()
        try:
            fresh = data.review_queue(limit=40)
            cand_cache.clear()
            cand_cache.update(data.candidates_bulk([r["raw"] for r in fresh]))
        except Exception as exc:  # noqa: BLE001
            with container:
                guards.error_panel(str(exc))
            return
        if not fresh:
            with container:
                ui.label("Queue empty — every resolution has been reviewed.").classes(
                    "text-sm text-emerald-800"
                )
            return
        with container:
            for r in fresh:
                render_item(r)

    def render_item(r: dict):
        with ui.card().classes("p-3 w-full border-l-4 border-dashed border-amber-600"):
            with ui.row().classes("items-center justify-between w-full"):
                with ui.column().classes("gap-0"):
                    ui.label(r["raw"]).classes("font-mono text-sm font-semibold")
                    ui.label(
                        f"{r['source']} · {r.get('kind') or '-'} · normalised: "
                        f"{r.get('normalised') or '-'}"
                    ).classes("text-xs text-zinc-500")
                with ui.row().classes("items-center gap-2"):
                    badge(STATE_BADGES["needs_review"])
                    ui.label(f"conf {r['confidence']}").classes(
                        "text-xs font-mono " + confidence_classes(float(r["confidence"] or 0), 0.75)
                    )
            ui.label(f"evidence: {r.get('evidence') or '-'}").classes(
                "text-xs text-zinc-600 italic"
            )

            cands = cand_cache.get(r["raw"]) or []
            if not cands:
                cands = [{
                    "entity_id": r["entity_id"], "canonical": r["canonical"],
                    "rxcui": r["rxcui"], "score": r["confidence"], "rank": 1,
                }]
            ui.label("candidates (all retained — none silently collapsed)").classes(
                "text-xs font-semibold mt-2"
            )
            for cd in cands:
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.label(f"#{cd['rank']}").classes("text-xs w-8 font-mono")
                    ui.label(str(cd["canonical"] or "(unnamed)")[:58]).classes("text-sm grow")
                    ui.label(f"rxcui {cd['rxcui']}").classes("text-xs font-mono text-zinc-500")
                    ui.label(f"score {cd['score']}").classes("text-xs font-mono")
                    ui.button(
                        "accept",
                        on_click=lambda raw=r["raw"], eid=cd["entity_id"]: accept(raw, eid),
                    ).props("flat dense no-caps color=primary")
            with ui.row().classes("gap-2 mt-1"):
                ui.button(
                    "reject all candidates",
                    on_click=lambda raw=r["raw"]: reject(raw),
                ).props("flat dense no-caps color=negative")

    def accept(raw: str, eid: str):
        data.record_review(raw, eid, "accept", reviewer["name"])
        ui.notify(f"accepted {raw[:34]} (prior resolution superseded)", type="positive")
        refresh()

    def reject(raw: str):
        data.record_review(raw, None, "reject", reviewer["name"])
        ui.notify(f"rejected {raw[:34]} — stays ineligible", type="warning")
        refresh()

    cand_cache.update(data.candidates_bulk([r["raw"] for r in rows]))
    with container:
        for r in rows:
            render_item(r)


# =========================================================================== #
# 3. provenance explorer
# =========================================================================== #


@ui.page("/entities")
def page_entities():
    shell("/entities", "Provenance explorer")
    term = {"v": "pembrolizumab"}
    results = ui.column().classes("w-full gap-2")
    detail = ui.column().classes("w-full gap-2 mt-3")

    def show(entity_id: str):
        detail.clear()
        d = data.entity_detail(entity_id)
        with detail:
            if not d:
                guards.error_panel(f"{entity_id} not found")
                return
            props = d["props"]
            with ui.card().classes(f"p-3 w-full {tier_classes(props.get('tier'))}"):
                st = tier_style(props.get("tier"))
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"{st.glyph} {entity_id}").classes("font-mono font-semibold")
                    ui.label(" · ".join(d["labels"])).classes("text-xs bg-slate-200 px-2 rounded")
                    ui.label(st.label).classes("text-xs text-zinc-600")
                ui.label(
                    f"source {props.get('source')} @ {props.get('source_version')} · "
                    f"retrieved {str(props.get('retrieved_at'))[:19]}"
                ).classes("text-xs text-zinc-600")
                ui.label(", ".join(f"{k}={v}" for k, v in props.items()
                                   if k not in ("source", "source_version", "retrieved_at", "tier"))
                         ).classes("text-xs font-mono mt-1")

            if d["identifiers"]:
                with ui.card().classes("p-3 w-full"):
                    ui.label("identifiers — every authority retained, none overwritten") \
                        .classes("font-semibold text-sm")
                    for i in d["identifiers"]:
                        with ui.row().classes("items-center gap-2"):
                            ui.label(f"{i['authority']}").classes("text-xs w-28 font-mono")
                            ui.label(str(i["value"])).classes("text-sm font-mono")
                            ui.label(f"release {i['release']}").classes("text-xs text-zinc-500")

            if d["mentions"]:
                with ui.card().classes("p-3 w-full"):
                    ui.label("mentions resolving here").classes("font-semibold text-sm")
                    for m in d["mentions"]:
                        with ui.row().classes("items-center gap-2 w-full"):
                            ui.label(str(m["raw"])[:46]).classes("font-mono text-xs grow")
                            ui.label(str(m["method"])).classes("text-xs w-56 text-zinc-600")
                            ui.label(f"{m['confidence']}").classes(
                                "text-xs font-mono "
                                + confidence_classes(float(m["confidence"] or 0), 0.75)
                            )
                            if m["review_state"] == "needs_review":
                                badge(STATE_BADGES["needs_review"])

            for title, rows_, keyname in (
                ("outgoing relationships", d["out"], "target"),
                ("incoming relationships", d["incoming"], "source_node"),
            ):
                if rows_:
                    with ui.card().classes("p-3 w-full"):
                        ui.label(title).classes("font-semibold text-sm")
                        for r in rows_:
                            with ui.row().classes("items-center gap-2"):
                                ui.label(r["type"]).classes("text-xs font-mono w-56")
                                ui.label(str(r.get(keyname))[:52]).classes("text-sm grow")
                                st2 = tier_style(r.get("tier"))
                                ui.label(f"{st2.glyph} {st2.label}").classes(
                                    "text-xs text-zinc-500"
                                )
                                if r.get("derivation"):
                                    ui.label(f"via {r['derivation']}").classes(
                                        "text-xs italic text-amber-800"
                                    )

    def search():
        results.clear()
        try:
            rows = data.search_entities(term["v"])
        except Exception as exc:  # noqa: BLE001
            with results:
                guards.error_panel(str(exc))
            return
        with results:
            if not rows:
                ui.label("no matches").classes("text-sm text-zinc-600")
            for r in rows:
                with ui.row().classes(
                    f"items-center gap-2 w-full {tier_classes(r['tier'])} py-1 "
                    "hover:bg-sky-50 cursor-pointer"
                ).on("click", lambda e, i=r["entity_id"]: show(i)):
                    st = tier_style(r["tier"])
                    ui.label(st.glyph).classes("w-4")
                    ui.label(str(r["name"] or r["entity_id"])[:52]).classes("text-sm grow")
                    ui.label(", ".join(x for x in r["labels"]
                                       if x not in ("Provenanced", "ResolvedEntity"))
                             ).classes("text-xs bg-slate-200 px-2 rounded")
                    ui.label(f"{r['source']}@{r['version']}").classes("text-xs text-zinc-500")

    with ui.row().classes("items-end gap-2"):
        ui.input("search entities", value=term["v"]).bind_value(term, "v").props(
            "dense outlined"
        ).classes("w-80").on("keydown.enter", search)
        ui.button("search", on_click=search).props("no-caps")
    search()


# =========================================================================== #
# 4. decision trace
# =========================================================================== #


@ui.page("/decision")
def page_decision():
    shell("/decision", "Decision trace")
    ui.label(
        "The catalog-vs-policy case, evaluated through the trust boundary. "
        "Note the facts-withheld panel and the CANNOT_EVALUATE outcome — neither "
        "existed in v1."
    ).classes("text-sm text-zinc-700 mb-2")

    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    cfg = config.load()
    policy = TrustPolicy(
        version=cfg.trust.policy_version, confidence_floor=cfg.trust.confidence_floor
    )

    def stamp(src, tier, method=None, evidence=None):
        from ..foundation import Confidence
        conf = Confidence.of(method) if method else None
        return ProvenanceStamp(
            source=src, source_version="2026-10", retrieved_at=now, tier=tier,
            confidence=conf, method=method.label if method else None, evidence=evidence,
        )

    facts = [
        Fact(key="dq_rule_count", value="0", entity_id="table:CustomerMaster",
             stamp=stamp("catalog", Tier.INGESTED),
             validity=ValidityInterval(valid_from=now - timedelta(days=4)), recorded_at=now),
        Fact(key="certified", value="true", entity_id="table:CustomerMaster",
             stamp=stamp("catalog", Tier.INGESTED),
             validity=ValidityInterval(valid_from=now - timedelta(days=4)), recorded_at=now),
        Fact(key="consent_status", value="Withdrawn", entity_id="patient:PAT015",
             stamp=stamp("authored", Tier.ASSERTED),
             validity=ValidityInterval(valid_from=now - timedelta(days=200)), recorded_at=now),
        Fact(key="rxcui", value="1657993", entity_id="patient:PAT015",
             stamp=stamp("resolver", Tier.DERIVED, ResolutionMethod.MULTI_CANDIDATE,
                         "'pembrolizumab 100mg' matched 3 RxNorm candidates"),
             validity=ValidityInterval(valid_from=now - timedelta(days=1)), recorded_at=now,
             review_state=ReviewState.NEEDS_REVIEW),
        Fact(key="ae_severity", value="Severe", entity_id="ae:AE004",
             stamp=stamp("openfda:faers", Tier.INGESTED),
             validity=ValidityInterval(valid_from=now - timedelta(days=140)), recorded_at=now),
    ]

    bundle = assemble(facts, policy, now)
    results = []
    found, missing = bundle.require("dq_rule_count")
    results.append(RuleResult(
        "governance.hub_table_without_dq_rule",
        RuleOutcome.VIOLATION if found and found[0].value == "0" else RuleOutcome.CANNOT_EVALUATE,
        Severity.BLOCK, message="zero DQ rules for a hub table",
        policy_iri="graph:Policy/POL_HUB_DQ", missing_dependencies=tuple(missing)))
    found, missing = bundle.require("consent_status")
    results.append(RuleResult(
        "consent.withdrawn_patient_data",
        RuleOutcome.VIOLATION if found and str(found[0].value) == "Withdrawn"
        else RuleOutcome.CANNOT_EVALUATE,
        Severity.BLOCK, message="consent withdrawn", policy_iri="bio:POL001",
        missing_dependencies=tuple(missing)))
    _, missing = bundle.require("rxcui")
    results.append(RuleResult(
        "safety.drug_without_external_identifier", RuleOutcome.CANNOT_EVALUATE, Severity.WARN,
        message="identifier unavailable at the required confidence",
        missing_dependencies=tuple(missing)))
    _, missing = bundle.require("ae_severity")
    results.append(RuleResult(
        "safety.severe_adverse_event_requires_disclosure",
        RuleOutcome.CANNOT_EVALUATE if missing else RuleOutcome.VIOLATION,
        Severity.WARN, message="severity unavailable", missing_dependencies=tuple(missing)))

    verdict = Verdict(
        question="Certify Customer Master so I can publish PAT015's response in the Q3 report.",
        results=tuple(results), facts_relied_on=bundle.references(),
        facts_withheld=bundle.withheld, trust_policy_version=bundle.trust_policy_version,
        source_versions=bundle.source_versions(),
        timings=Timings.of(facts_ms=389.41, rules_ms=0.05), decided_at=now,
        bundle_hash=bundle.content_hash,
    )

    with ui.card().classes("p-3 w-full"):
        ui.label(verdict.question).classes("text-sm italic")
        with ui.row().classes("items-center gap-3 mt-1"):
            ui.label(verdict.decision()).classes(
                f"px-3 py-1 rounded font-bold {DECISION_BADGES[verdict.decision()]}"
            )
            ui.label(f"trust policy v{verdict.trust_policy_version}").classes("text-xs")
            ui.label(f"bundle {verdict.bundle_hash[:12]}").classes("text-xs font-mono")
        ui.label(verdict.timings.describe()).classes("text-xs text-zinc-600 mt-1 font-mono")
        ui.label(
            "The 0.05 ms rule figure excludes the graph read. End-to-end is the only "
            "headline permitted."
        ).classes("text-[11px] text-amber-800 italic")

    with ui.card().classes("p-3 w-full mt-3"):
        ui.label("rules").classes("font-semibold text-sm mb-1")
        for r in verdict.results:
            with ui.row().classes("items-center gap-3 w-full"):
                badge(OUTCOME_BADGES[r.outcome.value])
                ui.label(r.rule_id).classes("font-mono text-sm grow")
                ui.label(r.severity.value).classes("text-xs w-14 text-zinc-600")
                if r.policy_iri:
                    ui.label(r.policy_iri).classes("text-xs text-sky-800")
            if r.outcome is RuleOutcome.CANNOT_EVALUATE:
                ui.label(
                    f"    could not evaluate — withheld dependency: "
                    f"{', '.join(r.missing_dependencies) or r.message}"
                ).classes("text-xs text-amber-800 italic ml-6")

    with ui.row().classes("gap-4 w-full mt-3 items-start flex-wrap"):
        with ui.card().classes("p-3 grow"):
            ui.label("facts relied on").classes("font-semibold text-sm mb-1")
            for f in verdict.facts_relied_on:
                with ui.element("div").classes(f"{tier_classes(f.tier)} my-1"):
                    st = tier_style(f.tier)
                    with ui.row().classes("items-center gap-2"):
                        ui.label(f"{st.glyph} {f.key}={f.value_repr}").classes("text-sm font-mono")
                        ui.label(f"{f.confidence:.2f}").classes(
                            "text-xs " + confidence_classes(f.confidence, policy.confidence_floor)
                        )
                    ui.label(f"{f.source}@{f.source_version} · valid {f.validity}").classes(
                        "text-xs text-zinc-500"
                    )
        with ui.card().classes("p-3 grow border-l-4 border-amber-600"):
            ui.label("facts withheld — the trust boundary audit trail").classes(
                "font-semibold text-sm mb-1"
            )
            ui.label("v1 had no equivalent of this view.").classes(
                "text-xs italic text-zinc-600 mb-1"
            )
            for w in verdict.facts_withheld:
                with ui.element("div").classes(f"{tier_classes(w.reference.tier)} my-1"):
                    st = tier_style(w.reference.tier)
                    ui.label(f"{st.glyph} {w.reference.key}={w.reference.value_repr}").classes(
                        "text-sm font-mono"
                    )
                    with ui.row().classes("gap-1 flex-wrap"):
                        for reason in w.reasons:
                            ui.label(reason.value).classes(
                                "text-[10px] px-1.5 py-0.5 rounded bg-red-100 text-red-900"
                            )


# =========================================================================== #
# 5. evaluation
# =========================================================================== #


@ui.page("/eval")
def page_eval():
    shell("/eval", "Evaluation")
    reports = data.eval_reports()
    if not reports:
        guards.error_panel(
            "No evaluation report found. Run: .venv/bin/python scripts/run_four_arm_eval.py"
        )
        return
    rep = data.load_eval(reports[0])
    d = rep["design"]

    with ui.card().classes("p-3 w-full"):
        ui.label("pre-registered design").classes("font-semibold text-sm")
        with ui.row().classes("gap-3 flex-wrap"):
            kpi("cases", d["n_cases"])
            kpi("answers wrong", d["class_balance"]["answer_wrong"])
            kpi("answers correct", d["class_balance"]["answer_correct"])
            kpi("design hash", rep["design_hash"][:10])
        ui.label(f"rationale: {d['balance_rationale']}").classes("text-xs text-zinc-700")
        ui.label(f"falsifier: {d['falsifier']}").classes("text-xs text-zinc-700 italic")
        ui.label(f"judge: {rep['judge_model']}").classes("text-xs font-mono text-zinc-600")

    for name, arm in rep["arms"].items():
        border = "border-l-4 border-sky-600" if name == "judge_grounded" else ""
        with ui.card().classes(f"p-3 w-full mt-3 {border}"):
            with ui.row().classes("items-center gap-2"):
                ui.label(name).classes("font-semibold text-base font-mono")
                ui.label(f"facts={'Y' if arm['gets_graph_facts'] else 'N'}").classes(
                    "text-xs px-2 py-0.5 rounded "
                    + ("bg-emerald-600 text-white" if arm["gets_graph_facts"]
                       else "bg-zinc-300 text-zinc-800")
                )
                ui.label(f"rubric={'Y' if arm['gets_rubric'] else 'N'}").classes(
                    "text-xs px-2 py-0.5 rounded "
                    + ("bg-violet-600 text-white" if arm["gets_rubric"]
                       else "bg-zinc-300 text-zinc-800")
                )
                if name == "judge_grounded":
                    ui.label("THE MISSING CONTROL").classes(
                        "text-xs px-2 py-0.5 rounded bg-sky-600 text-white"
                    )
                if arm["gets_rubric"]:
                    ui.label("circularity demonstration, not a performance result").classes(
                        "text-xs italic text-violet-800"
                    )
            if arm["degenerate"]:
                ui.label(f"DEGENERATE — {arm['degenerate_note']}").classes(
                    "text-xs bg-amber-200 text-black px-2 py-1 rounded my-1"
                )
            c = arm["confusion"]
            ui.label(
                f"confusion  tp={c['tp']} fn={c['fn']} tn={c['tn']} fp={c['fp']}"
            ).classes("text-xs font-mono text-zinc-600")
            guards.render_rate("TPR (catches bad)", arm["tpr"]["value"],
                               k=arm["tpr"]["k"], n=arm["tpr"]["n"],
                               interval=tuple(arm["tpr"]["ci"]))
            guards.render_rate("TNR (passes good)", arm["tnr"]["value"],
                               k=arm["tnr"]["k"], n=arm["tnr"]["n"],
                               interval=tuple(arm["tnr"]["ci"]))
            guards.render_rate("kappa", arm["kappa"], k=None, n=d["n_cases"],
                               interval=tuple(arm["kappa_ci"]),
                               note="agreement above chance")
            guards.render_rate("accuracy", arm["accuracy"]["value"], k=None, n=d["n_cases"],
                               interval=(0.0, 1.0), note=arm["accuracy"]["note"])

    with ui.card().classes("p-3 w-full mt-3 border-l-4 border-sky-600"):
        ui.label("reading").classes("font-semibold text-sm")
        blind = rep["arms"]["judge_blind"]["kappa"]
        ground = rep["arms"]["judge_grounded"]["kappa"]
        ui.markdown(
            f"Giving the judge the same facts the rules get moved kappa from "
            f"**{blind:.3f}** to **{ground:.3f}**. The gap attributed to "
            f"*rules versus LLM* was substantially attributable to "
            f"**evidence access**. The Connected Data reviewer was right: the "
            f"original comparison was confounded."
        ).classes("text-sm")


# =========================================================================== #
# 6. schema evolution
# =========================================================================== #


@ui.page("/releases")
def page_releases():
    shell("/releases", "Schema evolution")
    ui.label(
        "Real HGNC symbol renames, from the current release. A governance rule "
        "written against a previous symbol matches nothing today — silently."
    ).classes("text-sm text-zinc-700 mb-2")
    try:
        rows = data.symbol_renames()
    except Exception as exc:  # noqa: BLE001
        guards.error_panel(str(exc))
        return
    if not rows:
        guards.error_panel("No renames in the graph. Run scripts/ingest.py first.")
        return

    # live rule-integrity consequence - not a prose assertion
    try:
        integ = data.rule_integrity()
    except Exception as exc:  # noqa: BLE001
        integ = None
        guards.error_panel(f"integrity check unavailable: {exc}")

    with ui.row().classes("gap-3 flex-wrap"):
        kpi("renames recorded", len(rows))
        if integ:
            kpi("rules checked", integ["rules_checked"])
            kpi("rules broken", len(integ["findings"]), "by a moved identifier")
            kpi("orphans", integ["orphans"], "no auto path forward")

    if integ and integ["findings"]:
        with ui.card().classes("p-3 w-full mt-3 border-l-4 border-red-600"):
            ui.label("GOVERNANCE CONSEQUENCE — rules broken by a vocabulary change") \
                .classes("font-semibold text-sm text-red-900")
            ui.label(
                "Same rule, same code, same facts. Only the vocabulary release differs."
            ).classes("text-xs italic text-zinc-600 mb-2")
            for f in integ["findings"]:
                with ui.element("div").classes("border-l-4 border-dashed border-amber-600 pl-3 my-3"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(f["rule_id"]).classes("font-mono text-sm font-semibold")
                        ui.label(f"severity {f['severity']}").classes(
                            "text-xs px-2 py-0.5 rounded bg-slate-200")
                        ui.label(f"authored {f['authored']}").classes("text-xs text-zinc-500")
                    ui.label(f"depends on {f['identifier']} — {f['change']}").classes(
                        "text-xs text-zinc-700")
                    with ui.row().classes("gap-2 mt-2 flex-wrap items-center"):
                        for label, key, hint in (
                            ("release N", "before", "correct"),
                            ("release N+1, unchecked", "after_unchecked", "FAIL-OPEN"),
                            ("release N+1, checked", "after_checked", "correct"),
                        ):
                            outcome = f[key]
                            with ui.element("div").classes("border rounded p-2 min-w-48"):
                                ui.label(label).classes("text-[10px] text-zinc-600")
                                badge(OUTCOME_BADGES[outcome])
                                ui.label(hint).classes(
                                    "text-[10px] mt-1 "
                                    + ("text-red-700 font-bold" if hint == "FAIL-OPEN"
                                       else "text-emerald-700"))
                    if f["after_unchecked"] == "no_violation":
                        ui.label(
                            "Unchecked, the rule reported NO_VIOLATION — indistinguishable "
                            "from 'checked and clean'. No error, no alert; the control "
                            "silently stopped."
                        ).classes("text-xs text-red-800 mt-1")
        with ui.card().classes("p-3 w-full mt-2"):
            ui.label("remediation proposals — proposed, never applied").classes(
                "font-semibold text-sm")
            for prop in integ["proposals"]:
                ui.label(prop).classes("text-xs font-mono")
            ui.label(
                "Applying a rename automatically would be exactly the unaudited inference "
                "this layer exists to prevent."
            ).classes("text-xs italic text-zinc-600 mt-1")

    ui.label("all recorded renames").classes("font-semibold text-sm mt-4")
    for r in rows:
        with ui.card().classes("p-3 w-full mt-2 border-l-4 border-dashed border-amber-600"):
            with ui.row().classes("items-center gap-3"):
                badge(STATE_BADGES["withdrawn"])
                ui.label(str(r["was"])).classes("font-mono text-base line-through text-red-800")
                ui.icon("arrow_forward").classes("text-zinc-500")
                ui.label(str(r["now"])).classes("font-mono text-base font-semibold text-emerald-800")
                ui.label(str(r["change_kind"])).classes(
                    "text-xs px-2 py-0.5 rounded bg-amber-100 text-amber-900"
                )
            ui.label(
                f"{r['gene_name']} · {r['hgnc_id']} · release {r['release']}"
            ).classes("text-xs text-zinc-600")
            ui.label(
                f"any rule citing '{r['was']}' now matches nothing"
            ).classes("text-xs italic text-zinc-500")


# =========================================================================== #
# 7. claims register
# =========================================================================== #


@ui.page("/claims")
def page_claims():
    shell("/claims", "Claims register")
    ui.label(
        "Every published claim with its evidence, its n, and what it does NOT establish. "
        "A claim whose limits cannot be stated is rejected at build time rather than published."
    ).classes("text-sm text-zinc-700 mb-2")
    reg = data.claims()
    for category in ClaimCategory:
        group = reg.by_category(category)
        if not group:
            continue
        ui.label(category.value.upper()).classes(
            "text-xs font-bold mt-3 px-2 py-1 rounded bg-slate-200 inline-block"
        )
        for c in group:
            with ui.card().classes("p-3 w-full mt-1"):
                ui.label(c.claim).classes("text-sm font-semibold")
                with ui.row().classes("gap-3 items-center flex-wrap mt-1"):
                    ui.label(f"n={c.n if c.n is not None else 'n/a'}").classes(
                        "text-xs font-mono px-2 py-0.5 bg-slate-100 rounded"
                    )
                    if c.interval:
                        ui.label(c.interval).classes("text-xs font-mono text-zinc-600")
                    ui.label(c.evidence).classes("text-xs text-sky-800 font-mono")
                with ui.element("div").classes(
                    "border-l-4 border-red-500 pl-2 mt-2 bg-red-50 p-2 rounded"
                ):
                    ui.label("does NOT establish").classes("text-xs font-bold text-red-900")
                    ui.label(c.does_not_establish).classes("text-xs text-red-900")



# =========================================================================== #
# 8. contradiction board
# =========================================================================== #

KIND_BLURB = {
    "indication_vs_adverse_event": (
        "A drug investigated FOR a disease, and co-reported WITH it as a reaction. "
        "Read naively the FAERS edge says the drug caused the disease — but a "
        "chemotherapy appears alongside its own indication because the patients "
        "have that disease. Confounding by indication. Source precedence cannot "
        "settle this; clinical review can."
    ),
    "ambiguous_mention": (
        "One surface form with several plausible targets. Every candidate is "
        "retained and the mention stays ineligible as a rule input until reviewed."
    ),
    "cross_authority_identity": (
        "Two canonical entities claiming the same external identifier. Published "
        "cross-references between disease authorities disagree, so this is "
        "expected. Auto-resolvable by declared precedence."
    ),
    "property_disagreement": "Two sources assert incompatible values for one property.",
}


@ui.page("/conflicts")
def page_conflicts():
    shell("/conflicts", "Contradiction board")
    try:
        d = data.conflicts()
    except Exception as exc:  # noqa: BLE001
        guards.error_panel(f"could not load conflicts: {exc}")
        return

    if not d["total"]:
        ui.label(
            "No conflicts detected. Run: "
            ".venv/bin/python scripts/detect_conflicts.py"
        ).classes("text-sm text-zinc-700")
        return

    with ui.row().classes("gap-3 flex-wrap"):
        kpi("conflicts", d["total"])
        kpi("unresolved", d["unresolved"])
        kpi("contested facts", d["contested_resolutions"], "ineligible as rule inputs")
        for k in d["by_kind"]:
            kpi(str(k["kind"]).replace("_", " ")[:18], k["count"])

    ui.label(
        "Both sides of every contradiction are retained. Nothing is auto-resolved "
        "where adjudicating needs domain knowledge rather than source precedence."
    ).classes("text-xs text-zinc-600 italic my-2")

    seen_kinds: set[str] = set()
    for r in d["rows"]:
        kind = str(r["kind"])
        if kind not in seen_kinds:
            seen_kinds.add(kind)
            ui.label(kind.replace("_", " ").upper()).classes(
                "text-xs font-bold mt-4 px-2 py-1 rounded bg-slate-200 inline-block"
            )
            ui.label(KIND_BLURB.get(kind, "")).classes(
                "text-xs text-zinc-700 italic mb-1 max-w-3xl"
            )

        border = "border-red-600" if r["requires_human"] else "border-sky-600"
        with ui.card().classes(f"p-3 w-full mt-1 border-l-4 {border}"):
            with ui.row().classes("items-center gap-2 flex-wrap"):
                ui.label(str(r["subject"])[:60]).classes("font-mono text-sm font-semibold")
                ui.label(str(r["property"])[:46]).classes("text-xs text-zinc-500")
                if r["chosen_source"]:
                    ui.label(f"RESOLVED -> {r['chosen_source']}").classes(
                        "text-xs px-2 py-0.5 rounded bg-emerald-600 text-white")
                    ui.label(f"authority: {r['authority']}").classes(
                        "text-xs text-zinc-600")
                elif r["requires_human"]:
                    ui.label("REQUIRES A HUMAN").classes(
                        "text-xs px-2 py-0.5 rounded bg-red-600 text-white")
                else:
                    ui.label("unresolved").classes(
                        "text-xs px-2 py-0.5 rounded bg-amber-500 text-black")

            for i, sd in enumerate(r["sides"] or []):
                with ui.row().classes("items-start gap-2 w-full mt-1"):
                    ui.label("⇄" if i == 0 else " ").classes(
                        "text-fuchsia-700 w-4 text-center")
                    ui.label(str(sd)).classes("text-xs font-mono grow")
            if r["note"]:
                ui.label(str(r["note"])).classes(
                    "text-xs text-amber-900 italic mt-1 max-w-4xl")


def main() -> None:
    import os

    cfg = config.load()
    port = int(os.environ.get("UI_PORT", "8099"))
    print("BioMedical Context Layer — inspection surface")
    print(f"  neo4j configured: {cfg.neo4j.configured}")
    print(f"  open http://localhost:{port}   (override with UI_PORT)")
    ui.run(host="0.0.0.0", port=port, title="BioMedical Context Layer",
           reload=False, show=False, dark=False)
