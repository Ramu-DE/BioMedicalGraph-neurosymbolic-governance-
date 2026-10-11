"""Zero-JavaScript inspection surface.

NiceGUI's frontend is Vue + ES modules + an importmap + a websocket, with
root-absolute asset URLs. That does not initialise inside VS Code's webview or
behind a path-prefixing proxy, which produces:

    Your browser does not support ES modules. Please use a modern browser.

This module renders the same surfaces as plain server-side HTML with inline CSS:

  * no JavaScript of any kind
  * no ES modules, no importmap, no websocket
  * no external assets - nothing to 404 behind a proxy
  * no third-party web framework - Python's stdlib `http.server`

It therefore works in any browser, any webview and behind any proxy. Forms are
plain HTML POSTs, so even the review queue writes without JavaScript.

    python -m biomedical.ui.plain          # http://localhost:8098
    PLAIN_UI_PORT=9000 python -m biomedical.ui.plain
"""

from __future__ import annotations

import html
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402

from ..foundation import ClaimCategory  # noqa: E402
from . import data  # noqa: E402

# --------------------------------------------------------------------------- #
# the visual language, as CSS only
#
# Component 00 R2.7 makes visual hierarchy a correctness concern: a derived fact
# must not look like a curated one. Each tier carries three independent
# encodings - border style, glyph and label - so the distinction survives
# colour-blindness, greyscale printing and a screenshot in a slide deck.
# --------------------------------------------------------------------------- #

CSS = """
:root { --ink:#18181b; --dim:#71717a; --line:#d4d4d8; --bg:#fafafa; --card:#fff; }
* { box-sizing:border-box; }
body { margin:0; font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
       color:var(--ink); background:var(--bg); }
header { background:#27272a; color:#fff; padding:10px 18px; display:flex;
         align-items:baseline; gap:14px; flex-wrap:wrap; }
header b { font-size:16px; } header span { color:#a1a1aa; font-size:12px; }
nav { background:#f4f4f5; border-bottom:1px solid var(--line); padding:8px 18px;
      display:flex; gap:4px; flex-wrap:wrap; }
nav a { padding:5px 11px; border-radius:4px; text-decoration:none; color:#3f3f46; font-size:13px; }
nav a:hover { background:#e4e4e7; }
nav a.on { background:#bae6fd; color:#0c4a6e; font-weight:600; }
main { padding:18px; max-width:1180px; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:15px; margin:22px 0 8px; }
.sub { color:var(--dim); font-size:13px; margin:0 0 14px; max-width:70ch; }
.kpis { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px; }
.kpi { background:var(--card); border:1px solid var(--line); border-radius:6px;
       padding:9px 13px; min-width:112px; }
.kpi b { display:block; font-size:21px; font-family:ui-monospace,monospace; }
.kpi span { color:var(--dim); font-size:11px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:6px;
        padding:12px 14px; margin-bottom:11px; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th,td { text-align:left; padding:5px 9px; border-bottom:1px solid #f0f0f2; vertical-align:top; }
th { color:var(--dim); font-weight:600; font-size:11px; text-transform:uppercase; }
td.n { font-family:ui-monospace,monospace; text-align:right; }
code,.mono { font-family:ui-monospace,monospace; font-size:12px; }

/* tier encoding: border STYLE + glyph + label, colour is only the 4th channel */
.t { padding-left:11px; margin:5px 0; }
.t-asserted  { border-left:4px solid  #52525b; font-weight:600; }
.t-ingested  { border-left:4px solid  #0284c7; font-weight:600; }
.t-derived   { border-left:4px dashed  #d97706; opacity:.85; }
.t-runtime   { border-left:4px dotted  #7c3aed; opacity:.75; }
.t-unknown   { border-left:4px double  #dc2626; }

.tag { display:inline-block; font-size:11px; padding:1px 7px; border-radius:9px;
       background:#e4e4e7; color:#3f3f46; margin-right:4px; }
.bad   { background:#dc2626; color:#fff; }
.warn  { background:#f59e0b; color:#000; }
.good  { background:#059669; color:#fff; }
.info  { background:#0284c7; color:#fff; }
.strike { text-decoration:line-through; color:#b91c1c; }
.note { font-size:12px; color:#92400e; font-style:italic; }
.err  { border:2px solid #dc2626; background:#fef2f2; color:#7f1d1d;
        padding:11px; border-radius:6px; }
.limit { border-left:4px solid #dc2626; background:#fef2f2; padding:9px 11px;
         border-radius:4px; font-size:12px; margin-top:7px; }
.ci { position:relative; height:7px; background:#e4e4e7; border-radius:4px; margin:3px 0 9px; }
.ci i { position:absolute; height:7px; background:#7dd3fc; border-radius:4px; }
.ci u { position:absolute; height:7px; width:2px; background:#18181b; }
form { display:inline; } button { font:inherit; font-size:12px; padding:3px 9px;
       border:1px solid var(--line); border-radius:4px; background:#fff; cursor:pointer; }
button:hover { background:#f4f4f5; } button.p { border-color:#0284c7; color:#0369a1; }
button.d { border-color:#dc2626; color:#b91c1c; }
footer { color:var(--dim); font-size:12px; padding:18px; }
"""

