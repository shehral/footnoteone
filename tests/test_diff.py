import pytest
from helpers import OTHER, OWN, build_store, shifted_project

from footnoteone.config import ProjectConfig, load_config, load_intents
from footnoteone.diff import compute_diff, intent_rates, render_diff_markdown
from footnoteone.library import owned_set_for
from footnoteone.metrics import run_views
from footnoteone.plan import intents_needed
from footnoteone.schema import EngineConfig, Intent, Prompt
from footnoteone.stats import holm, paired_sign_flip_p, t_interval

E1 = EngineConfig(provider="openai", model_requested="gpt-5-mini")
CFG = ProjectConfig.model_validate(
    {
        "site": {"url": "https://example.org"},
        "engines": [{"provider": "openai", "model": "gpt-5-mini"}],
        "design": {"paraphrases": 1, "reps": 1, "budget_usd_per_burst": 1.0},
    }
)


def intents(n=10):
    return [Intent(id=f"u{k}", label=f"u{k}", prompts=[Prompt(id=f"u{k}p0", text="q")]) for k in range(n)]


def pattern_before_after(label, intent, prompt, engine, rep):
    k = int(intent.id[1:])
    cites = k < 2 if label == "w1" else k < 6
    return ("ok", "yes", [OWN], [OWN] if cites else [OTHER])


def gains_everywhere(label, intent, prompt, engine, rep):
    return ("ok", "yes", [OWN], [OWN] if label == "w2" else [OTHER])


def no_citations(label, intent, prompt, engine, rep):
    return ("ok", "yes", [OTHER], [OTHER])  # every answer searched; none cites the site


def test_diff_matches_stats_and_reads_cant_tell(tmp_path):
    build_store(tmp_path, ["w1", "w2"], [E1], intents(), reps=1, pattern=pattern_before_after)
    d = compute_diff(tmp_path, CFG, intents(), [E1], pages=[], adapters={}, before=["w1"], after=["w2"])
    [e] = d.engines
    diffs = [1.0] * 4 + [0.0] * 6  # u2..u5 gained a citation; the others are unchanged
    assert e.shared_intents == 10
    assert e.before_rate == pytest.approx(0.2) and e.after_rate == pytest.approx(0.6)
    # one engine: Holm is identity
    assert e.p_raw == pytest.approx(paired_sign_flip_p(diffs)) and e.p_adj == pytest.approx(e.p_raw)
    mean, lo, hi = t_interval(diffs)
    assert (e.mean_diff, e.diff_lo, e.diff_hi) == pytest.approx((mean, lo, hi))
    assert e.verdict == "cant_tell" and e.intents_needed is not None and e.intents_needed > 10
    assert d.verdict_lines[0].startswith("api:openai gpt-5-mini") and "Can't tell yet" in d.verdict_lines[0]
    assert "+40" in d.verdict_lines[0] and "10 shared intents" in d.verdict_lines[0]
    # C7 (M13): the interval clears zero, so the sign-flip p holds the verdict back; the line says why.
    assert lo > 0 and e.p_adj >= 0.05
    assert d.verdict_lines[0] == (
        f"api:openai gpt-5-mini ({E1.config_sha[:8]}): Can't tell yet. Mean change +40 points, 95% interval "
        f"+{round(100 * lo)} to +{round(100 * hi)}, across 10 shared intents; the change is not consistent "
        f"across intents: 4 of 10 moved; about {e.intents_needed} intents would be needed to see a 10-point "
        "move at this design."
    )


def test_diff_cant_tell_gives_no_consistency_reason_when_the_interval_spans_zero(tmp_path):
    def pattern(label, intent, prompt, engine, rep):  # u0 gains a citation and u1 loses one
        cites = (intent.id == "u0" and label == "w2") or (intent.id == "u1" and label == "w1")
        return ("ok", "yes", [OWN], [OWN] if cites else [OTHER])

    build_store(tmp_path, ["w1", "w2"], [E1], intents(), reps=1, pattern=pattern)
    d = compute_diff(tmp_path, CFG, intents(), [E1], pages=[], adapters={}, before=["w1"], after=["w2"])
    assert d.engines[0].verdict == "cant_tell" and d.engines[0].diff_lo < 0 < d.engines[0].diff_hi
    assert "not consistent" not in d.verdict_lines[0]


