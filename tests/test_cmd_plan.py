import typer
from typer.testing import CliRunner

from footnoteone.commands.plan import register
from footnoteone.config import engine_configs, load_config, load_intents, write_templates
from footnoteone.plan import make_plan, render_plan_markdown
from footnoteone.planning import PlanningAssumptions
from footnoteone.pricing import PriceTable


def test_plan_prints_costs_and_power(tmp_path):
    write_templates(tmp_path, "https://example.org")
    app = typer.Typer()
    register(app)
    result = CliRunner().invoke(app, ["--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "worst case" in result.output and "api:openai" in result.output and "Warnings" in result.output
    assert "unbranded" in result.output and "\u2014" not in result.output


def test_plan_without_config_exits_2(tmp_path):
    app = typer.Typer()
    register(app)
    assert CliRunner().invoke(app, ["--root", str(tmp_path)]).exit_code == 2


# The tests below pin behaviour lines of the brief that the tests above leave unexercised.


def test_plan_prints_the_rendered_plan_of_the_engines_with_the_planning_limits_merged(tmp_path):
    write_templates(tmp_path, "https://example.org")
    config, intents, assumptions = load_config(tmp_path), load_intents(tmp_path), PlanningAssumptions.load()
    engines = engine_configs(config, assumptions)
    expected = render_plan_markdown(make_plan(config, intents, engines, PriceTable.load(), assumptions))
    app = typer.Typer()
    register(app)
    result = CliRunner().invoke(app, ["--root", str(tmp_path)])
    assert result.exit_code == 0 and result.output == expected


def test_plan_reads_the_current_folder_by_default(tmp_path, monkeypatch):
    write_templates(tmp_path, "https://example.org")
    monkeypatch.chdir(tmp_path)
    app = typer.Typer()
    register(app)
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 0 and result.output.startswith("# Plan for one burst")