TIERS = {
    "asserted": ("●", "authored"),
    "ingested": ("◆", "ingested"),
    "derived": ("◇", "derived"),
    "runtime": ("○", "observed"),
}

PAGES = [
    ("/", "Overview"), ("/review", "Review queue"), ("/entities", "Provenance"),
    ("/decision", "Decision trace"), ("/eval", "Evaluation"),
    ("/conflicts", "Contradictions"), ("/releases", "Schema evolution"),
    ("/claims", "Claims register"),
]


def e(x) -> str:
    return html.escape("" if x is None else str(x))


def tier_div(tier, body: str) -> str:
    key = str(tier or "unknown")
    glyph, label = TIERS.get(key, ("?", "unstamped"))
    return (f'<div class="t t-{e(key if key in TIERS else "unknown")}">'
            f'<span class="mono">{glyph}</span> {body} '
            f'<span class="tag">{e(label)}</span></div>')


def kpi(value, label: str, hint: str = "") -> str:
    return (f'<div class="kpi"><b>{e(value)}</b><span>{e(label)}</span>'
            + (f'<br><span>{e(hint)}</span>' if hint else "") + "</div>")


def rate_bar(value, lo, hi) -> str:
    try:
        v, l, h = float(value), float(lo), float(hi)
    except (TypeError, ValueError):
        return ""
    return (f'<div class="ci"><i style="left:{l*100:.1f}%;width:{max(0.8,(h-l)*100):.1f}%"></i>'
            f'<u style="left:{v*100:.1f}%"></u></div>')


def render_rate(label: str, value, lo=None, hi=None, k=None, n=None, note="") -> str:
    """Refuses to render a bare proportion (component 06 R5.2)."""
    if value is None or n is None or lo is None:
        return (f'<div class="err"><b>RENDER REFUSED</b><br>{e(label)}: a rate without n '
                f'and a confidence interval hides whether the number means anything.</div>')
    kn = f"k={k}, n={n}" if k is not None else f"n={n}"
    return (f'<div><span style="color:var(--dim)">{e(label)}</span> '
            f'<b class="mono">{float(value):.3f}</b> '
            f'<span class="mono" style="color:var(--dim)">[{float(lo):.3f}, {float(hi):.3f}]</span> '
            f'<span class="tag">{e(kn)}</span>'
            + (f' <span class="note">&larr; {e(note)}</span>' if note else "")
            + "</div>" + rate_bar(value, lo, hi))