def test_diff_consistency_reason_counts_moves_both_ways(tmp_path):
    def pattern(label, intent, prompt, engine, rep):  # u0 to u6 gain, u7 loses, u8 and u9 stay
        k = int(intent.id[1:])
        cites = (k < 7 and label == "w2") or (k == 7 and label == "w1")
        return ("ok", "yes", [OWN], [OWN] if cites else [OTHER])

    build_store(tmp_path, ["w1", "w2"], [E1], intents(), reps=1, pattern=pattern)
    d = compute_diff(tmp_path, CFG, intents(), [E1], pages=[], adapters={}, before=["w1"], after=["w2"])
    [e] = d.engines
    assert e.verdict == "cant_tell" and e.diff_lo > 0 and e.p_adj == pytest.approx(18 / 256)
    assert "the change is not consistent across intents: 7 of 10 moved up and 1 down;" in d.verdict_lines[0]


def test_moved_prints_a_holm_adjusted_p_below_one_in_a_thousand_as_such(tmp_path):
    """C6 (Ruling B22): twelve unanimous intents give an exact p of 2 / 4096, which rounds to 0.000."""
    build_store(tmp_path, ["w1", "w2"], [E1], intents(12), reps=1, pattern=gains_everywhere)
    d = compute_diff(tmp_path, CFG, intents(12), [E1], pages=[], adapters={}, before=["w1"], after=["w2"])
    assert d.engines[0].p_adj < 0.0005 and d.verdict_lines[0].endswith("; Holm-adjusted p < 0.001).")
    build_store(tmp_path / "ten", ["w1", "w2"], [E1], intents(10), reps=1, pattern=gains_everywhere)
    ten = compute_diff(
        tmp_path / "ten", CFG, intents(10), [E1], pages=[], adapters={}, before=["w1"], after=["w2"]
    )
    assert ten.verdict_lines[0].endswith("; Holm-adjusted p 0.002).")  # 2 / 1024


def test_the_diff_heading_names_both_windows(tmp_path):
    build_store(tmp_path, ["w1", "w2", "w3"], [E1], intents(), reps=1, pattern=pattern_before_after)
    d = compute_diff(
        tmp_path, CFG, intents(), [E1], pages=[], adapters={}, before=["w1", "w2"], after=["w3"]
    )
    assert render_diff_markdown(d).splitlines()[0] == "# Change between windows (before: w1, w2; after: w3)"


def test_diff_insufficient_below_minimum_and_ignores_unshared_intents(tmp_path):
    def pattern(label, intent, prompt, engine, rep):
        if label == "w1" and intent.id == "u6":
            return None  # u6 did not exist in the first window
        return pattern_before_after(label, intent, prompt, engine, rep)

    build_store(tmp_path, ["w1", "w2"], [E1], intents(7), reps=1, pattern=pattern)
    d = compute_diff(tmp_path, CFG, intents(7), [E1], pages=[], adapters={}, before=["w1"], after=["w2"])
    [e] = d.engines
    assert e.shared_intents == 6 and e.verdict == "insufficient"
    assert "Not enough shared intents (6 of 8)" in d.verdict_lines[0]


def test_diff_moved_when_every_intent_gains(tmp_path):
    def pattern(label, intent, prompt, engine, rep):
        return ("ok", "yes", [OWN], [OWN] if label == "w2" else [OTHER])

    build_store(tmp_path, ["w1", "w2"], [E1], intents(10), reps=1, pattern=pattern)
    d = compute_diff(tmp_path, CFG, intents(10), [E1], pages=[], adapters={}, before=["w1"], after=["w2"])
    [e] = d.engines
    assert e.p_raw == pytest.approx(2 / 1024) and e.verdict == "moved" and "Moved" in d.verdict_lines[0]
    assert "\u2014" not in render_diff_markdown(d)


