"""Cost and power planning: what a burst and a month of bursts cost, and what a burst could ever detect."""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from footnoteone.config import ProjectConfig
from footnoteone.design import headline_intents
from footnoteone.planning import PlanningAssumptions, typical_usd, worst_case_usd
from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig, Intent
from footnoteone.stats import mde

BASELINE_FLOOR, BASELINE_CAP, TARGET_POINTS = 0.05, 0.95, 0.10


def planning_baseline(p: float) -> tuple[float, bool]:
    """The baseline used for power maths: p floored at 0.05 and capped at 0.95; the flag says it is a
    prior."""
    used = min(max(p, BASELINE_FLOOR), BASELINE_CAP)
    return used, used != p


def moved_reachable_at(n_engines: int, min_intents: int = 8) -> int:
    """Smallest number of shared intents at which Moved is reachable: the most extreme sign-flip p is
    2 / 2**k, and Holm across n engines multiplies it by n, so n x 2 / 2**k must be below 0.05."""
    k = max(1, min_intents)
    while max(1, n_engines) * 2 / 2**k >= 0.05:
        k += 1
    return k


def answers_per_intent(intents: list[Intent], reps: int, paraphrases: int) -> float:
    """m of the power maths: the answers one engine gives each headline intent in one burst.

    Counted from the wordings each unbranded intent has, because the runner asks every wording
    (design.enumerate_calls), not from design.paraphrases. When the counts differ it is the harmonic mean of
    wordings x reps over those intents: the METRICS rate is an unweighted mean of per-intent rates, whose
    variance p (1 - p) / k x (icc + (1 - icc) x mean(1 / m_i)) equals p (1 - p) DEFF / N with N = k x m
    exactly at that m (the arithmetic mean would overstate the power). With no unbranded intent that has a
    wording it is paraphrases x reps, the answers an intent written to the design would get. Use it wherever
    a power figure needs m, so the plan and a Can't tell yet verdict agree.
    """
    counts = [len(i.prompts) * reps for i in headline_intents(intents) if i.prompts]
    if not counts:
        return float(paraphrases * reps)
    return float(len(counts) / sum(Fraction(1, c) for c in counts))  # exact: equal counts give c itself


def intents_needed(
    p: float,
    m: float,
    icc: float,
    n_engines: int,
    target: float = TARGET_POINTS,
    min_intents: int = 8,
    cap: int = 1000,
) -> int | None:
    """Smallest k >= moved_reachable_at with mde(p, N = k x m, m, icc) <= target; None past cap.

    m is answers_per_intent, so it need not be a whole number.
    """
    p_used, _ = planning_baseline(p)
    for k in range(moved_reachable_at(n_engines, min_intents), cap + 1):
        if mde(p_used, k * m, m, icc) <= target:
            return k
    return None


@dataclass
class EnginePlan:
    engine: EngineConfig
    surface: str
    priced: bool
    calls_per_burst: int
    typical_call_usd: float
    worst_call_usd: float
    typical_burst_usd: float
    worst_burst_usd: float
    baseline_used: float
    baseline_is_prior: bool
    mde_points: float | None  # percentage points, for the headline intents of one burst against another


@dataclass
class PlanSummary:
    engines: list[EnginePlan]
    intents_total: int
    intents_unbranded: int
    intents_branded: int
    intents_placebo: int
    paraphrases: int
    reps: int
    calls_per_burst_total: int
    typical_total_usd: float
    worst_total_usd: float
    budget_usd: float
    fits_typical: bool
    fits_worst: bool
    min_shared_intents: int
    moved_reachable_at: int
    intents_needed_for_mde: int | None
    price_table_version: str
    planning_version: str
    wordings_total: int  # every wording of every intent; calls_per_burst_total = this x reps x engines
    answers_per_intent: float  # m of the detectable move and intents needed (answers_per_intent)
    bursts_per_month: int
    typical_month_usd: float
    worst_month_usd: float
    baseline_used: float  # the baseline cited rate the power maths used (planning_baseline)
    baseline_is_prior: bool
    warnings: list[str] = field(default_factory=list)