def page(path: str, title: str, sub: str, body: str) -> bytes:
    cfg = config.load()
    mode = ("live graph", "good") if cfg.neo4j.configured else ("no credentials", "warn")
    nav = "".join(f'<a href="{p}" class="{"on" if p == path else ""}">{e(t)}</a>'
                  for p, t in PAGES)
    legend = "".join(tier_div(k, f"<b>{e(v[1])}</b>") for k, v in TIERS.items())
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} — BioMedical Context Layer</title><style>{CSS}</style></head><body>
<header><b>BioMedical Context Layer</b><span>zero-JavaScript inspection surface</span>
<span class="tag {mode[1]}">{e(mode[0])}</span></header>
<nav>{nav}</nav><main><h1>{e(title)}</h1><p class="sub">{sub}</p>{body}
<h2>Tier legend</h2><div class="card">{legend}
<p class="sub" style="margin:8px 0 0">Border <em>style</em>, glyph and label are three
independent encodings, so the distinction survives colour-blindness and greyscale.
A derived fact must not look like a curated one.</p></div></main>
<footer>Rendered server-side. No JavaScript, no ES modules, no websocket, no external
assets &mdash; works in any browser, webview or proxy.</footer></body></html>""".encode()


# --------------------------------------------------------------------------- #
# surfaces
# --------------------------------------------------------------------------- #


def v_overview() -> str:
    o, bl, bands = data.overview(), data.backlog_counts(), data.resolution_bands()
    out = ['<div class="kpis">',
           kpi(f"{o['nodes']:,}", "nodes"), kpi(f"{o['rels']:,}", "relationships"),
           kpi(len(o["sources"]), "sources", "independent, live"),
           kpi(bl["resolved"], "resolutions"),
           kpi(bl["needs_review"], "need review",
               f"{bl['needs_review'] / max(1, bl['resolved']):.0%} of resolutions"),
           kpi(bl["unlinked"], "unlinked"), "</div>",
           '<p class="sub">A visible backlog is published deliberately. It is more '
           'credible than none.</p>']

    for title, rows, cols in [
        ("Nodes by label", o["labels"], ("label", "count")),
        ("Relationships by type", o["rels_by_type"], ("type", "count")),
    ]:
        body = "".join(f"<tr><td>{e(r[cols[0]])}</td><td class='n'>{r[cols[1]]:,}</td></tr>"
                       for r in rows)
        out.append(f'<h2>{title}</h2><div class="card"><table>'
                   f"<tr><th>{cols[0]}</th><th>count</th></tr>{body}</table></div>")

    out.append('<h2>Provenance tiers — every node carries one</h2><div class="card">')
    out += [tier_div(r["tier"], f"<b>{r['count']:,}</b> nodes") for r in o["tiers"]]
    out.append("</div>")

    src = "".join(f"<tr><td>{e(r['source'])}</td><td><code>{e(r['version'])}</code></td>"
                  f"<td class='n'>{r['count']:,}</td></tr>" for r in o["sources"])
    out.append('<h2>Sources</h2><div class="card"><table><tr><th>source</th>'
               f"<th>pinned version</th><th>nodes</th></tr>{src}</table></div>")

    rb = ""
    for b in bands:
        conf = float(b["confidence"] or 0)
        cls = ' class="strike"' if conf < 0.75 else ""
        flag = '<span class="tag bad">below trust floor</span>' if conf < 0.75 else ""
        rb += (f"<tr><td><code>{e(b['method'])}</code></td><td class='n'>{b['count']}</td>"
               f"<td{cls}>{conf:.2f}</td><td>{flag}</td></tr>")
    out.append('<h2>Entity resolution — confidence derived from method, never authored</h2>'
               f'<div class="card"><table><tr><th>method</th><th>n</th><th>confidence</th>'
               f"<th></th></tr>{rb}</table></div>")

    out.append('<h2>Two problems the real data exposed</h2><div class="card">'
               '<p><b>1. Disease identity.</b> Resolved against MONDO — but phenotype '
               'conditions such as <code>Bradycardia</code> and <code>Albuminuria</code> '
               'belong in HPO, not a disease ontology. No threshold fixes that.</p>'
               '<p><b>2. <code>ASSOCIATED_WITH_REACTION</code> is co-reporting, not '
               'causation.</b> FAERS has no denominator, no controls and heavy reporting '
               'bias. These edges are <code>tier: derived</code> so they cannot be mistaken '
               'for evidence of harm. <b>No causal claim may be made from them.</b></p></div>')
    return "".join(out)


def v_review(msg: str = "") -> str:
    bl, rows = data.backlog_counts(), data.review_queue(limit=30)
    cands = data.candidates_bulk([r["raw"] for r in rows])
    out = []
    if msg:
        out.append(f'<div class="card"><span class="tag good">{e(msg)}</span></div>')
    out += ['<div class="kpis">',
            kpi(bl["needs_review"], "awaiting review"), kpi(bl["cleared"], "cleared"),
            kpi(bl["rejected"], "rejected"),
            kpi(bl.get("superseded", 0), "superseded", "retained, never deleted"), "</div>",
            '<p class="sub">Accepting <b>supersedes</b> the prior resolution; it never '
            'overwrites it. Low-confidence mappings stay ineligible as rule inputs until '
            'cleared. These forms are plain HTML POSTs — no JavaScript.</p>']
    if not rows:
        out.append('<div class="card">Queue empty — every resolution has been reviewed.</div>')
    for r in rows:
        opts = cands.get(r["raw"]) or [{"entity_id": r["entity_id"], "canonical": r["canonical"],
                                        "rxcui": r["rxcui"], "score": r["confidence"], "rank": 1}]
        rowsh = ""
        for c in opts:
            rowsh += (f"<tr><td class='n'>#{e(c['rank'])}</td><td>{e(str(c['canonical'])[:56])}</td>"
                      f"<td><code>{e(c['rxcui'])}</code></td><td class='n'>{e(c['score'])}</td>"
                      f"<td><form method='post' action='/review'>"
                      f"<input type='hidden' name='raw' value='{e(r['raw'])}'>"
                      f"<input type='hidden' name='entity_id' value='{e(c['entity_id'])}'>"
                      f"<input type='hidden' name='decision' value='accept'>"
                      f"<button class='p'>accept</button></form></td></tr>")
        out.append(
            f'<div class="card t t-derived"><b class="mono">{e(r["raw"])}</b>'
            f'<span class="tag warn">needs review</span>'
            f'<span class="tag">conf {e(r["confidence"])}</span><br>'
            f'<span class="sub">{e(r["source"])} · {e(r.get("kind"))} · normalised: '
            f'<code>{e(r.get("normalised"))}</code></span>'
            f'<p class="note">evidence: {e(r.get("evidence"))}</p>'
            f'<table><tr><th>rank</th><th>candidate</th><th>id</th><th>score</th><th></th></tr>'
            f'{rowsh}</table>'
            f"<form method='post' action='/review'>"
            f"<input type='hidden' name='raw' value='{e(r['raw'])}'>"
            f"<input type='hidden' name='decision' value='reject'>"
            f"<button class='d'>reject all candidates</button></form></div>")
    return "".join(out)


def v_entities(term: str) -> str:
    out = [f'<div class="card"><form method="get" action="/entities">'
           f'<input name="q" value="{e(term)}" style="padding:5px;width:300px">'
           f' <button class="p">search</button></form></div>']
    rows = data.search_entities(term) if term else []
    if term and not rows:
        out.append('<div class="card">no matches</div>')
    for r in rows:
        labels = " ".join(f'<span class="tag">{e(x)}</span>' for x in r["labels"]
                          if x not in ("Provenanced", "ResolvedEntity"))
        out.append(tier_div(r["tier"],
            f'<a href="/entities?q={e(term)}&id={e(r["entity_id"])}">'
            f'<b>{e(str(r["name"] or r["entity_id"])[:58])}</b></a> {labels}'
            f'<span class="tag">{e(r["source"])}@{e(r["version"])}</span>'))
    return "".join(out)


def v_entity_detail(entity_id: str, term: str) -> str:
    d = data.entity_detail(entity_id)
    if not d:
        return f'<div class="err">{e(entity_id)} not found</div>'
    p = d["props"]
    out = [f'<p><a href="/entities?q={e(term)}">&larr; back to results</a></p>',
           '<div class="card">',
           tier_div(p.get("tier"), f'<b class="mono">{e(entity_id)}</b> '
                    + " ".join(f'<span class="tag">{e(l)}</span>' for l in d["labels"])),
           f'<p class="sub">source <code>{e(p.get("source"))}</code> @ '
           f'<code>{e(p.get("source_version"))}</code> · retrieved '
           f'{e(str(p.get("retrieved_at"))[:19])}</p></div>']
    if d["identifiers"]:
        rows = "".join(f"<tr><td>{e(i['authority'])}</td><td><code>{e(i['value'])}</code></td>"
                       f"<td>{e(i['release'])}</td></tr>" for i in d["identifiers"])
        out.append('<h2>Identifiers — every authority retained, none overwritten</h2>'
                   f'<div class="card"><table><tr><th>authority</th><th>value</th>'
                   f"<th>release</th></tr>{rows}</table></div>")
    if d["mentions"]:
        rows = ""
        for m in d["mentions"]:
            conf = float(m["confidence"] or 0)
            flag = '<span class="tag warn">review</span>' if m["review_state"] == "needs_review" else ""
            rows += (f"<tr><td class='mono'>{e(str(m['raw'])[:46])}</td>"
                     f"<td><code>{e(m['method'])}</code></td>"
                     f"<td class='n'>{conf:.2f}</td><td>{flag}</td></tr>")
        out.append('<h2>Mentions resolving here</h2><div class="card"><table>'
                   f"<tr><th>raw</th><th>method</th><th>conf</th><th></th></tr>{rows}</table></div>")
    for title, rs, key in [("Outgoing relationships", d["out"], "target"),
                           ("Incoming relationships", d["incoming"], "source_node")]:
        if rs:
            rows = "".join(
                f"<tr><td><code>{e(r['type'])}</code></td><td>{e(str(r.get(key))[:52])}</td>"
                f"<td><span class='tag'>{e(r.get('tier'))}</span></td>"
                f"<td class='note'>{e(r.get('derivation') or '')}</td></tr>" for r in rs)
            out.append(f'<h2>{title}</h2><div class="card"><table>{rows}</table></div>')
    return "".join(out)


def v_decision() -> str:
    """Rendered from the saved notebook scenario — no model call, no mutation."""
    return """