def test_diff_no_change_when_rates_are_stable_with_spread(tmp_path):
    def pattern(label, intent, prompt, engine, rep):
        k = int(intent.id[1:])
        # each intent cites in exactly one of two reps, both windows
        cites = (k % 2 == 0) if rep == 0 else (k % 2 == 1)
        return ("ok", "yes", [OWN], [OWN] if cites else [OTHER])

    cfg = CFG.model_copy(update={"design": CFG.design.model_copy(update={"reps": 2})})
    build_store(tmp_path, ["w1", "w2"], [E1], intents(10), reps=2, pattern=pattern)
    d = compute_diff(tmp_path, cfg, intents(10), [E1], pages=[], adapters={}, before=["w1"], after=["w2"])
    [e] = d.engines
    assert e.mean_diff == pytest.approx(0.0) and e.diff_lo == e.diff_hi == 0.0 and e.verdict == "cant_tell"
    assert "no width" in d.verdict_lines[0]


def test_diff_cant_tell_without_width_names_the_intents_needed(tmp_path):
    # The common creator case: no engine cited the site in either window, so every difference is 0 and the
    # interval has no width. METRICS shows the intents needed with every Can't tell yet verdict.
    build_store(tmp_path, ["w1", "w2"], [E1], intents(), reps=1, pattern=no_citations)
    d = compute_diff(tmp_path, CFG, intents(), [E1], pages=[], adapters={}, before=["w1"], after=["w2"])
    [e] = d.engines
    assert e.diff_lo == e.diff_hi == 0.0 and e.verdict == "cant_tell"
    assert e.intents_needed == intents_needed(0.0, 1.0, CFG.design.icc, 1, min_intents=8)
    line = (
        f"api:openai gpt-5-mini ({E1.config_sha[:8]}): Can't tell yet. The interval has no width (every "
        "shared intent changed by the same amount, +0 points) across 10 shared intents, which is no evidence "
        f"of no change; about {e.intents_needed} intents would be needed to see a 10-point move at this "
        "design."
    )
    assert d.verdict_lines == [line] and line in render_diff_markdown(d)


def test_diff_cant_tell_without_an_interval_names_the_intents_needed(tmp_path):
    # Validation keeps min_shared_intents at 8 or more, so a family member always has an interval. model_copy
    # skips validation, which is the only way to reach the defensive branch for fewer than 2 differences.
    cfg = CFG.model_copy(update={"design": CFG.design.model_copy(update={"min_shared_intents": 1})})
    build_store(tmp_path, ["w1", "w2"], [E1], intents(1), reps=1, pattern=gains_everywhere)
    d = compute_diff(tmp_path, cfg, intents(1), [E1], pages=[], adapters={}, before=["w1"], after=["w2"])
    [e] = d.engines
    assert e.shared_intents == 1 and e.diff_lo is None and e.verdict == "cant_tell"
    assert e.intents_needed == intents_needed(0.0, 1.0, CFG.design.icc, 1, min_intents=1)
    assert d.verdict_lines[0].endswith(
        ": Can't tell yet. Fewer than 2 shared intents have spread; cited rate 0% before and 100% after "
        f"across 1 shared intents; about {e.intents_needed} intents would be needed to see a 10-point move "
        "at this design."
    )


@pytest.mark.parametrize("pattern", [pattern_before_after, no_citations])
def test_diff_cant_tell_says_out_of_reach_past_the_cap(tmp_path, monkeypatch, pattern):
    # plan.intents_needed gives None when no size up to its cap of 1000 intents reaches a 10-point move.
    monkeypatch.setattr("footnoteone.diff.intents_needed", lambda *args, **kwargs: None)
    build_store(tmp_path, ["w1", "w2"], [E1], intents(), reps=1, pattern=pattern)
    d = compute_diff(tmp_path, CFG, intents(), [E1], pages=[], adapters={}, before=["w1"], after=["w2"])
    assert d.engines[0].verdict == "cant_tell" and d.engines[0].intents_needed is None
    assert d.verdict_lines[0].endswith("; a 10-point move is out of reach below 1000 intents at this design.")


