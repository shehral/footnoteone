import math
import re
from statistics import NormalDist

import pytest

from footnoteone.config import ProjectConfig, engine_configs, load_config, load_intents, write_templates
from footnoteone.plan import (
    answers_per_intent,
    intents_needed,
    make_plan,
    moved_reachable_at,
    planning_baseline,
    render_plan_markdown,
)
from footnoteone.planning import PlanningAssumptions, worst_case_usd
from footnoteone.pricing import PriceTable
from footnoteone.schema import Intent, Prompt
from footnoteone.stats import mde


def project(budget=20.0, engines=None, **design):
    engines = engines or [
        {"provider": "openai", "model": "gpt-5-mini"},
        {"provider": "anthropic", "model": "claude-sonnet-4-5"},
    ]
    return ProjectConfig.model_validate(
        {
            "site": {"url": "https://example.org"},
            "engines": engines,
            "design": {"paraphrases": 3, "reps": 2, "budget_usd_per_burst": budget, **design},
        }
    )


def intents(unbranded=8, branded=1, placebo=1):
    def mk(prefix, n, kind):
        return [
            Intent(
                id=f"{prefix}{k}",
                label=prefix,
                kind=kind,
                prompts=[Prompt(id=f"{prefix}{k}p{j}", text="q", paraphrase_idx=j) for j in range(3)],
            )
            for k in range(n)
        ]

    return mk("u", unbranded, "unbranded") + mk("b", branded, "branded") + mk("p", placebo, "placebo")


def unbranded_with(wordings):
    """One unbranded intent per entry, with that many wordings."""
    return [
        Intent(
            id=f"w{k}",
            label="w",
            prompts=[Prompt(id=f"w{k}p{j}", text="q", paraphrase_idx=j) for j in range(n)],
        )
        for k, n in enumerate(wordings)
    ]


def test_planning_baseline_floors_and_caps():
    assert planning_baseline(0.2) == (0.2, False)
    assert planning_baseline(0.0) == (0.05, True)
    assert planning_baseline(1.0) == (0.95, True)


def test_moved_reachable_at_respects_holm_and_floor():
    assert (
        moved_reachable_at(1) == 8
        and moved_reachable_at(3) == 8
        and moved_reachable_at(3, min_intents=6) == 7
    )
    # 20 x 2 / 512 = 0.078 at 9 intents, 20 x 2 / 1024 = 0.039 at 10 (adjusted p must be below 0.05;
    # METRICS 0.2.0: Moved is reachable when engines x 2 / 2^k < 0.05)
    assert moved_reachable_at(20) == 10


def test_intents_needed_matches_mde_inversion():
    assert intents_needed(0.2, m=6, icc=0.3, n_engines=3) == 105
    assert mde(0.2, 105 * 6, 6, 0.3) <= 0.10 < mde(0.2, 104 * 6, 6, 0.3)
    assert intents_needed(0.5, m=6, icc=0.3, n_engines=1, cap=50) is None


def test_make_plan_counts_costs_and_power():
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    engines = engine_configs(cfg, a)
    summary = make_plan(cfg, intents(), engines, table, a)
    assert (
        summary.intents_total,
        summary.intents_unbranded,
        summary.intents_branded,
        summary.intents_placebo,
    ) == (10, 8, 1, 1)
    assert summary.paraphrases == 3 and summary.reps == 2 and summary.calls_per_burst_total == 10 * 3 * 2 * 2
    e = summary.engines[0]
    assert e.calls_per_burst == 60 and e.worst_burst_usd == pytest.approx(
        60 * worst_case_usd(engines[0], table, a)
    )
    assert e.mde_points == pytest.approx(100 * mde(0.2, 8 * 6, 6, 0.3)) and e.baseline_is_prior is False
    assert summary.moved_reachable_at == 8 and summary.intents_needed_for_mde == intents_needed(
        0.2, 6, 0.3, 2
    )
    assert summary.worst_total_usd == pytest.approx(sum(x.worst_burst_usd for x in summary.engines))
    assert summary.fits_worst == (summary.worst_total_usd <= 20.0) and summary.min_shared_intents == 8
    assert summary.warnings == [] or all(isinstance(w, str) for w in summary.warnings)


def test_make_plan_warns():
    cfg, table, a = (
        project(budget=0.01, engines=[{"provider": "openai", "model": "gpt-999"}]),
        PriceTable.load(),
        PlanningAssumptions.load(),
    )
    summary = make_plan(cfg, intents(unbranded=3, placebo=0), engine_configs(cfg, a), table, a)
    text = "\n".join(summary.warnings)
    assert "not priced" in text and "budget" in text and "placebo" in text and "8 unbranded" in text
    assert summary.engines[0].priced is False