<div class="card">
<p><b>Q:</b> Certify Customer Master so I can publish PAT015's response in the Q3 report.</p>
<p><span class="tag bad">BLOCKED</span><span class="tag">trust policy v1.0</span></p>
<p class="mono" style="font-size:12px">389.46 ms end-to-end
(facts 389.41 ms &middot; rules 0.05 ms — rule evaluation over already-materialised
facts; <b>excludes the graph round-trip</b>)</p>
<p class="note">v1 published 0.09&nbsp;ms as the cost of a verdict. A reviewer asked
whether that included database access. It did not — roughly four orders of magnitude.
The component figure is now un-publishable without this note.</p>
</div>

<h2>Rules</h2><div class="card"><table>
<tr><th>outcome</th><th>rule</th><th>severity</th><th>policy</th></tr>
<tr><td><span class="tag bad">&#10005; VIOLATION</span></td>
    <td class="mono">governance.hub_table_without_dq_rule</td><td>block</td>
    <td><code>graph:Policy/POL_HUB_DQ</code></td></tr>
<tr><td><span class="tag bad">&#10005; VIOLATION</span></td>
    <td class="mono">consent.withdrawn_patient_data</td><td>block</td>
    <td><code>bio:POL001</code></td></tr>
<tr><td><span class="tag warn">? CANNOT EVALUATE</span></td>
    <td class="mono">safety.drug_without_external_identifier</td><td>warn</td><td>—</td></tr>
</table>
<p class="note">CANNOT_EVALUATE is not a pass. The withheld dependency was
<code>rxcui</code>, so the verdict reads INCONCLUSIVE rather than ALLOWED — the
governed action does not proceed on the rule's silence.</p></div>

<h2>Facts relied on</h2><div class="card">
<div class="t t-ingested"><span class="mono">&#9670;</span>
  <code>dq_rule_count = "0"</code> <span class="tag">1.00</span>
  <span class="tag">catalog@2026-10</span><span class="tag">ingested</span></div>
<div class="t t-ingested"><span class="mono">&#9670;</span>
  <code>certified = "true"</code> <span class="tag">1.00</span>
  <span class="tag">catalog@2026-10</span><span class="tag">ingested</span></div>
<div class="t t-asserted"><span class="mono">&#9679;</span>
  <code>consent_status = "Withdrawn"</code> <span class="tag">1.00</span>
  <span class="tag">authored@synthetic-v1</span><span class="tag">authored</span></div>
