"""Run the four-arm evaluation (component 05 Phase 2).

This is the experiment the Connected Data reviewer asked for:

    "The comparison needs equivalent evidence access, class balance and an
     analysis of failures."

v1 compared rules-with-facts against a judge-without-facts. `judge_grounded` is
the missing control. Either outcome is publishable and nothing here is tuned
toward either.

    .venv/bin/python scripts/run_four_arm_eval.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from biomedical.clients.bedrock import BedrockClient  # noqa: E402
from biomedical.eval import stats  # noqa: E402
from biomedical.eval.arms import build_arms, run_case  # noqa: E402
from biomedical.eval.cases import CASES, design  # noqa: E402
from biomedical.foundation import RunManifest  # noqa: E402

JUDGE_MODEL = "us.anthropic.claude-sonnet-4-6"


def main() -> int:
    cfg = config.load()
    d = design()

    print("four-arm governance evaluation")
    print("=" * 74)
    print(f"cases: {d['n_cases']}   class balance: "
          f"{d['class_balance']['answer_wrong']} wrong / "
          f"{d['class_balance']['answer_correct']} correct")
    print(f"rationale: {d['balance_rationale']}")
    print(f"falsifier: {d['falsifier']}")
    print(f"judge model: {JUDGE_MODEL}")
    print("=" * 74)

    llm = BedrockClient(
        region=cfg.embedding.provider and "us-west-2" or "us-west-2",
        model_id=JUDGE_MODEL,
        embedding_model_id=cfg.embedding.model_id,
        embedding_dim=cfg.embedding.dim,
    )
    arms = build_arms(llm)

    truth = [c.answer_is_wrong for c in CASES]
    per_arm: dict[str, list[bool]] = {a.name: [] for a in arms}
    rows: list[dict] = []
    errors = 0

    print("\nper-case verdicts  (W = says answer is wrong, . = says correct)")
    header = "  case            truth  " + "  ".join(f"{a.name[:14]:>14}" for a in arms)
    print(header)

    for case in CASES:
        results, fact_hash = run_case(case, arms)
        marks = []
        for r in results:
            per_arm[r.arm].append(r.says_wrong)
            if r.error:
                errors += 1
            marks.append("W" if r.says_wrong else ".")
            rows.append(
                {
                    "case_id": case.case_id,
                    "category": case.category,
                    "arm": r.arm,
                    "answer_is_wrong": case.answer_is_wrong,
                    "says_wrong": r.says_wrong,
                    "correct": r.says_wrong == case.answer_is_wrong,
                    "raw": r.raw,
                    "latency_ms": round(r.latency_ms, 1),
                    "error": r.error,
                    "fact_hash": fact_hash[:12],
                }
            )
        t = "WRONG" if case.answer_is_wrong else "ok   "
        print(f"  {case.case_id:<16}{t}  " + "  ".join(f"{m:>14}" for m in marks))

    # ---------------------------------------------------------------- metrics
    print("\n" + "=" * 74)
    print("RESULTS  (positive class = 'the answer is wrong')")
    print("=" * 74)

    computed: dict[str, stats.Rates] = {}
    for arm in arms:
        verdicts = per_arm[arm.name]
        r = stats.rates(verdicts, truth)
        computed[arm.name] = r
        badges = (
            f"facts={'Y' if arm.gets_graph_facts else 'N'} "
            f"rubric={'Y' if arm.gets_rubric else 'N'}"
        )
        print(f"\n{arm.name}   [{badges}]")
        print(r.report())

    # ---------------------------------------------------------------- reading
    print("\n" + "=" * 74)
    print("READING")
    print("=" * 74)
    blind, grounded, det = (
        computed["judge_blind"],
        computed["judge_grounded"],
        computed["deterministic"],
    )
    print(f"  judge_blind     TNR {blind.tnr}   kappa {blind.kappa:.3f}")
    print(f"  judge_grounded  TNR {grounded.tnr}   kappa {grounded.kappa:.3f}")
    print(f"  deterministic   TNR {det.tnr}   kappa {det.kappa:.3f}")
    if grounded.kappa > blind.kappa + 0.1:
        print("\n  -> Giving the judge the SAME facts materially improved it.")
        print("     The v1 finding was substantially about EVIDENCE ACCESS,")
        print("     not about rules versus LLMs. The confound was real.")
    elif abs(grounded.kappa - blind.kappa) <= 0.1:
        print("\n  -> Facts did not materially help the judge. The original")
        print("     finding survives the missing control.")
    else:
        print("\n  -> Facts made the judge worse. Worth investigating before claiming anything.")

    # ------------------------------------------------------- failure analysis
    print("\nfailure analysis by arm and category")
    for arm in arms:
        wrong = [
            r for r in rows
            if r["arm"] == arm.name and not r["correct"]
        ]
        if not wrong:
            print(f"  {arm.name:<16} no errors")
            continue
        kinds = Counter(
            ("false_pass (missed a bad answer)" if not r["says_wrong"] else "false_fail (failed a good answer)")
            for r in wrong
        )
        cats = Counter(r["category"] for r in wrong)
        print(f"  {arm.name:<16} {len(wrong)} error(s): {dict(kinds)}")
        print(f"                   categories: {dict(cats)}")

    # ---------------------------------------------------------------- outputs
    manifest = RunManifest.create(
        design=d,
        source_versions={"graph_facts": "authored-v1", "judge_model": JUDGE_MODEL},
        slice_params={"suite": "governance-module-E"},
        seeds={"bootstrap": 20261011},
        trust_policy_version=cfg.trust.policy_version,
        notes={"embedding_model": cfg.embedding.model_id, "errors": str(errors)},
    )
    mdir = ROOT / "manifests"
    mpath = manifest.write(mdir)

    report = {
        "run_id": manifest.run_id,
        "design_hash": manifest.design_hash,
        "design": d,
        "judge_model": JUDGE_MODEL,
        "arms": {
            a.name: {
                "gets_graph_facts": a.gets_graph_facts,
                "gets_rubric": a.gets_rubric,
                "confusion": computed[a.name].confusion.__dict__,
                "tpr": {"value": computed[a.name].tpr.value,
                        "ci": [computed[a.name].tpr.interval.lo, computed[a.name].tpr.interval.hi],
                        "k": computed[a.name].tpr.k, "n": computed[a.name].tpr.n},
                "tnr": {"value": computed[a.name].tnr.value,
                        "ci": [computed[a.name].tnr.interval.lo, computed[a.name].tnr.interval.hi],
                        "k": computed[a.name].tnr.k, "n": computed[a.name].tnr.n},
                "kappa": computed[a.name].kappa,
                "kappa_ci": [computed[a.name].kappa_ci.lo, computed[a.name].kappa_ci.hi],
                "balanced_accuracy": computed[a.name].balanced_accuracy,
                "accuracy": {"value": computed[a.name].accuracy.value,
                             "note": stats.Rates.ACCURACY_NOTE},
                "degenerate": computed[a.name].degenerate,
                "degenerate_note": computed[a.name].degenerate_note,
            }
            for a in arms
        },
        "rows": rows,
    }
    rpath = mdir / f"{manifest.run_id}_report.json"
    rpath.write_text(json.dumps(report, indent=2, default=str))

    print(f"\nllm usage: {llm.usage_summary()}")
    if errors:
        print(f"WARNING: {errors} arm call(s) errored - see report")
    print(f"manifest: {mpath.relative_to(ROOT)}")
    print(f"report:   {rpath.relative_to(ROOT)}")
    print(f"design hash: {manifest.design_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