def test_render_plan_markdown_has_plain_words_and_no_dashes():
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    md = render_plan_markdown(make_plan(cfg, intents(), engine_configs(cfg, a), table, a))
    assert "worst case" in md and "api:openai" in md and "intents" in md
    assert "\u2014" not in md and "\u2013" not in md


def test_power_counts_the_wordings_the_intents_have():
    # Eight unbranded intents with one wording each under paraphrases = 3 and reps = 2. The runner asks every
    # wording, so each intent gets m = 1 x 2 = 2 answers per engine and N = 16, not 3 x 2 = 6 and N = 48.
    # DEFF = 1 + (2 - 1) x 0.3 = 1.3; mde = 2.8016 x sqrt(2 x 0.2 x 0.8 x 1.3 / 16) = 0.452, so 45 points.
    # 10 points needs N >= 2.8016^2 x 0.416 / 0.01 = 326.5, so k >= 163.3: 164 intents. m = 6 would give
    # 36 points and 105 intents.
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    summary = make_plan(cfg, unbranded_with([1] * 8), engine_configs(cfg, a), table, a)
    e = summary.engines[0]
    assert e.mde_points == pytest.approx(100 * mde(0.2, 16, 2, 0.3)) and round(e.mde_points) == 45
    assert summary.intents_needed_for_mde == intents_needed(0.2, 2, 0.3, 2) == 164
    assert summary.answers_per_intent == 2.0 and summary.wordings_total == 8 and e.calls_per_burst == 16
    text = "\n".join(summary.warnings)
    assert "8 of 8 unbranded intents" in text and "paraphrases = 3" in text


def test_header_multiplies_out_when_intents_have_different_numbers_of_wordings():
    placebo = Intent(id="p0", label="p", kind="placebo", prompts=[Prompt(id="p0p0", text="q")])
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    summary = make_plan(cfg, unbranded_with([3]) + [placebo], engine_configs(cfg, a), table, a)
    md = render_plan_markdown(summary)
    assert (
        "2 intents (1 unbranded, 0 branded, 1 placebo), 4 wordings in all; "
        "4 wordings x 2 repeats x 2 engines = 16 calls." in md
    )


def test_init_template_plan_header_multiplies_out(tmp_path):
    write_templates(tmp_path, "https://example.org")
    cfg, intents_, a = load_config(tmp_path), load_intents(tmp_path), PlanningAssumptions.load()
    md = render_plan_markdown(make_plan(cfg, intents_, engine_configs(cfg, a), PriceTable.load(), a))
    found = re.search(r"(\d+) wordings x (\d+) repeats x (\d+) engines = (\d+) calls", md)
    assert found is not None, md
    w, r, e, c = map(int, found.groups())
    assert w == sum(len(i.prompts) for i in intents_) and w * r * e == c


def test_plan_prices_a_month_of_bursts():
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    summary = make_plan(cfg, intents(), engine_configs(cfg, a), table, a)
    assert summary.bursts_per_month == 4  # the default, about weekly
    assert summary.typical_month_usd == pytest.approx(4 * summary.typical_total_usd)
    assert summary.worst_month_usd == pytest.approx(4 * summary.worst_total_usd)
    daily = project(bursts_per_month=30)
    s30 = make_plan(daily, intents(), engine_configs(daily, a), table, a)
    assert s30.typical_month_usd == pytest.approx(30 * s30.typical_total_usd)
    assert s30.worst_month_usd == pytest.approx(30 * s30.worst_total_usd)
    md = render_plan_markdown(s30)
    assert "Per burst: typical" in md and "Per month at 30 bursts" in md
    assert f"worst case {s30.worst_month_usd:.2f} USD" in md


