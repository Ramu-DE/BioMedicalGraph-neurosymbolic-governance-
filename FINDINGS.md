# Findings — the four-arm governance evaluation

Run `run_20261011_093151_5b859c`, design hash `28a95d9341f607bf`.
Judge: `us.anthropic.claude-sonnet-4-6` (the same model v1 used, so results stay
comparable). 12 cases, ground truth **7 answers wrong / 5 correct**. Cost $0.0145.

Reproduce: `.venv/bin/python scripts/run_four_arm_eval.py`

---

## The headline

**The Connected Data reviewer was right. The v1 comparison was confounded.**

| Arm | facts | rubric | TPR | TNR | κ | κ 95% CI |
|---|---|---|---|---|---|---|
| deterministic | Y | N | 0.857 *(1.000 here)* | 1.000 | **1.000** | [1.000, 1.000] |
| judge_blind | N | N | 0.857 | **0.400** | **0.273** | **[−0.286, 0.800]** |
| **judge_grounded** | **Y** | N | 0.857 | **1.000** | **0.833** | [0.437, 1.000] |
| judge_rubric | N | Y | 1.000 | 1.000 | 1.000 | [1.000, 1.000] |

Giving the judge **the same facts the rules get** moved κ from **0.273 to 0.833**.
The gap between "rules" and "LLM judge" closed almost entirely, and
`judge_grounded`'s interval overlaps `deterministic`'s.

### What this means for the claim

The v1 claim — *rules beat LLM judges* — **is not supported** once evidence access
is equalised. What the data does support is narrower and more useful:

> An evaluator with no access to authoritative facts has poor specificity. It
> fails correct answers because it cannot verify them. Give it the facts and most
> of the deficit disappears.

The variable was **evidence access**, not evaluator architecture. That is exactly
what the reviewer said, and the missing control proves it.

---

## Secondary findings

**1. The blind judge's κ interval includes zero.** [−0.286, 0.800] at n=12. Even
its 0.273 is not distinguishable from no skill. Any claim resting on this point
estimate is unsupportable, in either direction.

**2. The errors differ in kind, not just count.** The blind judge made 4 errors:
**3 false fails** (failed good answers) and 1 false pass. It is
over-conservative — it refuses what it cannot verify. That is the Agreement
Illusion mechanism, now measured directly rather than inferred.

**3. Facts did not fix every case, and the exception is the interesting one.**
`judge_grounded`'s single remaining error is `E03-catalog-wrong` — the catalog
says `certified: true`, `dq_rule_count` is 0, and the governing policy requires
at least one DQ rule for a hub table. The facts alone were insufficient; the case
needs the **policy inference** on top of them. That is a real argument for a rule
engine, and it is narrow and defensible in a way "rules beat LLMs" was not.

**4. The rubric arm scoring 1.000 is a circularity demonstration, not a result.**
The rubric contains the correct assessment. It is reported as a labelled control.

---

## Honest limits

- **n = 12.** Five true-negative opportunities. Every interval above is wide and
  stated as such.
- **One domain, one judge model, one prompt style, temperature 0.** No claim about
  LLM judges in general.
- **The deterministic arm was built against these cases.** It encodes the
  contrasts the cases test (`PD-1` vs `PD-L1`, `Withdrawn` vs `Active`), so its
  1.000 should be read as "a correctly specified rule set catches what it was
  specified for", not as evidence of generality. v1's deterministic arm scored
  0.917 on the same design and missed one case; that is the more realistic figure.
- **Cases are authored, facts are synthetic.** Real multi-source ingestion is
  component 03 and has not been built.
- Positive class is "the answer is wrong", so TPR = catching bad answers,
  TNR = correctly passing good ones.

---

## What changed in the claim register

| v1 claim | Status |
|---|---|
| "LLM-as-Judge has zero predictive value" | **Withdrawn.** κ 0.273 [−0.286, 0.800] blind; 0.833 grounded. |
| "Deterministic beats LLM judging" | **Withdrawn as stated.** Not supported once evidence is equalised. |
| "The judge needs ground truth to work" | **Retained, sharpened.** The rubric arm needs the answer; the grounded arm needs only the facts, which is a materially different and weaker requirement. |
| — | **New:** evidence access, not evaluator sophistication, explains most of the measured gap. |
| — | **New:** the one case facts could not fix required a policy inference over those facts. |

---

## Why this is a better talk

The rejected submission had a dramatic claim that its own evidence could not
carry. This run replaces it with a result that is smaller, survivable, and more
interesting to a room of practitioners:

> I built an experiment to show rules beat LLM judges. A reviewer pointed out my
> two arms had different access to evidence. I added the missing control, and the
> effect I had attributed to method was substantially attributable to access. The
> finding that survived is the one worth having.

That is a talk about experimental design, which is what The Fifth Elephant's CFP
asks for — and it is now backed by a reproducible run with a design hash,
intervals on every rate, and a failure analysis.
