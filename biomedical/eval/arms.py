"""The four evaluation arms (component 05 R1).

The point of this module is the third arm.

v1 compared rules-that-read-the-graph against a judge-that-reads-nothing and
attributed the difference to "rules vs LLM". That comparison was confounded: the
independent variable was *evidence access*. `judge_grounded` is the missing
control — the same judge, the same prompt, plus exactly the facts the
deterministic arm receives.

Parity is structural: the orchestrator materialises one `FactSet` per case and
hands the identical object to every arm declaring `gets_graph_facts`. Arms hold
no graph client, so there is no code path by which they could fetch anything else.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol

from .cases import Case

VERDICT_RE = re.compile(r"\b(PASS|FAIL)\b", re.I)


@dataclass(frozen=True)
class FactSet:
    """Immutable, content-hashed evidence. The hash is the parity proof."""

    facts: tuple[tuple[str, str], ...]

    @classmethod
    def of(cls, mapping: dict[str, str]) -> FactSet:
        return cls(facts=tuple(sorted(mapping.items())))

    @property
    def content_hash(self) -> str:
        blob = json.dumps(self.facts, sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()

    def render(self) -> str:
        return "\n".join(f"  {k} = {v}" for k, v in self.facts)


@dataclass(frozen=True)
class ArmResult:
    arm: str
    case_id: str
    says_wrong: bool          # the instrument's verdict
    raw: str = ""
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


class Arm(Protocol):
    name: str
    gets_graph_facts: bool
    gets_rubric: bool

    def judge(self, case: Case, facts: FactSet | None) -> ArmResult: ...


# --------------------------------------------------------------------------- #
# arm 1 - deterministic rules over graph facts
# --------------------------------------------------------------------------- #


class DeterministicArm:
    """Checks the answer's claims against the facts. No model involved."""

    name = "deterministic"
    gets_graph_facts = True
    gets_rubric = False

    # value -> the mutually exclusive alternatives it must not be confused with
    CONTRASTS: dict[str, tuple[str, ...]] = {
        "withdrawn": ("active",),
        "active": ("withdrawn",),
        "pd-l1": ("pd-1",),
        "pd-1": ("pd-l1",),
        "approved": ("investigational",),
        "investigational": ("approved",),
        "severe": ("moderate", "mild"),
        "moderate": ("severe",),
        "phase 2": ("phase 3",),
        "phase 3": ("phase 2",),
        "recruiting": ("completed", "terminated"),
    }

    def judge(self, case: Case, facts: FactSet | None) -> ArmResult:
        if facts is None:
            raise RuntimeError("deterministic arm requires facts")
        answer = case.answer.lower()
        violated: list[str] = []

        for key, value in facts.facts:
            v = value.lower()
            for wrong_alt in self.CONTRASTS.get(v, ()):
                # the answer asserts a contrasting value while the graph says otherwise
                if wrong_alt in answer and v not in answer:
                    violated.append(f"{key}: graph={value!r} answer asserts {wrong_alt!r}")

        # POL_HUB_DQ: a hub table with zero DQ rules cannot be treated as certified
        fd = dict(facts.facts)
        if fd.get("table:CustomerMaster.layer") == "hub" and fd.get(
            "table:CustomerMaster.dq_rule_count"
        ) == "0":
            if re.search(r"\b(certified|confirmed|cleared|done)\b", answer) and not re.search(
                r"\b(cannot|not certified|blocked|must not)\b", answer
            ):
                violated.append("POL_HUB_DQ: hub table has 0 DQ rules but answer certifies it")

        return ArmResult(
            arm=self.name,
            case_id=case.case_id,
            says_wrong=bool(violated),
            raw="; ".join(violated) or "no contradiction against supplied facts",
        )


# --------------------------------------------------------------------------- #
# arms 2-4 - LLM judge, differing ONLY in what evidence it is given
# --------------------------------------------------------------------------- #

BASE_PROMPT = """You are reviewing whether an answer given to a user is correct.

Question asked:
{question}

Answer given:
{answer}
{evidence}
Reply with exactly one word: PASS if the answer is correct, FAIL if it is not.
"""


class JudgeArm:
    """One judge, three evidence conditions. The prompt differs only by the
    evidence block, so the comparison isolates evidence access."""

    def __init__(self, llm, *, name: str, with_facts: bool, with_rubric: bool):
        self._llm = llm
        self.name = name
        self.gets_graph_facts = with_facts
        self.gets_rubric = with_rubric

    def _evidence_block(self, case: Case, facts: FactSet | None) -> str:
        if self.gets_rubric:
            return (
                "\nReference assessment (authoritative):\n"
                f"  {case.why}\n"
            )
        if self.gets_graph_facts:
            if facts is None:
                raise RuntimeError(f"{self.name} requires facts")
            return (
                "\nVerified facts from the knowledge graph (authoritative):\n"
                f"{facts.render()}\n"
            )
        return ""

    def judge(self, case: Case, facts: FactSet | None) -> ArmResult:
        prompt = BASE_PROMPT.format(
            question=case.question,
            answer=case.answer,
            evidence=self._evidence_block(case, facts),
        )
        out = self._llm.converse(prompt, max_tokens=8)
        verdict = VERDICT_RE.search(out.text or "")
        says_wrong = bool(verdict and verdict.group(1).upper() == "FAIL")
        return ArmResult(
            arm=self.name,
            case_id=case.case_id,
            says_wrong=says_wrong,
            raw=(out.text or "").strip()[:120],
            latency_ms=out.latency_ms,
            input_tokens=out.input_tokens,
            output_tokens=out.output_tokens,
            error=out.error,
        )


def build_arms(llm) -> list[Arm]:
    return [
        DeterministicArm(),
        JudgeArm(llm, name="judge_blind", with_facts=False, with_rubric=False),
        JudgeArm(llm, name="judge_grounded", with_facts=True, with_rubric=False),
        JudgeArm(llm, name="judge_rubric", with_facts=False, with_rubric=True),
    ]


class ParityError(RuntimeError):
    pass


def run_case(case: Case, arms: list[Arm]) -> tuple[list[ArmResult], str]:
    """Materialise facts ONCE, hand the identical object to every arm."""
    factset = FactSet.of(case.facts)
    seen_hashes: set[str] = set()
    results: list[ArmResult] = []

    for arm in arms:
        supplied = factset if arm.gets_graph_facts else None
        if supplied is not None:
            seen_hashes.add(supplied.content_hash)
        results.append(arm.judge(case, supplied))

    if len(seen_hashes) > 1:
        raise ParityError(
            f"{case.case_id}: fact-receiving arms saw different evidence {seen_hashes}"
        )
    return results, factset.content_hash
