import pytest
from helpers import OWN, build_store
from typer.main import except_hook
from typer.testing import CliRunner

from footnoteone.cli import app
from footnoteone.config import engine_configs, load_config, write_templates
from footnoteone.planning import PlanningAssumptions
from footnoteone.schema import Intent, Prompt
from footnoteone.store import JsonlStore


def test_help_lists_every_command():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ("init", "plan", "run", "report", "diff", "crawl-check", "doctor"):
        assert name in result.output


def test_plan_and_report_run_on_a_fixture_store(tmp_path):
    write_templates(tmp_path, "https://example.org")
    engines = engine_configs(load_config(tmp_path), PlanningAssumptions.load())
    intents = [
        Intent(id="example-topic", label="x", prompts=[Prompt(id="p", text="q")]),
        Intent(id="placebo-water", label="p", kind="placebo", prompts=[Prompt(id="pp", text="q")]),
    ]
    build_store(
        tmp_path, ["w1"], engines[:1], intents, reps=1, pattern=lambda *a: ("ok", "yes", [OWN], [OWN])
    )
    plan = CliRunner().invoke(app, ["plan", "--root", str(tmp_path)])
    report = CliRunner().invoke(app, ["report", "--root", str(tmp_path), "--label", "w1"])
    assert plan.exit_code == 0 and "worst case" in plan.output
    assert report.exit_code == 0 and (tmp_path / "reports" / "w1" / "report.html").exists()
    doctor = CliRunner().invoke(app, ["doctor", "--root", str(tmp_path)])
    assert doctor.exit_code in (0, 1) and "pricing.yaml" in doctor.output


# A traceback never shows local variables: run_design holds the keys as a local, and Typer's Rich panel would
# print every local of every frame.
SECRET = "sk-local-panel-secret-0123"


def test_the_app_never_shows_local_variables_in_a_traceback():
    assert app.pretty_exceptions_show_locals is False and app.pretty_exceptions_enable is False


def crash_after_the_keys_are_read(tmp_path, monkeypatch):
    """A project whose burst dies with an unexpected error once run_design holds the keys."""
    write_templates(tmp_path, "https://example.org")
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PERPLEXITY_API_KEY"):
        monkeypatch.setenv(name, SECRET)

    def boom(self, name, record):
        raise RuntimeError("the disk is on fire")

    monkeypatch.setattr(JsonlStore, "append", boom)
    with pytest.raises(RuntimeError) as info:
        app(["run", "--root", str(tmp_path), "--label", "b1"], prog_name="footnote")
    return info.value


def test_an_unexpected_error_prints_a_traceback_without_the_keys(tmp_path, monkeypatch, capsys):
    exc = crash_after_the_keys_are_read(tmp_path, monkeypatch)
    except_hook(type(exc), exc, exc.__traceback__)
    printed = capsys.readouterr()
    assert "the disk is on fire" in printed.err and SECRET not in printed.out + printed.err


def test_the_traceback_check_would_see_a_key_in_a_locals_panel(tmp_path, monkeypatch, capsys):
    """The check above can fail: with Typer's locals panel switched on, the same crash prints the key."""
    monkeypatch.setattr(app, "pretty_exceptions_enable", True)
    monkeypatch.setattr(app, "pretty_exceptions_show_locals", True)
    exc = crash_after_the_keys_are_read(tmp_path, monkeypatch)
    except_hook(type(exc), exc, exc.__traceback__)
    assert SECRET in capsys.readouterr().err
