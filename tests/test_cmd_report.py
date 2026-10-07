from datetime import UTC, datetime

import typer
from helpers import OWN, build_store, shifted_project
from typer.testing import CliRunner

from footnoteone.commands.report import register
from footnoteone.config import write_templates
from footnoteone.schema import EngineConfig, Intent, Prompt


def test_report_command_writes_files(tmp_path):
    write_templates(tmp_path, "https://example.org")
    engine = EngineConfig(
        provider="openai",
        model_requested="gpt-5-mini",
        params={"max_output_tokens": 1200, "max_tool_calls": 3, "force_search": True},
    )
    intents = [Intent(id="example-topic", label="x", prompts=[Prompt(id="p", text="q")])]
    build_store(tmp_path, ["w1"], [engine], intents, reps=1, pattern=lambda *a: ("ok", "yes", [OWN], [OWN]))
    app = typer.Typer()
    register(app)
    result = CliRunner().invoke(app, ["--root", str(tmp_path), "--label", "w1"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "reports" / "w1" / "report.html").exists() and "report.md" in result.output


# Behaviour lines of the brief that the test above leaves unexercised.


def cites_own(label, intent, prompt, engine, rep):
    return ("ok", "yes", [OWN], [OWN])


def two_bursts(root):
    """w1 starts 2026-10-01 at 15:00 UTC and w2 a week later, both with the template's openai engine."""
    write_templates(root, "https://example.org")
    engine = EngineConfig(
        provider="openai",
        model_requested="gpt-5-mini",
        params={"max_output_tokens": 1200, "max_tool_calls": 3, "force_search": True},
    )
    intents = [Intent(id="example-topic", label="x", prompts=[Prompt(id="p", text="q")])]
    start = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
    build_store(root, ["w1", "w2"], [engine], intents, reps=1, pattern=cites_own, start=start)


def run_report(root, *args):
    app = typer.Typer()
    register(app)
    return CliRunner().invoke(app, ["--root", str(root), *args])


def test_report_selects_bursts_by_date_and_until_includes_its_whole_day(tmp_path):
    two_bursts(tmp_path)
    late = run_report(tmp_path, "--since", "2026-10-02")
    assert late.exit_code == 0, late.output
    assert f"Wrote {tmp_path / 'reports' / 'w2' / 'report.md'}" in late.stdout
    early = run_report(tmp_path, "--until", "2026-10-01")  # w1 started at 15:00 that day
    assert early.exit_code == 0, early.output
    assert f"Wrote {tmp_path / 'reports' / 'w1' / 'report.html'}" in early.stdout


def test_report_exits_2_on_a_date_that_is_not_iso_or_a_reversed_window(tmp_path):
    two_bursts(tmp_path)
    bad = run_report(tmp_path, "--since", "October")
    assert bad.exit_code == 2 and "--since takes an ISO date" in bad.stderr
    reversed_window = run_report(tmp_path, "--since", "2026-10-09", "--until", "2026-10-01")
    assert reversed_window.exit_code == 2 and "is after --until" in reversed_window.stderr
    assert not (tmp_path / "reports").exists()


def test_report_writes_into_out_and_says_when_no_burst_matches(tmp_path):
    two_bursts(tmp_path)
    result = run_report(tmp_path, "--since", "2027-01-01", "--out", str(tmp_path / "custom"))
    assert result.exit_code == 0, result.output
    assert (tmp_path / "custom" / "report.md").exists() and (tmp_path / "custom" / "report.html").exists()
    assert "no bursts match the selection" in result.stderr


# C5 (M4, M5, Ruling B29): with no --label (and no dates) the report covers the most recent burst only and
# says so; a label no burst carries exits 2 naming it.


def test_report_without_a_label_covers_the_most_recent_burst_and_says_so(tmp_path):
    two_bursts(tmp_path)
    result = run_report(tmp_path)
    assert result.exit_code == 0, result.output
    md = (tmp_path / "reports" / "w2" / "report.md").read_text(encoding="utf-8")
    assert "- Bursts: w2 (the most recent burst; --label picks others)\n" in md
    assert "| w1 |" not in md and "| w2 |" in md


def test_report_with_dates_and_no_label_covers_every_burst_in_the_window(tmp_path):
    two_bursts(tmp_path)
    result = run_report(tmp_path, "--since", "2026-09-01", "--out", str(tmp_path / "window"))
    assert result.exit_code == 0, result.output
    md = (tmp_path / "window" / "report.md").read_text(encoding="utf-8")
    assert "- Bursts: w1, w2\n" in md and "| w1 |" in md and "| w2 |" in md


def test_report_exits_2_naming_a_label_no_burst_carries(tmp_path):
    two_bursts(tmp_path)
    result = run_report(tmp_path, "--label", "w1", "--label", "nope")
    assert result.exit_code == 2 and "nope" in result.stderr and "w1, w2" in result.stderr
    assert not (tmp_path / "reports").exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    write_templates(empty, "https://example.org")
    fresh = run_report(empty, "--label", "w1")
    assert fresh.exit_code == 2 and "w1" in fresh.stderr and "no burst has been run yet" in fresh.stderr


def test_report_exits_2_on_a_damaged_store(tmp_path):
    two_bursts(tmp_path)
    (tmp_path / ".footnote" / "runs.jsonl").write_text('{"id": 1}\n{"id": 2}\n', encoding="utf-8")
    result = run_report(tmp_path)
    assert result.exit_code == 2 and "runs.jsonl" in result.stderr


def test_a_report_on_a_burst_run_under_an_older_config_shows_that_config_s_data(tmp_path):
    """C1, the build_shift scenario: w1 ran with output limit 1000, footnote.toml now says 1200."""
    old, current = shifted_project(tmp_path)
    result = run_report(tmp_path, "--label", "w1")
    assert result.exit_code == 0, result.output
    md = (tmp_path / "reports" / "w1" / "report.md").read_text(encoding="utf-8")
    sections = md.split("\n## ")
    for engine in old:
        [section] = [s for s in sections if f"config {engine.config_sha[:8]})" in s.splitlines()[0]]
        assert "Not in footnote.toml now" in section and "| Cited: " in section and "50%" in section
    for engine in current:
        [section] = [s for s in sections if f"config {engine.config_sha[:8]})" in s.splitlines()[0]]
        assert "no runs in the selected bursts" in section and "| Cited: " not in section
    assert "none of its runs is stored" not in md
