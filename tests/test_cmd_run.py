from datetime import UTC, datetime

import pytest
import typer
from test_runner import FakeAdapter
from typer.testing import CliRunner

from footnoteone.commands import run as run_cmd
from footnoteone.config import engine_configs, load_config, load_intents, write_templates
from footnoteone.design import enumerate_calls
from footnoteone.plan import make_plan
from footnoteone.planning import PlanningAssumptions, worst_case_usd
from footnoteone.pricing import PriceTable
from footnoteone.schema import Manifest, Run
from footnoteone.store import JsonlStore


def app_with_command():
    app = typer.Typer()
    run_cmd.register(app)
    return app


def test_dry_run_needs_no_keys(tmp_path, monkeypatch):
    write_templates(tmp_path, "https://example.org")
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PERPLEXITY_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert (
        "calls" in result.output
        and "worst case" in result.output
        and not (tmp_path / ".footnote" / "manifests.jsonl").exists()
    )


def test_missing_key_exits_2_with_variable_name(tmp_path, monkeypatch):
    write_templates(tmp_path, "https://example.org")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--label", "b1"])
    assert result.exit_code == 2 and "OPENAI_API_KEY" in (result.output + str(result.exception or ""))


def test_run_with_fake_adapters_records_runs(tmp_path, monkeypatch):
    write_templates(tmp_path, "https://example.org")
    toml = (tmp_path / "footnote.toml").read_text()
    toml = (
        toml.split("[[engines]]")[0]
        + '[[engines]]\nprovider = "openai"\nmodel = "gpt-5-mini"\n\n'
        + "[design]"
        + toml.split("[design]")[1]
    )
    (tmp_path / "footnote.toml").write_text(toml)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake = FakeAdapter(["ok"] * 100)
    monkeypatch.setattr(run_cmd, "default_adapters", lambda: {"openai": fake})
    result = CliRunner().invoke(
        app_with_command(), ["--root", str(tmp_path), "--label", "b1", "--budget", "5"]
    )
    assert result.exit_code == 0, result.output
    runs = list(JsonlStore(tmp_path / ".footnote").iter("runs", Run))
    assert runs and all(r.status == "ok" for r in runs) and "ok" in result.output and "spent" in result.output
    again = CliRunner().invoke(
        app_with_command(), ["--root", str(tmp_path), "--label", "b1", "--budget", "5"]
    )
    assert f"{len(runs)} already done" in again.output and fake.calls == len(runs)


# The tests below pin behaviour lines of the brief that the tests above leave unexercised.

KEY = "sk-secret-value"
INTERRUPTED = "interrupted; the manifest is marked aborted and a rerun with the same label resumes"


def openai_only(root):
    """The init templates with one engine, OpenAI gpt-5-mini: 4 wordings x 2 repeats = 8 calls a burst."""
    write_templates(root, "https://example.org")
    toml = (root / "footnote.toml").read_text()
    engine = '[[engines]]\nprovider = "openai"\nmodel = "gpt-5-mini"\n\n'
    head, tail = toml.split("[[engines]]")[0], toml.split("[design]")[1]
    (root / "footnote.toml").write_text(head + engine + "[design]" + tail)


def fake_openai(monkeypatch, script):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    fake = FakeAdapter(script)
    monkeypatch.setattr(run_cmd, "default_adapters", lambda: {"openai": fake})
    return fake


def invoke(root, *args):
    return CliRunner().invoke(app_with_command(), ["--root", str(root), *args])


def manifests_of(root):
    return list(JsonlStore(root / ".footnote").iter("manifests", Manifest))


def dry_run_call_lines(root):
    """The call list a dry run of a fresh label prints: intent id, wording and repeat, surface and model."""
    config, intents = load_config(root), load_intents(root)
    calls = enumerate_calls(intents, engine_configs(config, PlanningAssumptions.load()), config.design.reps)
    return [
        f"  {c.intent.id}, wording {c.prompt.paraphrase_idx + 1}, repeat {c.rep_idx + 1}, "
        f"{c.engine.surface} {c.engine.model_requested}"
        for c in calls
    ]