</div>

<h2>Facts withheld — the trust boundary audit trail</h2>
<div class="card" style="border-left:4px solid #d97706">
<p class="sub">v1 had no equivalent of this view. It is the direct answer to
<em>"how do requests become trusted rule inputs?"</em></p>
<div class="t t-derived"><span class="mono">&#9671;</span>
  <code>rxcui = "1657993"</code>
  <span class="tag bad">tier_too_weak</span>
  <span class="tag bad">confidence_below_floor</span>
  <span class="tag bad">awaiting_review</span></div>
</div>

<div class="card"><p><b>The case itself.</b> The catalog says
<code>certified: true</code>. The governing policy requires every hub-layer table to
carry at least one critical DQ rule, and <code>dq_rule_count</code> is 0. Two
internally consistent systems of record; nobody lying; the organisation wrong in a
way neither can see alone. The policy was pre-existing, not written for the demo.</p>
<p class="sub">Run <code>notebooks/05_governance_decision_trace.ipynb</code> to
reproduce this live — it needs no credentials.</p></div>"""


def v_eval() -> str:
    reports = data.eval_reports()
    if not reports:
        return ('<div class="err">No evaluation report found. Run '
                '<code>notebooks/09_four_arm_evaluation.ipynb</code>.</div>')
    rep = data.load_eval(reports[0])
    d = rep["design"]
    out = ['<div class="kpis">', kpi(d["n_cases"], "cases"),
           kpi(d["class_balance"]["answer_wrong"], "answers wrong"),
           kpi(d["class_balance"]["answer_correct"], "answers correct"),
           kpi(rep["design_hash"][:10], "design hash"), "</div>",
           f'<div class="card"><p class="sub"><b>rationale:</b> {e(d["balance_rationale"])}</p>'
           f'<p class="sub"><b>falsifier:</b> {e(d["falsifier"])}</p>'
           f'<p class="sub"><b>judge:</b> <code>{e(rep["judge_model"])}</code></p></div>']
    for name, arm in rep["arms"].items():
        hi = ' style="border-left:4px solid #0284c7"' if name == "judge_grounded" else ""
        tags = (f'<span class="tag {"good" if arm["gets_graph_facts"] else ""}">'
                f'facts={"Y" if arm["gets_graph_facts"] else "N"}</span>'
                f'<span class="tag {"info" if arm["gets_rubric"] else ""}">'
                f'rubric={"Y" if arm["gets_rubric"] else "N"}</span>')
        if name == "judge_grounded":
            tags += '<span class="tag info">THE MISSING CONTROL</span>'
        if arm["gets_rubric"]:
            tags += '<span class="note"> circularity demonstration, not a result</span>'
        c = arm["confusion"]
        body = [f'<div class="card"{hi}><b class="mono">{e(name)}</b> {tags}']
        if arm["degenerate"]:
            body.append(f'<p><span class="tag warn">DEGENERATE</span> '
                        f'{e(arm["degenerate_note"])}</p>')
        body.append(f'<p class="mono" style="font-size:12px;color:var(--dim)">confusion '
                    f'tp={c["tp"]} fn={c["fn"]} tn={c["tn"]} fp={c["fp"]}</p>')
        body.append(render_rate("TPR (catches bad)", arm["tpr"]["value"],
                                *arm["tpr"]["ci"], k=arm["tpr"]["k"], n=arm["tpr"]["n"]))
        body.append(render_rate("TNR (passes good)", arm["tnr"]["value"],
                                *arm["tnr"]["ci"], k=arm["tnr"]["k"], n=arm["tnr"]["n"]))
        body.append(render_rate("kappa", arm["kappa"], *arm["kappa_ci"],
                                n=d["n_cases"], note="agreement above chance"))
        body.append(render_rate("accuracy", arm["accuracy"]["value"], 0.0, 1.0,
                                n=d["n_cases"], note=arm["accuracy"]["note"]))
        body.append("</div>")
        out.append("".join(body))
    blind = rep["arms"]["judge_blind"]["kappa"]
    ground = rep["arms"]["judge_grounded"]["kappa"]
    out.append(f'<div class="card" style="border-left:4px solid #0284c7"><h2 '
               f'style="margin-top:0">Reading</h2><p>Giving the judge the same facts the '
               f'rules get moved kappa from <b>{blind:.3f}</b> to <b>{ground:.3f}</b>. '
               f'The gap attributed to <em>rules versus LLM</em> was substantially '
               f'attributable to <b>evidence access</b>. The reviewer was right: the '
               f'original comparison was confounded, and that claim is withdrawn.</p>'
               f'<div class="limit"><b>And the blind arm does not reproduce.</b> Three runs '
               f'gave kappa 0.273, 0.471 and 0.471 at temperature 0 — the deterministic and '
               f'grounded arms reproduce exactly, the blind arm does not. The direction holds '
               f'across all three; the magnitude is not established by any single run.</div></div>')
    return "".join(out)


def v_conflicts() -> str:
    d = data.conflicts()
    if not d["total"]:
        return ('<div class="err">No conflicts detected. Run '
                '<code>notebooks/08_conflict_detection.ipynb</code>.</div>')
    blurb = {
        "indication_vs_adverse_event":
            "A drug investigated FOR a disease and co-reported WITH it as a reaction. "
            "Read naively the FAERS edge says the drug caused the disease — but a "
            "chemotherapy appears alongside its own indication because the patients have "
            "that disease. Confounding by indication. Source precedence cannot settle "
            "this; clinical review can.",
        "ambiguous_mention":
            "One surface form with several plausible targets. Every candidate is retained "
            "and the mention stays ineligible as a rule input until reviewed.",
        "cross_authority_identity":
            "Two canonical entities claiming one external identifier. Published "
            "cross-references between disease authorities disagree, so this is expected. "
            "Auto-resolvable by declared precedence.",
    }
    out = ['<div class="kpis">', kpi(d["total"], "conflicts"), kpi(d["unresolved"], "unresolved"),
           kpi(d["contested_resolutions"], "contested facts", "ineligible as rule inputs")]
    out += [kpi(k["count"], str(k["kind"]).replace("_", " ")[:18]) for k in d["by_kind"]]
    out += ["</div>", '<p class="sub">Both sides of every contradiction are retained. Nothing '
            'is auto-resolved where adjudicating needs domain knowledge rather than source '
            'precedence.</p>']
    seen = set()
    for r in d["rows"]:
        kind = str(r["kind"])
        if kind not in seen:
            seen.add(kind)
            out.append(f'<h2>{e(kind.replace("_", " ").upper())}</h2>'
                       f'<p class="sub">{e(blurb.get(kind, ""))}</p>')
        badge = ('<span class="tag good">RESOLVED &rarr; ' + e(r["chosen_source"]) + "</span>"
                 if r["chosen_source"] else
                 '<span class="tag bad">REQUIRES A HUMAN</span>' if r["requires_human"]
                 else '<span class="tag warn">unresolved</span>')
        sides = "".join(f'<div class="mono" style="font-size:12px">&#8646; {e(s)}</div>'
                        for s in (r["sides"] or []))
        border = "#dc2626" if r["requires_human"] else "#0284c7"
        out.append(f'<div class="card" style="border-left:4px solid {border}">'
                   f'<b class="mono">{e(str(r["subject"])[:62])}</b> '
                   f'<span class="tag">{e(str(r["property"])[:44])}</span> {badge}'
                   f'{sides}<p class="note">{e(r["note"])}</p></div>')
    return "".join(out)


def v_releases() -> str:
    rows = data.symbol_renames()
    if not rows:
        return ('<div class="err">No renames in the graph. Run '
                '<code>notebooks/03_multi_source_ingest.ipynb</code>.</div>')
    out = []
    try:
        integ = data.rule_integrity()
    except Exception as exc:  # noqa: BLE001
        integ = None
        out.append(f'<div class="err">integrity check unavailable: {e(exc)}</div>')
    out.insert(0, '<div class="kpis">' + kpi(len(rows), "renames recorded")
               + (kpi(integ["rules_checked"], "rules checked")
                  + kpi(len(integ["findings"]), "rules broken", "by a moved identifier")
                  + kpi(integ["orphans"], "orphans", "no auto path forward") if integ else "")
               + "</div>")
    if integ and integ["findings"]:
        out.append('<h2>Governance consequence — rules broken by a vocabulary change</h2>'
                   '<p class="sub">Same rule, same code, same facts. Only the vocabulary '
                   'release differs.</p>')
        for f in integ["findings"]:
            cells = ""
            for lbl, key, hint in [("release N", "before", "correct"),
                                   ("release N+1, unchecked", "after_unchecked", "FAIL-OPEN"),
                                   ("release N+1, checked", "after_checked", "correct")]:
                cls = {"violation": "bad", "no_violation": "good",
                       "cannot_evaluate": "warn"}.get(f[key], "")
                hcls = "bad" if hint == "FAIL-OPEN" else "good"
                cells += (f"<td><span class='tag {cls}'>{e(f[key])}</span><br>"
                          f"<span class='tag {hcls}'>{e(hint)}</span></td>")
            out.append(f'<div class="card" style="border-left:4px solid #dc2626">'
                       f'<b class="mono">{e(f["rule_id"])}</b>'
                       f'<span class="tag">severity {e(f["severity"])}</span>'
                       f'<span class="tag">authored {e(f["authored"])}</span>'
                       f'<p class="sub">depends on <code>{e(f["identifier"])}</code> — '
                       f'{e(f["change"])}</p><table><tr><th>release N</th>'
                       f'<th>N+1 unchecked</th><th>N+1 checked</th></tr><tr>{cells}</tr>'
                       f'</table><p class="note">Unchecked, the rule reported NO_VIOLATION — '
                       f'indistinguishable from "checked and clean". No error, no alert; the '
                       f'control silently stopped.</p></div>')
        out.append('<div class="card"><h2 style="margin-top:0">Remediation proposals — '
                   'proposed, never applied</h2>'
                   + "".join(f'<div class="mono" style="font-size:12px">{e(p)}</div>'
                             for p in integ["proposals"])
                   + '<p class="note">Applying a rename automatically would be exactly the '
                     'unaudited inference this layer exists to prevent.</p></div>')
    rr = "".join(f"<tr><td class='mono strike'>{e(r['was'])}</td><td>&rarr;</td>"
                 f"<td class='mono'><b>{e(r['now'])}</b></td><td>{e(r['gene_name'])}</td>"
                 f"<td><code>{e(r['hgnc_id'])}</code></td></tr>" for r in rows)
    out.append('<h2>All recorded renames</h2><div class="card"><table><tr><th>was</th>'
               f"<th></th><th>now</th><th>gene</th><th>hgnc</th></tr>{rr}</table></div>")
    return "".join(out)


def v_claims() -> str:
    reg = data.claims()
    out = ['<p class="sub">Every published claim with its evidence, its n, and what it does '
           '<b>not</b> establish. A claim whose limits cannot be stated is rejected at build '
           'time rather than published.</p>']
    for cat in ClaimCategory:
        group = reg.by_category(cat)
        if not group:
            continue
        out.append(f'<h2>{e(cat.value.upper())}</h2>')
        for c in group:
            out.append(f'<div class="card"><b>{e(c.claim)}</b><br>'
                       f'<span class="tag">n={e(c.n if c.n is not None else "n/a")}</span>'
                       + (f'<span class="tag mono">{e(c.interval)}</span>' if c.interval else "")
                       + f'<span class="tag">{e(c.evidence)}</span>'
                       f'<div class="limit"><b>does NOT establish</b><br>'
                       f'{e(c.does_not_establish)}</div></div>')
    return "".join(out)


# --------------------------------------------------------------------------- #
# server
# --------------------------------------------------------------------------- #

TITLES = dict(PAGES)
SUBS = {
    "/": "What is in the graph, from where, and at what epistemic tier.",
    "/review": "Which entity resolutions need a human. The only writing surface.",
    "/entities": "What we believe about an entity, how strongly, and since when.",
    "/decision": "Why this verdict, on what evidence, and what was withheld.",
    "/eval": "What the numbers support, and what they do not.",
    "/conflicts": "Where the sources disagree, and which disagreements a human must settle.",
    "/releases": "What moved between vocabulary releases, and which rules it broke.",
    "/claims": "What each published claim does not establish.",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "BioMedicalPlainUI/1.0"

    def log_message(self, fmt, *args):  # quieter
        sys.stderr.write(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}\n")

    def _send(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)
        if path not in TITLES:
            self._send(page("/", "Not found", "", f'<div class="err">No such page: '
                            f'{e(path)}</div>'), 404)
            return
        try:
            if path == "/":
                body = v_overview()
            elif path == "/review":
                body = v_review(q.get("msg", [""])[0])
            elif path == "/entities":
                term = q.get("q", ["pembrolizumab"])[0]
                body = (v_entity_detail(q["id"][0], term) if "id" in q else v_entities(term))
            elif path == "/decision":
                body = v_decision()
            elif path == "/eval":
                body = v_eval()
            elif path == "/conflicts":
                body = v_conflicts()
            elif path == "/releases":
                body = v_releases()
            else:
                body = v_claims()
        except config.NotConfigured as exc:
            body = (f'<div class="err"><b>Not configured</b><pre style="white-space:pre-wrap">'
                    f'{e(exc)}</pre></div>')
        except Exception as exc:  # noqa: BLE001
            body = (f'<div class="err"><b>{e(type(exc).__name__)}</b><br>{e(exc)}'
                    f'<pre style="white-space:pre-wrap;font-size:11px">'
                    f'{e(traceback.format_exc()[-900:])}</pre></div>')
        self._send(page(path, TITLES[path], SUBS.get(path, ""), body))

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/review":
            self._send(b"", 404)
            return
        n = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(n).decode())
        raw = form.get("raw", [""])[0]
        decision = form.get("decision", ["reject"])[0]
        entity_id = form.get("entity_id", [None])[0]
        try:
            data.record_review(raw, entity_id, decision, "plain-ui")
            msg = f"{decision}ed {raw[:40]} - prior resolution superseded, not overwritten"
        except Exception as exc:  # noqa: BLE001
            msg = f"failed: {type(exc).__name__}: {exc}"
        # HTTP headers are latin-1 (RFC 7230). An em-dash in the message raised
        # UnicodeEncodeError and killed the connection, so "accept" produced a
        # dead page. Percent-encode, and keep the header ASCII-only.
        self.send_response(303)
        self.send_header("Location", "/review?msg=" + quote(msg, safe=""))
        self.end_headers()


def export(out_dir: str = "site") -> list[Path]:
    """Write every surface as a self-contained .html file.

    Removes the server, the port and the proxy from the equation entirely: open
    the files with file:// and they render. Links are rewritten to relative
    filenames so navigation works offline.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    views = {
        "/": ("index.html", v_overview),
        "/review": ("review.html", lambda: v_review("")),
        "/entities": ("entities.html", lambda: v_entities("pembrolizumab")),
        "/decision": ("decision.html", v_decision),
        "/eval": ("eval.html", v_eval),
        "/conflicts": ("conflicts.html", v_conflicts),
        "/releases": ("releases.html", v_releases),
        "/claims": ("claims.html", v_claims),
    }
    files: list[Path] = []
    for path, (fname, view) in views.items():
        try:
            body = view()
        except config.NotConfigured as exc:
            body = (f'<div class="err"><b>Not configured</b><pre '
                    f'style="white-space:pre-wrap">{e(exc)}</pre></div>')
        except Exception as exc:  # noqa: BLE001
            body = f'<div class="err"><b>{e(type(exc).__name__)}</b><br>{e(exc)}</div>'
        doc = page(path, TITLES[path], SUBS.get(path, ""), body).decode()
        # rewrite nav + internal links to relative files
        for p2, (f2, _) in views.items():
            doc = doc.replace(f'href="{p2}"', f'href="{f2}"')
        doc = doc.replace('href="/entities?q=', 'href="entities.html?q=')
        doc = doc.replace('<form method="post" action="/review">',
                          '<form method="post" action="review.html">')
        doc = doc.replace("<form method='post' action='/review'>",
                          "<form method='post' action='review.html'>")
        target = out / fname
        target.write_text(doc)
        files.append(target)
        print(f"  {target}  ({len(doc):,} bytes)")
    print(f"\n{len(files)} static pages in {out.resolve()}")
    print(f"open {(out / 'index.html').resolve()}")
    print("No server, no port, no proxy. Forms are inert in static mode.")
    return files


