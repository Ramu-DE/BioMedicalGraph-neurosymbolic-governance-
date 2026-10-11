# Inspection surface

**Running now at http://localhost:8099**

```bash
.venv/bin/python -m biomedical.ui          # default port 8099
UI_PORT=9000 .venv/bin/python -m biomedical.ui
```

## Framework: NiceGUI

Pure Python. No HTML, no JavaScript, no Node, no build step — you write Python and
it renders. Chosen over Reflex and a FastAPI+React split for one reason that
matters here: **a reviewer who must install Node and run a build step will not
inspect the evidence.** One command, one language, one process.

Server-side rendered, so no credential reaches the client.

## The seven surfaces

| Route | Answers | Load |
|---|---|---|
| `/` | *What is in the graph, from where, at what tier?* | 7.9s |
| `/review` | *Which resolutions need a human?* | 7.6s |
| `/entities` | *What do we believe about this entity, how strongly, since when?* | 1.9s |
| `/decision` | *Why this verdict, on what evidence, and what was withheld?* | 0.02s |
| `/eval` | *What do the numbers support, and what don't they?* | 0.04s |
| `/releases` | *What moved between vocabulary releases?* | 1.9s |
| `/claims` | *What does each published claim not establish?* | 0.02s |

The multi-second pages are Aura round-trip latency, not rendering — `/` issues six
graph queries at roughly 390 ms each. That is the same latency the decision trace
reports honestly.

## The visual language is a correctness feature

Component 00 R2.7: **a derived fact must not look like a curated fact.** If the
interface renders a 0.30-confidence inference with the same weight as a pinned
RxNorm identifier, it undoes the epistemic model underneath.

So every tier carries **three independent encodings** — border style, glyph and
label — and colour is only the fourth channel. The distinction survives
colour-blindness, greyscale printing and a screenshot in a slide deck.

| Tier | Border | Glyph | Label | Weight |
|---|---|---|---|---|
| asserted | solid | ● | authored | full |
| ingested | solid | ◆ | ingested | full |
| derived | **dashed** | ◇ | derived | reduced |
| runtime | dotted | ○ | observed | reduced |

Overlay states compose with tier: `⊘ below floor` (struck through), `⏱ stale`,
`⇄ contested`, `◔ needs review`, `⤬ withdrawn identifier`.

Defined once in `ui/theme.py`. Per-page variation is not permitted, and a test
asserts no page overrides it.

## The UI refuses to mislead

`ui/guards.py` implements refusals rather than conventions:

- **`render_rate()` will not display a proportion without `n` and an interval.** It
  returns a red *RENDER REFUSED* panel instead. A bare percentage cannot reach the
  screen.
- Intervals render as a **visible bar with a point-estimate tick**, never as a
  tooltip.
- An unregistered claim does not render at all.
- Conformance results carry a note that shape conformance is not factual
  correctness.

## What each surface is for

### `/decision` — the page that answers the rejection

Three things on it exist only because of the reviewer's questions:

1. **facts withheld** with ineligibility reasons — answers *"how do requests become
   trusted rule inputs?"* v1 had no equivalent view.
2. **`CANNOT_EVALUATE`** rendered in amber with its withheld dependency named,
   never as a green tick. The verdict reads `INCONCLUSIVE`, not `ALLOWED`.
3. **end-to-end timing** as the headline, with the 0.05 ms rule figure shown only
   alongside its qualifying note — answers *"does 0.09 ms include database
   access?"* (it did not).

### `/review` — the only writing surface

Verified working against the live graph. Accepting a candidate **supersedes**
rather than overwrites:

```
state=cleared        method=human_review          conf=1.0   superseded=None
state=needs_review   method=multiple_candidates   conf=0.3   superseded=True
```

The prior resolution is retained. The page shows a `superseded` counter labelled
*"retained, never deleted"*.

Queue size is pinned in the header and never hidden: **89 awaiting review** out of
189 resolutions. A visible backlog is more credible than none.

> **Bug found and fixed while testing this.** The backlog counts initially included
> superseded edges, so clearing an item did not decrease `needs_review` (90 → 90).
> A superseded edge correctly *keeps* its historical `review_state` — that is the
> point of append-only storage — but the backlog is about current state. All
> current-state queries now filter `coalesce(r.superseded,false) = false`.

### `/eval` — evidence access made visible

Each arm shows `facts=Y/N` and `rubric=Y/N` as explicit badges, because the
confound that sank v1 was precisely that these differed between arms and nobody
could see it. `judge_grounded` is tagged **THE MISSING CONTROL**. The rubric arm is
labelled a circularity demonstration, not a performance result. Degenerate arms
carry a label stating their accuracy is determined by class balance.

### `/releases` — real schema evolution

Seven genuine HGNC renames (`CDKN2 → CDKN2A`, `WS2A → MITF`, `PBT → KIT`, …), each
with the note that a rule citing the old symbol returns no match — indistinguishable
from "checked and clean" unless the outcome is `CANNOT_EVALUATE`.

### `/claims` — the register

Eight claims, grouped by category so an `availability` claim cannot sit under a
safety heading. Every one carries its `n`, its interval and a red
**does NOT establish** panel. Including the uncomfortable ones: the FAERS
co-reporting edges are registered under `safety` with an explicit statement that
they are **not evidence of harm**.

## Status

All seven pages return HTTP 200 with no server errors. 128 tests still pass with
no credentials.

Not yet built from the spec: fixture mode (the UI currently requires a live
graph), the contradiction board (`conflicts.py` is not implemented yet), and
time-travel on the provenance explorer.
