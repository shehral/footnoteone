"""Statistics with their denominators spelled out. Pure functions, standard library only."""

from __future__ import annotations

import itertools
import math
import random
from statistics import NormalDist, mean, stdev
from typing import Literal

Verdict = Literal["moved", "no_change", "cant_tell", "insufficient"]
_Z = NormalDist()


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float] | None:
    """Wilson score interval for k successes in n trials. None when n == 0 (never 0).

    Numerator k: runs that meet the metric's condition (for example activated runs citing an owned URL).
    Denominator n: runs the metric is defined over (for example activated runs). Returns (lo, hi) clamped
    to [0, 1]; the default z gives a 95% interval. The lower bound is exactly 0 when k == 0 and the upper
    bound exactly 1 when k == n, where float rounding alone could leave them a hair off. Raises ValueError
    when k < 0 or k > n, which is a programming error, not an empty denominator.
    """
    if not 0 <= k <= n:
        raise ValueError(f"wilson needs 0 <= k <= n, got k={k}, n={n}")
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    lo = 0.0 if k == 0 else max(0.0, centre - half)
    hi = 1.0 if k == n else min(1.0, centre + half)
    return (lo, hi)


def cluster_bootstrap_mean(
    groups: list[list[float]], b: int = 2000, seed: int = 0
) -> tuple[float, float, float] | None:
    """Mean of cluster means (one cluster per intent) with a percentile bootstrap over clusters.

    Resamples clusters with replacement; the statistic is the mean of the resampled clusters' means,
    which matches METRICS.md: intent level = mean over its runs, library level = mean over intents.

    Cluster mean: numerator = sum of the intent's run values (1 or 0 per run for a rate), denominator =
    the intent's runs. Point estimate: numerator = sum of the cluster means, denominator = non-empty
    clusters (empty clusters are dropped). Returns (point, lo, hi), a 95% percentile interval from b
    resamples; a single cluster returns lo == hi == point. None when no cluster has a value (never 0).
    Raises ValueError when b < 1.
    """
    if b < 1:
        raise ValueError(f"cluster_bootstrap_mean needs b >= 1 resamples, got b={b}")
    means = [mean(g) for g in groups if g]
    if not means:
        return None
    point = mean(means)
    if len(means) == 1:
        return point, point, point
    rng = random.Random(seed)
    draws = sorted(mean(rng.choices(means, k=len(means))) for _ in range(b))
    lo = draws[int(0.025 * (b - 1))]
    hi = draws[int(0.975 * (b - 1))]
    return point, lo, hi


def _cf_term(j: int, a: float, b: float, x: float) -> float:
    """Partial numerator d_j of the continued fraction for I_x(a, b), DLMF 8.17.23."""
    m = j // 2
    if j % 2 == 0:
        return m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m))
    return -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))


def _cf_tail(a: float, b: float, x: float) -> float:
    """F = 1 + d_1 / (1 + d_2 / (1 + ...)), the denominator in DLMF 8.17.22, by the modified Lentz method.

    Modified Lentz algorithm (W. J. Lentz, Applied Optics 15, 1976; I. J. Thompson and A. R. Barnett,
    J. Comput. Phys. 64, 1986): for F = b0 + a1 / (b1 + a2 / (b2 + ...)), carry C_j = b_j + a_j / C_(j-1)
    and D_j = 1 / (b_j + a_j D_(j-1)) from C_0 = F_0 = b0 and D_0 = 0, and take F_j = F_(j-1) C_j D_j
    until C_j D_j is 1 to rounding. Here b0 = b_j = 1 and a_j = d_j; a zero denominator becomes tiny.
    """
    tiny = 1e-300
    value, ratio_c, ratio_d = 1.0, 1.0, 0.0
    for j in range(1, 20_000):
        d_j = _cf_term(j, a, b, x)
        ratio_c = 1.0 + d_j / ratio_c
        ratio_d = 1.0 + d_j * ratio_d
        ratio_c = ratio_c if abs(ratio_c) > tiny else tiny
        ratio_d = 1.0 / (ratio_d if abs(ratio_d) > tiny else tiny)
        value *= ratio_c * ratio_d
        if abs(ratio_c * ratio_d - 1.0) <= 1e-15:
            return value
    raise ArithmeticError("incomplete beta continued fraction did not converge")