def test_dry_run_prints_the_call_list_and_the_plan_totals_against_the_given_budget(tmp_path):
    write_templates(tmp_path, "https://example.org")
    config, intents, assumptions = load_config(tmp_path), load_intents(tmp_path), PlanningAssumptions.load()
    plan = make_plan(config, intents, engine_configs(config, assumptions), PriceTable.load(), assumptions)
    assert plan.worst_total_usd > 0.5 and plan.budget_usd == 20.0  # 0.50 USD cannot cover the worst case
    result = invoke(tmp_path, "--label", "b1", "--budget", "0.5", "--dry-run")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "Dry run for label b1: 24 calls planned, 0 already done, 24 to make; no call made.",
        *dry_run_call_lines(tmp_path),  # 24 calls: at most 40, so every one is listed
        f"Per burst: typical {plan.typical_total_usd:.4f} USD; worst case {plan.worst_total_usd:.4f} USD; "
        "budget 0.5000 USD (does not fit the worst case).",  # under 1 USD, so four decimals
        f"Per month at 4 bursts: typical {plan.typical_month_usd:.4f} USD; "
        f"worst case {plan.worst_month_usd:.4f} USD.",
        "Label b1 has spent 0.0000 USD so far; 0.5000 USD of the 0.5000 USD budget left.",
    ]
    assert not (tmp_path / ".footnote").exists()


def test_dry_run_lists_the_first_ten_calls_past_forty(tmp_path):
    write_templates(tmp_path, "https://example.org")
    entries = "".join(
        f'  - id: topic-{n}\n    label: "T{n}"\n    prompts: ["a{n}", "b{n}", "c{n}"]\n' for n in range(3)
    )
    (tmp_path / "intents.yaml").write_text(f"version: 1\nintents:\n{entries}")  # 9 wordings x 2 x 3 = 54
    result = invoke(tmp_path, "--label", "b1", "--dry-run")
    lines = result.output.splitlines()
    assert result.exit_code == 0, result.output
    assert lines[0] == "Dry run for label b1: 54 calls planned, 0 already done, 54 to make; no call made."
    assert lines[1:11] == dry_run_call_lines(tmp_path)[:10]
    assert lines[11] == "  ... and 44 more (54 calls to make)" and lines[12].startswith("Per burst: ")


def test_dry_run_after_a_partial_burst_counts_what_is_done_and_spent(tmp_path, monkeypatch):
    openai_only(tmp_path)
    fake_openai(monkeypatch, ["ok", "ok", "403", "ok", "ok", "ok", "ok", "ok"])
    assert invoke(tmp_path, "--label", "b1", "--budget", "5").exit_code == 0
    result = invoke(tmp_path, "--label", "b1", "--budget", "5", "--dry-run")
    lines = result.output.splitlines()
    assert result.exit_code == 0, result.output
    assert lines[0] == "Dry run for label b1: 8 calls planned, 7 already done, 1 to make; no call made."
    assert lines[1] == dry_run_call_lines(tmp_path)[2]  # the call that met the HTTP error is asked again
    assert lines[-1] == "Label b1 has spent 0.07 USD so far; 4.93 USD of the 5.00 USD budget left."


def test_dry_run_refuses_an_unpriced_engine_with_exit_2(tmp_path):
    write_templates(tmp_path, "https://example.org")
    toml = (tmp_path / "footnote.toml").read_text().replace('model = "gpt-5-mini"', 'model = "gpt-999"')
    (tmp_path / "footnote.toml").write_text(toml)
    result = invoke(tmp_path, "--dry-run")
    assert result.exit_code == 2 and "openai/gpt-999" in result.output and "not priced" in result.output
    assert not (tmp_path / ".footnote").exists()


