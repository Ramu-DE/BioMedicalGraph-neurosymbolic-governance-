"""Four-arm evaluation runner, extracted from the driver.

Materialises one FactSet per case and hands the identical object to every arm
declaring `gets_graph_facts`, so evidence parity is structural rather than
asserted. See FINDINGS.md for what the run produced.
"""

from __future__ import annotations

from collections import Counter

from ..clients.bedrock import BedrockClient
from ..eval import stats
from ..eval.arms import build_arms, run_case
from ..eval.cases import CASES, design

JUDGE_MODEL = "us.anthropic.claude-sonnet-4-6"


def run_four_arms(judge_model: str = JUDGE_MODEL, *, embedding_model: str = "amazon.titan-embed-text-v2:0",
                  embedding_dim: int = 1024):
    """Execute all four arms over the governance case set.

    Returns (rows, per_arm_verdicts, truth, computed_rates, llm_client).
    """
    llm = BedrockClient(model_id=judge_model, embedding_model_id=embedding_model,
                        embedding_dim=embedding_dim)
    arms = build_arms(llm)
    truth = [c.answer_is_wrong for c in CASES]
    per_arm: dict[str, list[bool]] = {a.name: [] for a in arms}
    rows: list[dict] = []

    for case in CASES:
        results, fact_hash = run_case(case, arms)
        for r in results:
            per_arm[r.arm].append(r.says_wrong)
            rows.append({
                "case_id": case.case_id, "category": case.category, "arm": r.arm,
                "answer_is_wrong": case.answer_is_wrong, "says_wrong": r.says_wrong,
                "correct": r.says_wrong == case.answer_is_wrong, "raw": r.raw,
                "latency_ms": round(r.latency_ms, 1), "error": r.error,
                "fact_hash": fact_hash[:12],
            })

    computed = {a.name: stats.rates(per_arm[a.name], truth) for a in arms}
    return rows, per_arm, truth, computed, arms, llm


def failure_table(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for arm in {r["arm"] for r in rows}:
        wrong = [r for r in rows if r["arm"] == arm and not r["correct"]]
        kinds = Counter(
            "false_pass (missed a bad answer)" if not r["says_wrong"]
            else "false_fail (failed a good answer)"
            for r in wrong
        )
        cats = Counter(r["category"] for r in wrong)
        out[arm] = {"errors": len(wrong), "kinds": dict(kinds), "categories": dict(cats)}
    return out