def make_plan(
    config: ProjectConfig,
    intents: list[Intent],
    engines: list[EngineConfig],
    table: PriceTable,
    assumptions: PlanningAssumptions,
) -> PlanSummary:
    """Cost of one burst per engine and in total, and of a month at design.bursts_per_month bursts; the
    detectable move of one burst against another. Calls, costs and power all count the wordings the
    intents have: the runner asks every one of them."""
    d = config.design
    headline = headline_intents(intents)
    m = answers_per_intent(intents, d.reps, d.paraphrases)
    n_headline = sum(1 for i in headline if i.prompts) * m  # N per engine: k x m, intents with a wording
    p_used, is_prior = planning_baseline(d.baseline_cited_rate)
    plans: list[EnginePlan] = []
    warnings: list[str] = []
    wordings_total = sum(len(i.prompts) for i in intents)
    calls_per_engine = wordings_total * d.reps
    for e in engines:
        priced = table.is_known(e.provider, e.model_requested)
        typical, worst = typical_usd(e, table, assumptions), worst_case_usd(e, table, assumptions)
        points = 100 * mde(p_used, n_headline, m, d.icc) if n_headline else None
        plans.append(
            EnginePlan(
                e,
                e.surface,
                priced,
                calls_per_engine,
                typical,
                worst,
                calls_per_engine * typical,
                calls_per_engine * worst,
                p_used,
                is_prior,
                points,
            )
        )
        if not priced:
            warnings.append(
                f"{e.surface} {e.model_requested}: not priced in pricing.yaml {table.version}; "
                "`footnote run` refuses unpriced engines"
            )
    typical_total = sum(x.typical_burst_usd for x in plans)
    worst_total = sum(x.worst_burst_usd for x in plans)
    if worst_total > d.budget_usd_per_burst:
        warnings.append(
            f"budget {d.budget_usd_per_burst:.2f} USD is below the worst case {worst_total:.2f} USD; "
            "a run records budget_skip once the reserve is spent"
        )
    if not headline:
        warnings.append("no unbranded intents: nothing can enter a headline")
    elif len(headline) < d.min_shared_intents:
        have = f"{len(headline)} unbranded intent" + ("" if len(headline) == 1 else "s")
        warnings.append(
            f"{have}; no verdict is possible below {d.min_shared_intents} unbranded intents "
            "(min_shared_intents in footnote.toml)"
        )
    off = [i.id for i in headline if len(i.prompts) != d.paraphrases]
    if off:
        shown = ", ".join(off[:5]) + (f" and {len(off) - 5} more" if len(off) > 5 else "")
        warnings.append(
            f"{len(off)} of {len(headline)} unbranded intents ({shown}) have a number of wordings other "
            f"than paraphrases = {d.paraphrases} in footnote.toml; every wording in intents.yaml is asked, "
            "so the calls, costs, detectable move and intents needed count those wordings"
        )
    if not any(i.kind == "placebo" for i in intents):
        warnings.append("no placebo intent: the placebo floor cannot be measured")
    if is_prior:
        warnings.append(
            f"baseline cited rate {d.baseline_cited_rate} replaced by the planning prior {p_used}"
        )
    return PlanSummary(
        engines=plans,
        intents_total=len(intents),
        intents_unbranded=len(headline),
        intents_branded=sum(i.kind == "branded" for i in intents),
        intents_placebo=sum(i.kind == "placebo" for i in intents),
        paraphrases=d.paraphrases,
        reps=d.reps,
        calls_per_burst_total=calls_per_engine * len(engines),
        typical_total_usd=typical_total,
        worst_total_usd=worst_total,
        budget_usd=d.budget_usd_per_burst,
        fits_typical=typical_total <= d.budget_usd_per_burst,
        fits_worst=worst_total <= d.budget_usd_per_burst,
        min_shared_intents=d.min_shared_intents,
        moved_reachable_at=moved_reachable_at(len(engines), d.min_shared_intents),
        intents_needed_for_mde=intents_needed(
            d.baseline_cited_rate, m, d.icc, len(engines), min_intents=d.min_shared_intents
        ),
        price_table_version=table.version,
        planning_version=assumptions.version,
        wordings_total=wordings_total,
        answers_per_intent=m,
        bursts_per_month=d.bursts_per_month,
        typical_month_usd=typical_total * d.bursts_per_month,
        worst_month_usd=worst_total * d.bursts_per_month,
        baseline_used=p_used,
        baseline_is_prior=is_prior,
        warnings=warnings,
    )