def test_run_summary_counts_what_earlier_runs_of_the_label_spent(tmp_path, monkeypatch):
    openai_only(tmp_path)
    fake = fake_openai(monkeypatch, ["ok"] * 8)
    assumptions = PlanningAssumptions.load()
    engine = engine_configs(load_config(tmp_path), assumptions)[0]
    # Each call reserves its worst case and the fake bills 0.01 USD, so this budget lets two calls run.
    budget = round(0.01 + worst_case_usd(engine, PriceTable.load(), assumptions) + 0.005, 4)
    worst, name = worst_case_usd(engine, PriceTable.load(), assumptions), f"config {engine.config_sha[:8]}"
    first = invoke(tmp_path, "--label", "b1", "--budget", str(budget))
    assert first.exit_code == 0, first.output
    assert first.output.splitlines() == [
        f"Label b1: 8 calls planned, 0 already done, 8 to make; budget {budget:.4f} USD, of which earlier "
        "runs of the label spent 0.0000 USD.",  # under 1 USD, so every amount has four decimals
        "[1/8] api:openai gpt-5-mini: ok, 0.0100 USD",
        "[2/8] api:openai gpt-5-mini: ok, 0.0100 USD",
        *(f"[{i}/8] api:openai gpt-5-mini: skipped for budget, 0.0000 USD" for i in range(3, 9)),
        f"api:openai gpt-5-mini ({name}): 2 ok, 6 skipped for budget; worst case {worst:.4f} USD per call",
        f"This run spent 0.0200 USD; label b1 has spent 0.0200 USD so far; {budget - 0.02:.4f} USD of the "
        f"{budget:.4f} USD budget left.",
        f"Manifest {manifests_of(tmp_path)[-1].id}",
    ]
    second = invoke(tmp_path, "--label", "b1", "--budget", "5")
    assert second.exit_code == 0, second.output
    lines = second.output.splitlines()
    assert lines[0] == (
        "Label b1: 8 calls planned, 2 already done, 6 to make; budget 5.00 USD, of which earlier runs of the "
        "label spent 0.02 USD."
    )
    assert lines[1] == "[3/8] api:openai gpt-5-mini: ok, 0.0100 USD" and len(lines) == 10
    assert lines[7:9] == [
        f"api:openai gpt-5-mini ({name}): 6 ok, 2 already done; worst case {worst:.4f} USD per call",
        "This run spent 0.06 USD; label b1 has spent 0.08 USD so far; 4.92 USD of the 5.00 USD budget left.",
    ]
    third = invoke(tmp_path, "--label", "b1", "--budget", "5")
    assert third.exit_code == 0 and "8 calls planned, 8 already done, 0 to make" in third.output
    assert f"({name}): 8 already done; worst case" in third.output
    assert "This run spent 0.00 USD" in third.output
    assert fake.calls == 8 and KEY not in first.output + second.output + third.output


def test_label_defaults_to_the_utc_date_and_the_budget_to_the_design_budget(tmp_path, monkeypatch):
    openai_only(tmp_path)
    fake_openai(monkeypatch, ["ok"] * 8)
    monkeypatch.setattr(run_cmd, "utcnow", lambda: datetime(2026, 10, 7, 23, 30, tzinfo=UTC))
    result = invoke(tmp_path)
    assert result.exit_code == 0, result.output
    manifest = manifests_of(tmp_path)[-1]
    assert manifest.label == "2026-10-07" and manifest.budget_usd == 20.0
    assert result.output.startswith("Label 2026-10-07: 8 calls planned")
    assert "of the 20.00 USD budget left" in result.output


def test_interrupt_exits_130_and_a_rerun_with_the_same_label_resumes(tmp_path, monkeypatch):
    openai_only(tmp_path)
    fake_openai(monkeypatch, ["ok", "interrupt"])
    result = invoke(tmp_path, "--label", "b1")
    assert result.exit_code == 130 and INTERRUPTED in result.output
    assert manifests_of(tmp_path)[-1].status == "aborted"
    rerun = fake_openai(monkeypatch, ["ok"] * 7)
    again = invoke(tmp_path, "--label", "b1")
    assert again.exit_code == 0, again.output
    assert "8 calls planned, 1 already done, 7 to make" in again.output and rerun.calls == 7
    assert result.output.splitlines()[1] == "[1/8] api:openai gpt-5-mini: ok, 0.0100 USD"  # printed before it


@pytest.mark.parametrize("budget", ["0", "-1", "nan", "inf"])
def test_a_budget_that_is_not_a_positive_number_exits_2_before_any_call(tmp_path, monkeypatch, budget):
    openai_only(tmp_path)
    fake = fake_openai(monkeypatch, ["ok"] * 8)
    result = invoke(tmp_path, "--label", "b1", f"--budget={budget}")
    assert result.exit_code == 2 and "--budget" in result.output
    assert fake.calls == 0 and not (tmp_path / ".footnote").exists()


