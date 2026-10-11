# Inspection surface

Two renderers over the same data. **If your browser shows "Your browser does not
support ES modules", use the second one.**

```bash
python -m biomedical.ui.plain --export site   # static HTML, no server at all
python -m biomedical.ui.plain                 # zero JS      -> localhost:8098
python -m biomedical.ui                       # NiceGUI      -> localhost:8099
```

**If a browser or proxy shows "Your browser does not support ES modules", use
`--export`.** It removes the server, the port and the proxy from the equation:
eight self-contained `.html` files you open with `file://`.

```
site/index.html      9,319B      site/eval.html        12,569B
site/review.html   134,086B      site/conflicts.html   35,193B
site/entities.html   9,096B      site/releases.html     9,207B
site/decision.html   8,494B      site/claims.html       8,892B
```

Verified self-contained: 0 `<script>` tags, 0 external references, 0
absolute-path assets, 1 inline `<style>`, nav links rewritten to relative
filenames so navigation works offline.

The export is a **snapshot**. The review queue's accept/reject buttons are inert
in static mode — forms need a server to POST to. Every other surface is
read-only anyway, so the snapshot loses nothing.

| | `ui.plain` | `ui` (NiceGUI) |
|---|---|---|
| JavaScript | **none** | Vue 3 + ES modules + importmap |
| transport | plain HTTP | websocket |
| external assets | **none** | `/_nicegui/...` root-absolute |
| dependencies | **stdlib only** | `nicegui` |
| works in VS Code webview | **yes** | no |
| works behind a path-prefix proxy | **yes** | no |
| interactivity | HTML forms | live |

## Why the fallback exists

NiceGUI serves a Vue app via `<script type="module">` with an `importmap`, a
websocket, and asset URLs rooted at `/_nicegui/3.18.0/...`. Three things break
that in a sandboxed environment:

1. a webview that does not support ES modules renders NiceGUI's own fallback
   text instead of the app;
2. a path-prefixing proxy makes every root-absolute asset URL 404, so the module
   script never loads;
3. a proxy that does not upgrade websockets leaves the page blank after load.

The assets serve correctly on `localhost` — verified HTTP 200 for
`vue.esm-browser.prod.js`, `nicegui.js` and `quasar.umd.prod.js` — so the app is
healthy. The problem is the path between it and the browser.

`plain.py` removes that path from the equation: Python's `http.server`, inline
CSS, server-rendered HTML. Measured across all eight pages:

```
/            200   9,302B   script=0  module=0  external=0
/review      200 132,752B   script=0  module=0  external=0
/entities    200   9,041B   script=0  module=0  external=0
/decision    200   8,479B   script=0  module=0  external=0
/eval        200  12,544B   script=0  module=0  external=0
/conflicts   200  35,242B   script=0  module=0  external=0
/releases    200   9,192B   script=0  module=0  external=0
/claims      200   8,865B   script=0  module=0  external=0
```

It also needs **no optional dependency** — not even NiceGUI. Verified in an
environment with only `neo4j` and `pyyaml` installed. `biomedical/ui/__init__.py`
imports submodules lazily so that stays true; an eager `from .app import main`
there defeated it until it was fixed.

## The eight surfaces

| Route | Answers |
|---|---|
| `/` | What is in the graph, from where, at what tier |
| `/review` | Which resolutions need a human — **the only writing surface** |
| `/entities` | What we believe about an entity, how strongly, since when |
| `/decision` | Why this verdict, on what evidence, **and what was withheld** |
| `/eval` | What the numbers support, and what they do not |
| `/conflicts` | Where sources disagree, and which a human must settle |
| `/releases` | What moved between releases, and which rules it broke |
| `/claims` | What each published claim does **not** establish |

## The visual language is a correctness feature

Component 00 R2.7: **a derived fact must not look like a curated fact.** A
0.30-confidence inference rendered with the same weight as a pinned RxNorm
identifier undoes the epistemic model underneath.

Each tier carries three independent encodings — border **style**, glyph and
label — with colour only as the fourth channel, so the distinction survives
colour-blindness, greyscale printing and a screenshot in a slide deck.

| Tier | Border | Glyph | Label |
|---|---|---|---|
| asserted | solid | ● | authored |
| ingested | solid | ◆ | ingested |
| derived | **dashed** | ◇ | derived |
| runtime | dotted | ○ | observed |

## The UI refuses to mislead

`render_rate()` will not display a proportion without `n` and a confidence
interval — it returns a red **RENDER REFUSED** panel instead. A bare percentage
cannot reach the screen. Intervals render as a visible bar with a point-estimate
tick, never a tooltip. Unregistered claims do not render at all.

## Writing without JavaScript

The review queue uses plain HTML `POST` forms, so accepting a candidate works in
any browser. Verified end to end: `needs_review` 178 → 177, `cleared` 1 → 2,
`superseded` 1 → 2 — it **supersedes** the prior resolution rather than
overwriting it, and the superseded edge is retained.

## A bug worth recording

`do_POST` crashed with `UnicodeEncodeError`: HTTP headers are latin-1 per
RFC 7230, and the redirect message after accepting a review item contained an
em-dash. The connection died, so clicking **accept** produced a dead page rather
than an error. Fixed by percent-encoding the redirect and keeping the header
ASCII-only.

It was found by reading the server log while diagnosing a report of blank pages —
which turned out to be a different problem entirely (the browser was never
reaching this server; all logged requests were from local `curl`). Two unrelated
faults, one of them real.

## Still not built

Fixture mode. Both renderers currently need a live graph for `/`, `/review`,
`/entities`, `/conflicts` and `/releases`. `/decision`, `/eval` and `/claims`
render without one. A reviewer without credentials should use notebooks `02` and
`05`, which execute with nothing configured.