def export_single(out_file: str = "report.html") -> Path:
    """Write EVERY surface into one self-contained HTML file.

    No navigation, no separate pages, no links to follow — scroll through the
    whole inspection surface. This is the most robust artefact available: a
    single file, no server, no port, no proxy, no JavaScript, no external asset,
    and nothing to click.
    """
    order = [
        ("Overview", "/", v_overview),
        ("Decision trace", "/decision", v_decision),
        ("Evaluation", "/eval", v_eval),
        ("Contradictions", "/conflicts", v_conflicts),
        ("Schema evolution", "/releases", v_releases),
        ("Claims register", "/claims", v_claims),
        ("Provenance", "/entities", lambda: v_entities("pembrolizumab")),
        ("Review queue", "/review", lambda: v_review("")),
    ]
    sections = []
    for title, path, view in order:
        try:
            body = view()
        except config.NotConfigured as exc:
            body = (f'<div class="err"><b>Not configured</b><pre '
                    f'style="white-space:pre-wrap">{e(exc)}</pre></div>')
        except Exception as exc:  # noqa: BLE001
            body = f'<div class="err"><b>{e(type(exc).__name__)}</b><br>{e(exc)}</div>'
        sections.append(
            f'<section><h1>{e(title)}</h1>'
            f'<p class="sub">{SUBS.get(path, "")}</p>{body}</section><hr>')

    legend = "".join(tier_div(k, f"<b>{e(v[1])}</b>") for k, v in TIERS.items())
    cfg = config.load()
    mode = ("live graph", "good") if cfg.neo4j.configured else ("no credentials", "warn")
    doc = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BioMedical Context Layer — full report</title><style>{CSS}
