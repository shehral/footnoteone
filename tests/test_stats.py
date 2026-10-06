import math
import random

import pytest

from footnoteone.stats import (
    cluster_bootstrap_mean,
    cluster_t_interval,
    holm,
    jaccard,
    mde,
    paired_sign_flip_p,
    t_interval,
    t_quantile,
    verdict,
    wilson,
)


def test_wilson_known_values_and_edges():
    lo, hi = wilson(12, 40)
    assert 0.17 < lo < 0.19 and 0.45 < hi < 0.47  # 12/40 = 0.30, Wilson 95% about (0.18, 0.46)
    assert wilson(0, 10) == pytest.approx((0.0, 0.2775), abs=0.01)
    assert wilson(10, 10)[1] == 1.0
    assert all(wilson(0, n)[0] == 0.0 and wilson(n, n)[1] == 1.0 for n in range(1, 300))  # exact edges
    assert wilson(0, 0) is None


def test_wilson_rejects_counts_outside_zero_to_n():
    for k, n in ((-1, 10), (11, 10), (1, 0)):
        with pytest.raises(ValueError, match="0 <= k <= n"):
            wilson(k, n)


def test_wilson_interval_coverage_is_near_nominal():
    rng = random.Random(1)
    p, n, covered, trials = 0.3, 40, 0, 2000
    for _ in range(trials):
        k = sum(rng.random() < p for _ in range(n))
        lo, hi = wilson(k, n)
        covered += lo <= p <= hi
    assert 0.93 <= covered / trials <= 0.975


def test_cluster_bootstrap_mean_of_cluster_means():
    groups = [[1, 1, 1], [0, 0, 0], [1, 0, 1, 0]]
    mean, lo, hi = cluster_bootstrap_mean(groups, b=500, seed=3)
    assert mean == pytest.approx(0.5)  # (1 + 0 + 0.5) / 3
    assert 0.0 <= lo <= mean <= hi <= 1.0
    # Same seed, same output. Irregular cluster means make the percentiles depend on the seed.
    spread = [[(i * 0.6180339887) % 1] for i in range(1, 16)]
    assert cluster_bootstrap_mean(spread, seed=4) == cluster_bootstrap_mean(spread, seed=4)
    assert cluster_bootstrap_mean(spread, seed=4) != cluster_bootstrap_mean(spread, seed=5)


def test_cluster_bootstrap_single_cluster_is_degenerate_but_defined():
    mean, lo, hi = cluster_bootstrap_mean([[0.2, 0.4]], b=100, seed=0)
    assert mean == lo == hi == pytest.approx(0.3)


def test_cluster_bootstrap_with_no_values_returns_none():
    assert cluster_bootstrap_mean([]) is None
    assert cluster_bootstrap_mean([[], []]) is None


def test_cluster_bootstrap_rejects_b_below_one():
    with pytest.raises(ValueError, match="b >= 1"):
        cluster_bootstrap_mean([[1.0], [0.0]], b=0)


def test_t_quantile_matches_table_values():
    for df, expected in ((1, 12.706), (2, 4.303), (5, 2.571), (10, 2.228), (29, 2.045), (100, 1.984)):
        assert t_quantile(0.975, df) == pytest.approx(expected, abs=2e-3)
    assert t_quantile(0.5, 7) == pytest.approx(0.0, abs=1e-9)
    assert t_quantile(0.025, 5) == pytest.approx(-2.571, abs=2e-3)
    assert t_quantile(0.975, 10**6) == pytest.approx(1.959966, abs=1e-6)  # huge df: rounding noise, no stall
    for p, df in ((0.0, 5), (1.0, 5), (0.975, 0)):
        with pytest.raises(ValueError):
            t_quantile(p, df)


def test_t_interval_is_mean_plus_minus_t_times_standard_error_unclipped():
    centre, lo, hi = t_interval([0.2, 0.4, 0.6])  # mean 0.4, sample sd 0.2, t(0.975, df=2) = 4.302653
    half = 4.302653 * 0.2 / math.sqrt(3)
    assert centre == pytest.approx(0.4)
    assert (lo, hi) == pytest.approx((0.4 - half, 0.4 + half), abs=1e-6)
    assert lo < 0  # not clipped to [0, 1]
    assert t_interval([0.5]) is None and t_interval([]) is None


