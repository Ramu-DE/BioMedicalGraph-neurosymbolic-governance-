"""Component 05 R2 — statistics, with a regression fixture against v1's
published numbers.

The module-E matrices below are recomputed from v1's committed
`eval_comprehensive_results.json`. They are pinned here so that any change to
the statistics code which would alter the published figures fails loudly.
"""

from __future__ import annotations

import math

import pytest

from biomedical.eval.stats import (
    Confusion,
    bootstrap_kappa_ci,
    cohens_kappa,
    confusion,
    is_degenerate,
    rate,
    rates,
    wilson,
)

# ---- v1 module E, n=12, ground truth 7 answers wrong / 5 correct -------------
V1 = {
    "deterministic": Confusion(tp=6, fn=1, tn=5, fp=0),
    "judge_no_rubric": Confusion(tp=7, fn=0, tn=0, fp=5),
    "judge_with_rubric": Confusion(tp=7, fn=0, tn=5, fp=0),
}


def test_v1_deterministic_matrix_reproduces_published_figures():
    c = V1["deterministic"]
    assert c.n == 12
    assert (c.tp + c.tn) / c.n == pytest.approx(0.9167, abs=1e-4)   # README: 92%
    assert c.tp / c.positives == pytest.approx(0.857, abs=1e-3)     # README: TPR 86%
    assert c.tn / c.negatives == pytest.approx(1.000)               # README: TNR 100%
    assert cohens_kappa(c) == pytest.approx(0.833, abs=1e-3)        # README: kappa 0.833


def test_v1_unanchored_judge_is_a_constant_fail_predictor():
    """The Agreement Illusion, pinned: 58% accuracy with zero specificity."""
    c = V1["judge_no_rubric"]
    assert (c.tp + c.tn) / c.n == pytest.approx(0.5833, abs=1e-4)   # README: 58%
    assert c.tp / c.positives == pytest.approx(1.000)               # caught all 7 bad
    assert c.tn / c.negatives == pytest.approx(0.000)               # failed all 5 good
    assert c.fp == 5                                                # README: 5 false fails
    assert cohens_kappa(c) == pytest.approx(0.000, abs=1e-9)        # README: kappa 0.000


def test_v1_rubric_judge_is_perfect_because_it_was_given_the_answer():
    c = V1["judge_with_rubric"]
    assert cohens_kappa(c) == pytest.approx(1.000)


def test_zero_specificity_interval_is_wide_at_n_equals_five():
    """0 of 5 does not establish a magnitude, and the interval must show that."""
    ci = wilson(0, 5)
    assert ci.lo == pytest.approx(0.0, abs=1e-9)
    assert 0.35 < ci.hi < 0.55, f"expected ~0.43, got {ci.hi}"


# ---- confusion ---------------------------------------------------------------


def test_confusion_counts_each_cell():
    verdicts = [True, True, False, False]
    truth = [True, False, True, False]
    c = confusion(verdicts, truth)
    assert (c.tp, c.fp, c.fn, c.tn) == (1, 1, 1, 1)


def test_confusion_requires_equal_lengths():
    with pytest.raises(ValueError, match="same length"):
        confusion([True], [True, False])


# ---- Wilson ------------------------------------------------------------------


@pytest.mark.parametrize("k,n", [(0, 1), (0, 5), (1, 5), (5, 5), (3, 10), (50, 100)])
def test_wilson_always_within_unit_interval(k, n):
    ci = wilson(k, n)
    assert 0.0 <= ci.lo <= ci.hi <= 1.0


def test_wilson_brackets_the_point_estimate():
    ci = wilson(3, 10)
    assert ci.lo <= 0.3 <= ci.hi


def test_wilson_narrows_as_n_grows():
    small = wilson(5, 10)
    large = wilson(500, 1000)
    assert (large.hi - large.lo) < (small.hi - small.lo)


def test_wilson_on_empty_denominator_is_maximally_uncertain():
    ci = wilson(0, 0)
    assert (ci.lo, ci.hi) == (0.0, 1.0)


def test_rate_carries_its_denominator():
    r = rate(2, 5)
    assert (r.k, r.n) == (2, 5)
    assert "k=2, n=5" in str(r)


# ---- kappa -------------------------------------------------------------------


def test_kappa_is_zero_for_a_constant_predictor():
    truth = [True] * 7 + [False] * 5
    assert cohens_kappa(confusion([True] * 12, truth)) == pytest.approx(0.0, abs=1e-9)


def test_kappa_is_one_for_perfect_agreement():
    truth = [True] * 7 + [False] * 5
    assert cohens_kappa(confusion(list(truth), truth)) == pytest.approx(1.0)


def test_kappa_is_negative_for_systematic_disagreement():
    truth = [True] * 6 + [False] * 6
    assert cohens_kappa(confusion([not t for t in truth], truth)) < 0


def test_kappa_is_nan_on_empty_input():
    assert math.isnan(cohens_kappa(Confusion(0, 0, 0, 0)))


# ---- bootstrap ---------------------------------------------------------------


def test_bootstrap_is_reproducible_given_a_seed():
    truth = [True] * 7 + [False] * 5
    verdicts = [True] * 6 + [False] + [False] * 5
    a = bootstrap_kappa_ci(verdicts, truth, resamples=500, seed=7)
    b = bootstrap_kappa_ci(verdicts, truth, resamples=500, seed=7)
    assert (a.lo, a.hi) == (b.lo, b.hi)


def test_bootstrap_brackets_the_point_estimate():
    truth = [True] * 7 + [False] * 5
    verdicts = [True] * 6 + [False] + [False] * 5
    point = cohens_kappa(confusion(verdicts, truth))
    ci = bootstrap_kappa_ci(verdicts, truth, resamples=2000, seed=11)
    assert ci.lo <= point <= ci.hi


def test_constant_predictor_kappa_ci_contains_zero():
    truth = [True] * 7 + [False] * 5
    ci = bootstrap_kappa_ci([True] * 12, truth, resamples=500, seed=3)
    assert ci.lo <= 0.0 <= ci.hi or (math.isnan(ci.lo) and math.isnan(ci.hi))


# ---- degeneracy --------------------------------------------------------------


def test_all_fail_is_degenerate():
    flag, note = is_degenerate([True] * 12)
    assert flag and "said WRONG to every case" in note


def test_all_pass_is_degenerate():
    flag, note = is_degenerate([False] * 12)
    assert flag and "said CORRECT to every case" in note


def test_mixed_verdicts_are_not_degenerate():
    assert is_degenerate([True, False])[0] is False


# ---- full report -------------------------------------------------------------


def test_rates_labels_accuracy_as_non_headline():
    truth = [True] * 7 + [False] * 5
    r = rates([True] * 12, truth, resamples=200)
    assert r.degenerate
    assert "not a headline metric" in r.report()
    assert "DEGENERATE" in r.report()


def test_rates_reports_class_balance_in_its_output():
    truth = [True] * 7 + [False] * 5
    r = rates([True] * 6 + [False] * 6, truth, resamples=200)
    assert "7 wrong / 5 correct" in r.report()