def _betainc(a: float, b: float, x: float, y: float) -> float:
    """Regularized incomplete beta I_x(a, b); y = 1 - x is passed in to keep precision near x = 1.

    DLMF 8.17.22, I_x(a, b) = x^a y^b / (a B(a, b) F) with F from _cf_tail, converges rapidly for
    x < (a + 1) / (a + b + 2). Elsewhere use DLMF 8.17.4, I_x(a, b) = 1 - I_y(b, a), whose fraction
    converges rapidly there; both forms share x^a y^b / B(a, b). Source: https://dlmf.nist.gov/8.17
    """
    if x <= 0.0:
        return 0.0
    if y <= 0.0:
        return 1.0
    log_shared = a * math.log(x) + b * math.log(y) + math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    shared = math.exp(log_shared)
    if x < (a + 1.0) / (a + b + 2.0):
        return shared / (a * _cf_tail(a, b, x))
    return 1.0 - shared / (b * _cf_tail(b, a, y))


def _t_sf(t: float, df: float) -> float:
    """P(T > t) for t >= 0: half of I_x(df / 2, 1 / 2) with x = df / (df + t^2)."""
    t2 = t * t
    return 0.5 * _betainc(df / 2, 0.5, df / (df + t2), t2 / (df + t2))


def _t_pdf(t: float, df: float) -> float:
    """Student t density at t."""
    log_norm = math.lgamma((df + 1) / 2) - math.lgamma(df / 2) - 0.5 * math.log(df * math.pi)
    return math.exp(log_norm - (df + 1) / 2 * math.log1p(t * t / df))


def t_quantile(p: float, df: int) -> float:
    """Student t quantile: the t with P(T <= t) = p for df degrees of freedom. Standard library only.

    The tail probability comes from the regularized incomplete beta function (a continued fraction).
    Newton's method solves P(T > t) = tail starting from the normal quantile, which sits closer to 0 than
    the t quantile; the tail is convex there, so the steps rise to the root without overshooting. At the
    tail quantiles used here (p = 0.975) the relative error is a few times 1e-12 or less up to df = 10**5,
    degrading beyond; central quantiles at df >= 10**4 reach about 5e-11. In t_interval, df = number of
    values - 1.
    Raises ValueError unless 0 < p < 1 and df >= 1.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"t_quantile needs 0 < p < 1, got p={p}")
    if df < 1:
        raise ValueError(f"t_quantile needs df >= 1, got df={df}")
    if p == 0.5:
        return 0.0
    tail = min(p, 1.0 - p)  # solving on the tail keeps tiny p exact
    t, last = -_Z.inv_cdf(tail), math.inf
    for _ in range(200):
        step = (_t_sf(t, df) - tail) / _t_pdf(t, df)
        t += step
        size = abs(step) / max(1.0, t)
        # Stop when the step is negligible, or when steps near the root stop shrinking (rounding noise).
        if size <= 1e-12 or (size <= 1e-6 and size >= last):
            return t if p > 0.5 else -t
        last = size
    raise ArithmeticError(f"t_quantile did not converge for p={p}, df={df}")


def t_interval(values: list[float], alpha: float = 0.05) -> tuple[float, float, float] | None:
    """Mean with a two-sided (1 - alpha) Student t interval: mean +/- t_{1-alpha/2, k-1} * sd / sqrt(k).

    Numerator of the mean: the sum of the values; denominator: k, the number of values. sd is the sample
    standard deviation (k - 1 in its denominator), so the standard error is sd / sqrt(k). Returns
    (mean, lo, hi), not clipped to any range. None when k < 2, since one value has no spread (never 0).
    Identical values give sd = 0 and a zero-width interval; callers must treat zero width as "cannot
    judge", not "no change".
    """
    k = len(values)
    if k < 2:
        return None
    centre = mean(values)
    half = t_quantile(1 - alpha / 2, k - 1) * stdev(values) / math.sqrt(k)
    return centre, centre - half, centre + half


def cluster_t_interval(groups: list[list[float]], alpha: float = 0.05) -> tuple[float, float, float] | None:
    """Mean of cluster means (one cluster per intent) with a Student t interval over the cluster means.

    Cluster mean: numerator = sum of the intent's run values, denominator = the intent's runs. Then
    t_interval over the k non-empty clusters (empty ones are dropped), so df = k - 1. Same point estimate
    as cluster_bootstrap_mean; the t quantile widens the interval at few intents, where the percentile
    bootstrap is too narrow. Bounds are not clipped to [0, 1]; the caller decides how to show bounds
    outside it. None when fewer than 2 clusters have values (never 0). Identical cluster means (for
    example every intent at 0) give a zero-width interval; callers must treat zero width as "cannot
    judge", not "no change".
    """
    return t_interval([mean(g) for g in groups if g], alpha)


def paired_sign_flip_p(diffs: list[float], b: int = 20000, seed: int = 0) -> float:
    """Two-sided p for mean(diffs) == 0 by sign flipping. Exact for n <= 12, Monte Carlo above.

    diffs holds one paired difference per shared intent (for example its cited rate in the later window
    minus the earlier one), so n = shared intents. Exact: numerator = sign vectors whose |sum| is at least
    the observed |sum|, denominator = all 2**n sign vectors. Monte Carlo: numerator = random sign vectors
    at least as extreme, plus 1; denominator = b + 1. Returns 1.0 when diffs is empty or sums to 0.
    Raises ValueError when b < 1.
    """
    if b < 1:
        raise ValueError(f"paired_sign_flip_p needs b >= 1 Monte Carlo draws, got b={b}")
    n = len(diffs)
    if n == 0:
        return 1.0
    observed = abs(sum(diffs))
    if observed == 0:
        return 1.0
    if n <= 12:
        count = 0
        for signs in itertools.product((1, -1), repeat=n):
            if abs(sum(s * d for s, d in zip(signs, diffs, strict=True))) >= observed - 1e-12:
                count += 1
        return count / (2**n)
    rng = random.Random(seed)
    count = 0
    for _ in range(b):
        if abs(sum(d if rng.random() < 0.5 else -d for d in diffs)) >= observed - 1e-12:
            count += 1
    return (count + 1) / (b + 1)


def holm(pvals: list[float]) -> list[float]:
    """Holm step-down adjusted p-values, in the input order.

    m = len(pvals), one p-value per engine. The p-value ranked i (0-based, ascending) is multiplied by
    m - i, carried forward as a running maximum so adjusted values never decrease with rank, and capped
    at 1.
    """
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * pvals[idx])
        adjusted[idx] = min(1.0, running)
    return adjusted


def mde(
    p: float, n_per_arm: int, m: int = 1, icc: float = 0.3, alpha: float = 0.05, power: float = 0.8
) -> float:
    """Minimum detectable difference in proportion units for a two-arm comparison.

    (z_{1-alpha/2} + z_{power}) * sqrt(2 p (1 - p) DEFF / N), DEFF = 1 + (m - 1) ICC,
    m = answers per intent, N = answers per arm (METRICS.md).

    p is the baseline rate. Denominator: N = n_per_arm, which DEFF shrinks to an effective N / DEFF
    because answers to the same intent are correlated. ICC defaults to 0.3 until measured. Raises ValueError
    unless 0 < p < 1: a zero or full baseline has no detectable-effect size, and the report must say so.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(
            f"mde needs a baseline 0 < p < 1, got p={p}: a zero or full baseline has no detectable-effect"
            " size, so the report must say so instead of showing 0.0"
        )
    if n_per_arm <= 0:
        raise ValueError("n_per_arm must be positive")
    deff = 1 + (m - 1) * icc
    z = _Z.inv_cdf(1 - alpha / 2) + _Z.inv_cdf(power)
    return z * math.sqrt(2 * p * (1 - p) * deff / n_per_arm)