def test_a_damaged_record_line_exits_2_naming_it_and_never_prints_the_key(tmp_path, monkeypatch):
    openai_only(tmp_path)
    fake_openai(monkeypatch, ["ok"] * 8)
    assert invoke(tmp_path, "--label", "b1").exit_code == 0
    runs = tmp_path / ".footnote" / "runs.jsonl"
    lines = runs.read_text(encoding="utf-8").splitlines()
    lines[1] = "not a record"  # a middle line: the file still ends with a newline, so it is not a torn write
    runs.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = invoke(tmp_path, "--label", "b1")
    assert result.exit_code == 2 and f"{runs}:2" in result.stderr
    assert KEY not in result.output + result.stderr


# D1: exit 1 when nothing useful happened, with the next step.
NO_OK = (
    "No call ended ok: check the statuses above and footnote doctor, then rerun with the same label to ask "
    "the failed calls again (refused, cut-off and unparsable answers are kept)."
)
ALL_BUDGET = (
    "Every remaining call was skipped for budget: raise the budget or lower the design; see footnote plan."
)


def test_a_burst_whose_calls_all_fail_exits_1_with_a_next_step(tmp_path, monkeypatch):
    openai_only(tmp_path)
    fake_openai(monkeypatch, ["403"] * 8)
    result = invoke(tmp_path, "--label", "b1")
    assert result.exit_code == 1 and NO_OK in result.stderr
    assert "[1/8] api:openai gpt-5-mini: error (http), 0.0000 USD" in result.stdout


def test_a_burst_whose_remaining_calls_are_all_skipped_for_budget_exits_1(tmp_path, monkeypatch):
    openai_only(tmp_path)
    fake = fake_openai(monkeypatch, ["ok"] * 8)
    result = invoke(tmp_path, "--label", "b1", "--budget", "0.001")
    assert result.exit_code == 1 and ALL_BUDGET in result.stderr and fake.calls == 0
    assert "0 to make" not in result.stdout and "budget 0.0010 USD" in result.stdout


def test_some_ok_calls_or_nothing_left_to_do_exit_0(tmp_path, monkeypatch):
    openai_only(tmp_path)
    fake_openai(monkeypatch, ["403", "ok"] + ["refused"] * 6)
    assert invoke(tmp_path, "--label", "b1").exit_code == 0  # one ok call is enough
    fake_openai(monkeypatch, ["ok"])
    assert invoke(tmp_path, "--label", "b1").exit_code == 0  # the HTTP error is asked again; refusals stay
    fake_openai(monkeypatch, [])
    done = invoke(tmp_path, "--label", "b1")
    assert done.exit_code == 0 and "8 already done, 0 to make" in done.stdout


def test_run_warns_when_the_label_ran_this_model_under_another_config(tmp_path, monkeypatch):
    """C2: the first run of b1 used the planning output limit (1200); footnote.toml then sets 1000."""
    openai_only(tmp_path)
    fake_openai(monkeypatch, ["ok"] * 8)
    assert invoke(tmp_path, "--label", "b1").exit_code == 0
    earlier = engine_configs(load_config(tmp_path), PlanningAssumptions.load())[0]
    toml = tmp_path / "footnote.toml"
    limit = 'model = "gpt-5-mini"\n[engines.params]\nmax_output_tokens = 1000\n'
    toml.write_text(toml.read_text().replace('model = "gpt-5-mini"\n', limit))
    now = engine_configs(load_config(tmp_path), PlanningAssumptions.load())[0]
    fake_openai(monkeypatch, ["ok"] * 8)
    result = invoke(tmp_path, "--label", "b1")
    warning = (
        f"Warning: earlier runs of label b1 used api:openai gpt-5-mini as config {earlier.config_sha[:8]}, "
        f"with max_output_tokens 1200 instead of 1000; config {now.config_sha[:8]} starts afresh: those runs "
        "are not resumed, and the report and diff keep the two configs apart."
    )
    assert result.exit_code == 0 and warning in result.stderr
    assert result.stdout.splitlines()[0].startswith("Label b1: 8 calls planned, 0 already done")
    dry = invoke(tmp_path, "--label", "b1", "--dry-run")  # the label still holds the earlier config's runs
    assert dry.exit_code == 0 and warning in dry.stderr and "8 already done, 0 to make" in dry.stdout