def test_intent_rates_counts_activated_ok_runs_of_unbranded_intents_only(tmp_path):
    mixed = intents(2) + [
        Intent(id="b0", label="b0", kind="branded", prompts=[Prompt(id="b0p0", text="q")]),
        Intent(id="p0", label="p0", kind="placebo", prompts=[Prompt(id="p0p0", text="q")]),
    ]

    def pattern(label, intent, prompt, engine, rep):
        if intent.id == "u1":  # no activated ok run at all, so absent rather than 0
            return ("ok", "no", [], []) if rep == 0 else ("error", "unknown", [], [])
        if rep == 2:  # answered without searching: outside the denominator
            return ("ok", "no", [], [])
        return ("ok", "yes", [OWN], [OWN] if rep == 0 else [OTHER])

    manifests = build_store(tmp_path, ["w1"], [E1], mixed, reps=3, pattern=pattern)
    views = run_views(tmp_path, {}, manifests, owned_set_for(CFG, []))
    assert intent_rates(views) == {"u0": 0.5}  # branded b0 and placebo p0 never count


def test_diff_shares_only_intents_activated_in_both_windows_and_holm_skips_short_engines(tmp_path):
    short = EngineConfig(provider="anthropic", model_requested="claude-sonnet-4-5")

    def pattern(label, intent, prompt, engine, rep):
        if engine.provider == "anthropic":
            if label == "w1" and intent.id in ("u6", "u7"):
                return ("ok", "no", [], [])  # answered without searching in the first window
            if label == "w1" and intent.id == "u8":
                return ("error", "unknown", [], [])
            if label == "w2" and intent.id == "u9":
                return None  # not asked in the second window
        return gains_everywhere(label, intent, prompt, engine, rep)

    build_store(tmp_path, ["w1", "w2"], [E1, short], intents(), reps=1, pattern=pattern)
    engines = [E1, short]
    d = compute_diff(tmp_path, CFG, intents(), engines, pages=[], adapters={}, before=["w1"], after=["w2"])
    full, cut = d.engines
    assert [intent_id for intent_id, _, _ in cut.per_intent] == ["u0", "u1", "u2", "u3", "u4", "u5"]
    assert cut.shared_intents == 6 and cut.verdict == "insufficient" and cut.p_adj is None
    assert d.verdict_lines[1].endswith(": Not enough shared intents (6 of 8); no verdict.")
    # The short engine is outside the Holm family, so the full engine's p is not doubled.
    assert full.shared_intents == 10 and full.p_raw == pytest.approx(2 / 1024)
    assert full.p_adj == pytest.approx(full.p_raw) and full.verdict == "moved"
    assert "family of 1 engine" in full.note


def test_diff_names_engines_by_config_sha_and_holm_adjusts_across_the_family(tmp_path):
    forced = EngineConfig(provider="openai", model_requested="gpt-5-mini", params={"force_search": True})

    def pattern(label, intent, prompt, engine, rep):
        if engine.params:  # the forced engine gains on u2..u5 only
            return pattern_before_after(label, intent, prompt, engine, rep)
        return gains_everywhere(label, intent, prompt, engine, rep)

    build_store(tmp_path, ["w1", "w2"], [E1, forced], intents(), reps=1, pattern=pattern)
    engines = [E1, forced]
    d = compute_diff(tmp_path, CFG, intents(), engines, pages=[], adapters={}, before=["w1"], after=["w2"])
    plain, alt = d.engines
    assert [plain.p_adj, alt.p_adj] == pytest.approx(holm([plain.p_raw, alt.p_raw]))
    assert plain.verdict == "moved" and alt.verdict == "cant_tell"
    assert alt.intents_needed == intents_needed(0.2, 1.0, CFG.design.icc, 2, min_intents=8)
    # Same surface and model: only the config sha tells the two engines apart.
    assert d.verdict_lines[0].startswith(f"api:openai gpt-5-mini ({E1.config_sha[:8]}): Moved.")
    assert d.verdict_lines[1].startswith(f"api:openai gpt-5-mini ({forced.config_sha[:8]}): Can't tell yet.")
    assert f"## api:openai gpt-5-mini ({forced.config_sha[:8]})" in render_diff_markdown(d)