def verdict(
    diff_lo: float,
    diff_hi: float,
    p_adj: float,
    n_intents: int,
    threshold: float = 0.10,
    min_intents: int = 6,
) -> Verdict:
    """METRICS.md change verdict. The CI bounds are differences in proportion units.

    n_intents = shared intents, the n of the paired test. Under min_intents: "insufficient" (no verdict).
    Otherwise "moved" when the Holm-adjusted p is below 0.05, "no_change" when the 95% CI
    [diff_lo, diff_hi] lies inside +/- threshold (10 points by default), else "cant_tell". A zero-width CI
    (diff_lo == diff_hi) that is not "moved" is "cant_tell": it comes from differences with no spread, such
    as every intent at 0 in both windows, so the interval collapsed for want of variation, not from
    certainty, and is no evidence of no change. Raises ValueError when diff_lo > diff_hi.
    """
    if diff_lo > diff_hi:
        raise ValueError(f"verdict needs diff_lo <= diff_hi, got diff_lo={diff_lo} > diff_hi={diff_hi}")
    if n_intents < min_intents:
        return "insufficient"
    if p_adj < 0.05:
        return "moved"
    if diff_lo == diff_hi:
        return "cant_tell"
    if -threshold <= diff_lo and diff_hi <= threshold:
        return "no_change"
    return "cant_tell"


def jaccard(a: set, b: set) -> float | None:
    """Jaccard similarity of two sets, for example the cited canonical URL sets of a replicate pair.

    Numerator: elements in both sets. Denominator: elements in either set. None when both sets are empty
    (never 0).
    """
    union = a | b
    if not union:
        return None
    return len(a & b) / len(union)