section {{ margin-bottom:8px; }}
hr {{ border:none; border-top:2px solid var(--line); margin:30px 0; }}
h1 {{ margin-top:0; padding-top:6px; }}
</style></head><body>
<header><b>BioMedical Context Layer</b>
<span>complete inspection surface, one page</span>
<span class="tag {mode[1]}">{e(mode[0])}</span></header>
<main>
<div class="card"><b>Everything is on this page.</b> No navigation, no links to
follow, no server. Scroll down for all eight surfaces in order. Rendered
server-side with no JavaScript, no ES modules and no external assets.</div>
{"".join(sections)}
<h1>Tier legend</h1><div class="card">{legend}
<p class="sub" style="margin:8px 0 0">Border <em>style</em>, glyph and label are
three independent encodings, so the distinction survives colour-blindness and
greyscale. A derived fact must not look like a curated one.</p></div>
</main><footer>Single self-contained file. Generated by
<code>python -m biomedical.ui.plain --single</code>.</footer></body></html>"""

    target = Path(out_file)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(doc)
    print(f"  {target.resolve()}")
    print(f"  {len(doc):,} bytes · {len(order)} surfaces · 1 file · 0 links to click")
    print(f"\nopen {target.resolve()}")
    return target


def main() -> None:
    if "--single" in sys.argv:
        i = sys.argv.index("--single")
        target = sys.argv[i + 1] if len(sys.argv) > i + 1 and not sys.argv[i + 1].startswith("-") \
            else "report.html"
        export_single(target)
        return
    if "--export" in sys.argv:
        i = sys.argv.index("--export")
        target = sys.argv[i + 1] if len(sys.argv) > i + 1 else "site"
        export(target)
        return
    port = int(os.environ.get("PLAIN_UI_PORT", "8098"))
    cfg = config.load()
    print("BioMedical Context Layer — zero-JavaScript inspection surface")
    print(f"  neo4j configured: {cfg.neo4j.configured}")
    print(f"  open http://localhost:{port}   (override with PLAIN_UI_PORT)")
    print("  no JavaScript, no ES modules, no websocket, no external assets")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