def test_unequal_wordings_use_the_harmonic_mean_of_answers():
    # Answers per intent 2, 2, 2, 2, 6, 6, 6, 6 (one or three wordings, two repeats). The library rate is an
    # unweighted mean of per-intent rates; under an exchangeable icc its variance is
    # p (1 - p) / k x (icc + (1 - icc) x mean(1 / m_i)), which the METRICS form p (1 - p) DEFF / N gives
    # exactly with N = k x m and m the harmonic mean of the m_i (3 here), not their arithmetic mean (4).
    wordings = [1, 1, 1, 1, 3, 3, 3, 3]
    assert answers_per_intent(unbranded_with(wordings), reps=2, paraphrases=3) == 3.0
    p, icc, k = 0.2, 0.3, len(wordings)
    z = NormalDist().inv_cdf(0.975) + NormalDist().inv_cdf(0.80)
    inverse_mean = sum(1 / (2 * w) for w in wordings) / k
    direct = z * math.sqrt(2 * p * (1 - p) / k * (icc + (1 - icc) * inverse_mean))
    assert mde(p, k * 3.0, 3.0, icc) == pytest.approx(direct)
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    summary = make_plan(cfg, unbranded_with(wordings), engine_configs(cfg, a), table, a)
    assert summary.answers_per_intent == 3.0 and summary.engines[0].mde_points == pytest.approx(100 * direct)
    assert summary.intents_needed_for_mde == intents_needed(p, 3.0, icc, 2)
    assert "4 of 8 unbranded intents (w0, w1, w2, w3)" in "\n".join(summary.warnings)


def test_answers_per_intent_counts_unbranded_wordings_and_falls_back_to_the_design():
    mixed = intents()  # 8 unbranded, 1 branded and 1 placebo intent, three wordings each
    # The wordings asked, not paraphrases x reps (1 x 2).
    assert answers_per_intent(mixed, reps=2, paraphrases=1) == 6.0
    # No headline intent: the design's figure, paraphrases x reps.
    controls = [i for i in mixed if i.kind != "unbranded"]
    assert answers_per_intent(controls, reps=2, paraphrases=2) == 4.0
    # An intent with no wording gets no answer and drops out of the mean.
    silent = Intent(id="s", label="s", prompts=[])
    assert answers_per_intent(unbranded_with([3]) + [silent], reps=1, paraphrases=1) == 3.0


def test_wording_mismatch_warning_lists_at_most_five_intents():
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    summary = make_plan(cfg, unbranded_with([2] * 7 + [3]), engine_configs(cfg, a), table, a)
    text = "\n".join(summary.warnings)
    assert "7 of 8 unbranded intents (w0, w1, w2, w3, w4 and 2 more)" in text
    matching = make_plan(cfg, intents(), engine_configs(cfg, a), table, a)
    assert not any("paraphrases" in w for w in matching.warnings)


# F3: the plan renders with no engine, warns at the configured minimum, says when a move past 100 points is
# all a design could detect, says whether the budget covers the typical cost, and counts one burst as one.


def test_the_plan_renders_without_an_engine():
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    md = render_plan_markdown(make_plan(cfg, intents(), [], table, a))
    assert "No engine is configured." in md and "Baseline cited rate used 0.20" in md


def test_the_unbranded_warning_names_the_configured_minimum():
    cfg, table, a = project(min_shared_intents=12), PriceTable.load(), PlanningAssumptions.load()
    text = "\n".join(make_plan(cfg, intents(unbranded=10), engine_configs(cfg, a), table, a).warnings)
    assert "10 unbranded intents; no verdict is possible below 12 unbranded intents" in text
    assert "at least 8" not in text


def test_a_detectable_move_past_100_points_says_more_intents_are_needed():
    cfg, table, a = project(reps=1), PriceTable.load(), PlanningAssumptions.load()
    summary = make_plan(cfg, unbranded_with([1]), engine_configs(cfg, a), table, a)
    assert summary.engines[0].mde_points > 100
    md = render_plan_markdown(summary)
    assert "| more than 100 points: more intents needed |" in md and "| 102 |" not in md


@pytest.mark.parametrize(
    ("budget_of", "words"),
    [
        (lambda typical, worst: worst + 1, "fits the typical cost and the worst case"),
        (lambda typical, worst: (typical + worst) / 2, "fits the typical cost but not the worst case"),
        (lambda typical, worst: typical / 2, "fits neither the typical cost nor the worst case"),
    ],
    ids=["both", "typical-only", "neither"],
)
def test_the_plan_says_whether_the_budget_covers_the_typical_cost(budget_of, words):
    table, a = PriceTable.load(), PlanningAssumptions.load()
    base = make_plan(project(), intents(), engine_configs(project(), a), table, a)
    cfg = project(budget=round(budget_of(base.typical_total_usd, base.worst_total_usd), 2))
    md = render_plan_markdown(make_plan(cfg, intents(), engine_configs(cfg, a), table, a))
    assert f"({words})." in md


def test_one_burst_a_month_is_singular():
    cfg, table, a = project(bursts_per_month=1), PriceTable.load(), PlanningAssumptions.load()
    md = render_plan_markdown(make_plan(cfg, intents(), engine_configs(cfg, a), table, a))
    assert "Per month at 1 burst (bursts_per_month in footnote.toml)" in md