def _points(mde_points: float | None) -> str:
    """The detectable move as the plan prints it: whole points, or what a move past 100 points means."""
    if mde_points is None:
        return "no data"
    if mde_points > 100:  # an MDE above 1: no possible change of a rate could be detected
        return "more than 100 points: more intents needed"
    return f"{mde_points:.0f}"


def _fit(summary: PlanSummary) -> str:
    if summary.fits_worst:
        return "fits the typical cost and the worst case"
    if summary.fits_typical:
        return "fits the typical cost but not the worst case"
    return "fits neither the typical cost nor the worst case"


def render_plan_markdown(summary: PlanSummary) -> str:
    lines = [
        "# Plan for one burst",
        "",
        f"{summary.intents_total} intents ({summary.intents_unbranded} unbranded, "
        f"{summary.intents_branded} branded, {summary.intents_placebo} placebo), {summary.wordings_total} "
        f"wordings in all; {summary.wordings_total} wordings x {summary.reps} repeats x "
        f"{len(summary.engines)} engines = {summary.calls_per_burst_total} calls.",
        "",
    ]
    if summary.engines:
        lines += [
            "| Engine | Model | Calls | Typical cost | Worst case | Detectable move (points) |",
            "|---|---|---|---|---|---|",
        ]
    else:
        lines.append("No engine is configured.")
    for e in summary.engines:
        priced = "" if e.priced else " (not priced)"
        lines.append(
            f"| {e.surface} | {e.engine.model_requested}{priced} | {e.calls_per_burst} | "
            f"{e.typical_burst_usd:.2f} USD | {e.worst_burst_usd:.2f} USD | {_points(e.mde_points)} |"
        )
    bursts = f"{summary.bursts_per_month} burst" + ("" if summary.bursts_per_month == 1 else "s")
    lines += [
        "",
        f"Per burst: typical {summary.typical_total_usd:.2f} USD; worst case "
        f"{summary.worst_total_usd:.2f} USD; budget {summary.budget_usd:.2f} USD ({_fit(summary)}).",
        f"Per month at {bursts} (bursts_per_month in footnote.toml): typical "
        f"{summary.typical_month_usd:.2f} USD; worst case {summary.worst_month_usd:.2f} USD.",
        f"Baseline cited rate used {summary.baseline_used:.2f}"
        + (" (planning prior)" if summary.baseline_is_prior else "")
        + "; detectable move = minimum detectable effect at 80% power across the "
        f"{summary.intents_unbranded} unbranded intents.",
        f"A Moved verdict needs at least {summary.moved_reachable_at} shared intents (Holm across "
        f"{len(summary.engines)} engines); "
        + (
            "a 10-point move needs about " + str(summary.intents_needed_for_mde) + " intents at this design."
            if summary.intents_needed_for_mde
            else "a 10-point move is out of reach below 1000 intents at this design."
        ),
        f"Prices: pricing.yaml {summary.price_table_version}; assumptions: planning.yaml "
        f"{summary.planning_version}. API answers, not the consumer apps.",
    ]
    if summary.warnings:
        lines += ["", "Warnings:"] + [f"- {w}" for w in summary.warnings]
    return "\n".join(lines) + "\n"