def test_cluster_t_interval_uses_cluster_means_and_needs_two_clusters():
    via_clusters = cluster_t_interval([[0.0, 0.4], [0.4], [], [0.6, 0.6, 0.6]])  # means 0.2, 0.4, 0.6
    assert via_clusters == pytest.approx(t_interval([0.2, 0.4, 0.6]))
    assert cluster_t_interval([[1.0, 0.0], []]) is None
    assert cluster_t_interval([]) is None


def test_cluster_t_interval_coverage_at_six_intents():
    rng = random.Random(21)
    p, k, answers, covered, trials = 0.3, 6, 8, 0, 2000
    for _ in range(trials):
        groups = [[float(rng.random() < p) for _ in range(answers)] for _ in range(k)]
        _, lo, hi = cluster_t_interval(groups)
        covered += lo <= p <= hi
    assert covered / trials >= 0.92


def test_sign_flip_exact_small_n_and_zero_diffs():
    assert paired_sign_flip_p([0.0, 0.0, 0.0, 0.0]) == 1.0
    p = paired_sign_flip_p([0.3, 0.2, 0.25, 0.4, 0.35, 0.3])  # all positive, n = 6 -> 2 / 64
    assert p == pytest.approx(2 / 64)
    assert paired_sign_flip_p([0.1 * (i + 1) for i in range(12)]) == 2 / 4096  # n = 12 is still exact


def test_sign_flip_type_one_error_near_alpha_under_null():
    rng = random.Random(7)
    rejections, trials, n = 0, 300, 10
    for _ in range(trials):
        diffs = [rng.gauss(0, 1) for _ in range(n)]
        rejections += paired_sign_flip_p(diffs) < 0.05
    assert rejections / trials < 0.09


def test_sign_flip_large_n_uses_monte_carlo_and_detects_shift():
    rng = random.Random(11)
    diffs = [rng.gauss(0.5, 1) for _ in range(40)]
    assert paired_sign_flip_p(diffs, b=5000, seed=1) < 0.05
    assert paired_sign_flip_p([1.0] * 20, b=999) == pytest.approx(1 / 1000)  # (count + 1) / (b + 1), count 0


def test_paired_sign_flip_rejects_b_below_one():
    with pytest.raises(ValueError, match="b >= 1"):
        paired_sign_flip_p([0.1] * 20, b=0)


def test_holm_adjustment_is_monotone_and_capped():
    adj = holm([0.01, 0.04, 0.03])
    assert adj[0] == pytest.approx(0.03)
    assert adj[2] == pytest.approx(0.06)
    assert adj[1] == pytest.approx(0.06)
    assert all(0 <= a <= 1 for a in holm([0.5, 0.9, 0.7]))


def test_mde_matches_formula_and_grows_with_icc():
    base = mde(0.2, n_per_arm=200, m=1, icc=0.0)
    expected = (1.959964 + 0.841621) * math.sqrt(2 * 0.2 * 0.8 / 200)
    assert base == pytest.approx(expected, rel=1e-4)
    # m = 6, ICC = 0.3: DEFF = 1 + 5 * 0.3 = 2.5
    assert mde(0.2, n_per_arm=200, m=6, icc=0.3) == pytest.approx(expected * math.sqrt(2.5), rel=1e-4)


def test_mde_rejects_zero_or_full_baseline():
    for p in (0.0, 1.0, 1.5):
        with pytest.raises(ValueError, match="no detectable-effect size"):
            mde(p, n_per_arm=200)


def test_verdict_rules():
    assert verdict(0.02, 0.18, p_adj=0.01, n_intents=12) == "moved"
    assert verdict(-0.03, 0.04, p_adj=0.6, n_intents=12) == "no_change"
    assert verdict(-0.15, 0.20, p_adj=0.6, n_intents=12) == "cant_tell"
    assert verdict(0.02, 0.18, p_adj=0.01, n_intents=5) == "insufficient"
    assert verdict(0.02, 0.08, p_adj=0.01, n_intents=12) == "moved"  # moved outranks no_change


def test_verdict_zero_width_interval_is_cant_tell_unless_moved():
    assert verdict(0.0, 0.0, p_adj=1.0, n_intents=6) == "cant_tell"  # no spread is no evidence of no change
    assert verdict(-0.03, 0.04, p_adj=0.6, n_intents=12) == "no_change"  # a real interval still can be
    assert verdict(0.2, 0.2, p_adj=0.03, n_intents=6) == "moved"  # a significant shift is still moved


def test_verdict_rejects_inverted_interval():
    with pytest.raises(ValueError, match="diff_lo <= diff_hi"):
        verdict(0.1, -0.1, p_adj=0.5, n_intents=12)


def test_jaccard():
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert jaccard(set(), set()) is None