def test_diff_no_change_when_the_interval_lies_inside_ten_points(tmp_path):
    def pattern(label, intent, prompt, engine, rep):
        k = int(intent.id[1:])
        # every intent cites in one of its two reps, except u0 cites in both after and u1 in both before
        cites = rep == 0 or (k == 0 and label == "w2") or (k == 1 and label == "w1")
        return ("ok", "yes", [OWN], [OWN] if cites else [OTHER])

    cfg = CFG.model_copy(update={"design": CFG.design.model_copy(update={"reps": 2})})
    build_store(tmp_path, ["w1", "w2"], [E1], intents(30), reps=2, pattern=pattern)
    d = compute_diff(tmp_path, cfg, intents(30), [E1], pages=[], adapters={}, before=["w1"], after=["w2"])
    [e] = d.engines
    mean, lo, hi = t_interval([0.5, -0.5] + [0.0] * 28)
    assert (e.mean_diff, e.diff_lo, e.diff_hi) == pytest.approx((mean, lo, hi))
    assert -0.1 < e.diff_lo < e.diff_hi < 0.1 and e.verdict == "no_change" and e.intents_needed is None
    assert d.verdict_lines[0].endswith(
        ": No change. The 95% interval of the change, -5 to +5 points, lies inside plus or minus 10 points "
        "across 30 shared intents."
    )


def test_diff_reads_without_creating_the_store(tmp_path):
    d = compute_diff(tmp_path, CFG, intents(), [E1], pages=[], adapters={}, before=["w1"], after=["w2"])
    assert not (tmp_path / ".footnote").exists()
    assert d.verdict_lines == [
        f"api:openai gpt-5-mini ({E1.config_sha[:8]}): Not enough shared intents (0 of 8); no verdict."
    ]


def test_diff_window_without_labels_selects_no_burst(tmp_path):
    build_store(tmp_path, ["w1", "w2"], [E1], intents(), reps=1, pattern=pattern_before_after)
    d = compute_diff(tmp_path, CFG, intents(), [E1], pages=[], adapters={}, before=[], after=["w2"])
    [e] = d.engines
    assert e.shared_intents == 0 and e.verdict == "insufficient"


def test_a_window_that_ran_the_model_under_other_params_says_so_in_the_verdict(tmp_path):
    """C2: w1 ran each engine with an output limit of 1000 and w2 with today's 1200, so no config has runs
    in both windows. Each verdict line says the before window ran the model under another config and names
    the first param that differs."""
    old, current = shifted_project(tmp_path)
    config, intents_ = load_config(tmp_path), load_intents(tmp_path)
    d = compute_diff(tmp_path, config, intents_, current, pages=[], adapters={}, before=["w1"], after=["w2"])
    assert d.verdict_lines[0] == (
        f"api:openai gpt-5-mini ({current[0].config_sha[:8]}): Not enough shared intents (0 of 8); no "
        f"verdict. The before window ran this model as config {old[0].config_sha[:8]}, with "
        "max_output_tokens 1000 instead of 1200."
    )
    anthropic = f"as config {old[1].config_sha[:8]}, with max_tokens 1000 instead of 1200."
    assert d.verdict_lines[1].endswith(anthropic)
    same = compute_diff(
        tmp_path, config, intents_, current, pages=[], adapters={}, before=["w2"], after=["w2"]
    )
    assert "ran this model as config" not in " ".join(same.verdict_lines)  # both windows ran these configs
