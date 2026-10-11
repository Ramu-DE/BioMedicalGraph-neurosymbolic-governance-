"""Statistics for honest reporting (component 05 R2).

Pure functions, no I/O. Every rate carries its denominator and an interval; a
bare proportion is never returned as a headline.

The rule this module exists to enforce: 58% accuracy with kappa 0.000 is a
broken instrument that looks serviceable. Accuracy alone cannot tell you that.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Interval:
    lo: float
    hi: float

    def __str__(self) -> str:
        return f"[{self.lo:.3f}, {self.hi:.3f}]"


@dataclass(frozen=True)
class Rate:
    """A proportion that refuses to be reported without n and an interval."""

    value: float
    k: int
    n: int
    interval: Interval

    def __str__(self) -> str:
        return f"{self.value:.3f} {self.interval} (k={self.k}, n={self.n})"


@dataclass(frozen=True)
class Confusion:
    """Convention: the *positive* class is "the answer is wrong", because the
    instrument under test is a detector of bad answers.

      tp - correctly flagged a wrong answer
      fn - missed a wrong answer       (dangerous: a false pass)
      tn - correctly passed a good answer
      fp - failed a good answer        (a false fail)
    """

    tp: int
    fn: int
    tn: int
    fp: int

    @property
    def n(self) -> int:
        return self.tp + self.fn + self.tn + self.fp

    @property
    def positives(self) -> int:
        return self.tp + self.fn

    @property
    def negatives(self) -> int:
        return self.tn + self.fp

    def __str__(self) -> str:
        return f"tp={self.tp} fn={self.fn} tn={self.tn} fp={self.fp}"


def confusion(verdicts: Sequence[bool], truth: Sequence[bool]) -> Confusion:
    """`verdicts[i]` = instrument said "wrong"; `truth[i]` = answer really was wrong."""
    if len(verdicts) != len(truth):
        raise ValueError("verdicts and truth must be the same length")
    tp = sum(1 for v, t in zip(verdicts, truth, strict=True) if v and t)
    fn = sum(1 for v, t in zip(verdicts, truth, strict=True) if not v and t)
    tn = sum(1 for v, t in zip(verdicts, truth, strict=True) if not v and not t)
    fp = sum(1 for v, t in zip(verdicts, truth, strict=True) if v and not t)
    return Confusion(tp=tp, fn=fn, tn=tn, fp=fp)


def wilson(k: int, n: int, z: float = 1.959963985) -> Interval:
    """Wilson score interval. Behaves sensibly at k=0 and k=n, where the normal
    approximation collapses — which is exactly where this project's headline
    numbers live (0 of 5)."""
    if n == 0:
        return Interval(0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return Interval(max(0.0, centre - half), min(1.0, centre + half))


def rate(k: int, n: int) -> Rate:
    return Rate(value=(k / n if n else float("nan")), k=k, n=n, interval=wilson(k, n))


def cohens_kappa(c: Confusion) -> float:
    """Agreement above chance. Zero means no discriminative value whatsoever,
    regardless of how high raw accuracy looks."""
    n = c.n
    if n == 0:
        return float("nan")
    po = (c.tp + c.tn) / n
    pe = ((c.tp + c.fp) * (c.tp + c.fn) + (c.tn + c.fn) * (c.tn + c.fp)) / (n * n)
    if pe == 1.0:
        return float("nan")
    return (po - pe) / (1 - pe)


def bootstrap_kappa_ci(
    verdicts: Sequence[bool],
    truth: Sequence[bool],
    *,
    resamples: int = 10_000,
    seed: int = 20261011,
    alpha: float = 0.05,
) -> Interval:
    """Percentile bootstrap CI on kappa. Seeded, so it is reproducible."""
    n = len(verdicts)
    if n == 0:
        return Interval(float("nan"), float("nan"))
    rng = random.Random(seed)
    idx = range(n)
    draws: list[float] = []
    for _ in range(resamples):
        sample = [rng.choice(idx) for _ in idx]
        k = cohens_kappa(
            confusion([verdicts[i] for i in sample], [truth[i] for i in sample])
        )
        if not math.isnan(k):
            draws.append(k)
    if not draws:
        return Interval(float("nan"), float("nan"))
    draws.sort()
    lo = draws[int((alpha / 2) * len(draws))]
    hi = draws[min(len(draws) - 1, int((1 - alpha / 2) * len(draws)))]
    return Interval(lo, hi)


@dataclass(frozen=True)
class Rates:
    """The full picture. `accuracy` is present but is never the headline (R2.4)."""

    confusion: Confusion
    tpr: Rate
    tnr: Rate
    accuracy: Rate
    balanced_accuracy: float
    kappa: float
    kappa_ci: Interval
    degenerate: bool
    degenerate_note: str = ""

    ACCURACY_NOTE = "determined by class balance; not a headline metric"

    def report(self) -> str:
        lines = [
            f"  confusion        {self.confusion}  (n={self.confusion.n}: "
            f"{self.confusion.positives} wrong / {self.confusion.negatives} correct)",
            f"  TPR (catches bad) {self.tpr}",
            f"  TNR (passes good) {self.tnr}",
            f"  balanced acc      {self.balanced_accuracy:.3f}",
            f"  kappa             {self.kappa:.3f} {self.kappa_ci}",
            f"  accuracy          {self.accuracy}   <- {self.ACCURACY_NOTE}",
        ]
        if self.degenerate:
            lines.append(f"  !! DEGENERATE    {self.degenerate_note}")
        return "\n".join(lines)


def is_degenerate(verdicts: Sequence[bool]) -> tuple[bool, str]:
    """A constant predictor has no discriminative value by construction."""
    if not verdicts:
        return False, ""
    if all(verdicts):
        return True, "constant predictor: said WRONG to every case"
    if not any(verdicts):
        return True, "constant predictor: said CORRECT to every case"
    return False, ""


def rates(
    verdicts: Sequence[bool], truth: Sequence[bool], *, resamples: int = 10_000, seed: int = 20261011
) -> Rates:
    c = confusion(verdicts, truth)
    degenerate, note = is_degenerate(verdicts)
    tpr = rate(c.tp, c.positives)
    tnr = rate(c.tn, c.negatives)
    ba = (
        (tpr.value + tnr.value) / 2
        if c.positives and c.negatives
        else float("nan")
    )
    return Rates(
        confusion=c,
        tpr=tpr,
        tnr=tnr,
        accuracy=rate(c.tp + c.tn, c.n),
        balanced_accuracy=ba,
        kappa=cohens_kappa(c),
        kappa_ci=bootstrap_kappa_ci(verdicts, truth, resamples=resamples, seed=seed),
        degenerate=degenerate,
        degenerate_note=note,
    )
