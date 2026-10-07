import typer
from helpers import OWN, build_store
from typer.testing import CliRunner

from footnoteone.commands.diff import register
from footnoteone.config import engine_configs, load_config, write_templates
from footnoteone.planning import PlanningAssumptions
from footnoteone.schema import Intent, Prompt


def diff_app():
    app = typer.Typer()
    register(app)
    return app


def test_diff_command_prints_verdicts(tmp_path):
    write_templates(tmp_path, "https://example.org")
    # The stored engine must be the config's engine, planning limits merged, or the diff cannot find it.
    engine = engine_configs(load_config(tmp_path), PlanningAssumptions.load())[0]
    intents = [Intent(id=f"u{k}", label="x", prompts=[Prompt(id=f"u{k}p", text="q")]) for k in range(8)]
    entries = "".join(f"  - id: u{k}\n    label: x\n    prompts: [q]\n" for k in range(8))
    (tmp_path / "intents.yaml").write_text("version: 1\nintents:\n" + entries)

    def every_run_cites(*call):
        return ("ok", "yes", [OWN], [OWN])

    build_store(tmp_path, ["w1", "w2"], [engine], intents, reps=1, pattern=every_run_cites)
    app = typer.Typer()
    register(app)
    result = CliRunner().invoke(app, ["--root", str(tmp_path), "--before", "w1", "--after", "w2"])
    assert result.exit_code == 0, result.output
    assert "api:openai" in result.output and "shared intents" in result.output
    assert f"api:openai gpt-5-mini ({engine.config_sha[:8]}): Can't tell yet." in result.output
    # Every run cites in both windows, so the interval has no width; the line still names the intents needed.
    assert "intents would be needed to see a 10-point move at this design." in result.output
    # The two template engines with no runs get the insufficient line with no shared intents.
    lines = result.output.splitlines()
    assert sum(line.endswith(": Not enough shared intents (0 of 8); no verdict.") for line in lines) == 2


def test_diff_command_exits_2_on_a_damaged_store(tmp_path):
    write_templates(tmp_path, "https://example.org")
    (tmp_path / ".footnote").mkdir()
    (tmp_path / ".footnote" / "manifests.jsonl").write_text('{"id": 1}\n{"id": 2}\n', encoding="utf-8")
    result = CliRunner().invoke(diff_app(), ["--root", str(tmp_path), "--before", "w1", "--after", "w2"])
    assert result.exit_code == 2 and "manifests.jsonl" in result.stderr


# C6 (Ruling B22): a label that names no burst, or one given for both windows, exits 2 naming it.


def two_windows(root):
    write_templates(root, "https://example.org")
    engine = engine_configs(load_config(root), PlanningAssumptions.load())[0]
    intents = [Intent(id=f"u{k}", label="x", prompts=[Prompt(id=f"u{k}p", text="q")]) for k in range(8)]
    build_store(root, ["w1", "w2"], [engine], intents, reps=1, pattern=lambda *a: ("ok", "yes", [OWN], [OWN]))


def test_diff_exits_2_naming_a_label_no_burst_carries(tmp_path):
    two_windows(tmp_path)
    result = CliRunner().invoke(diff_app(), ["--root", str(tmp_path), "--before", "w1", "--after", "w9"])
    assert result.exit_code == 2 and "w9" in result.stderr and "w1, w2" in result.stderr
    before = CliRunner().invoke(diff_app(), ["--root", str(tmp_path), "--before", "nope", "--after", "w2"])
    assert before.exit_code == 2 and "nope" in before.stderr


def test_diff_exits_2_naming_a_label_given_for_both_windows(tmp_path):
    two_windows(tmp_path)
    args = ["--root", str(tmp_path), "--before", "w1", "--before", "w2", "--after", "w2"]
    result = CliRunner().invoke(diff_app(), args)
    assert result.exit_code == 2 and "w2 is in both --before and --after" in result.stderr


def test_diff_prints_both_windows_in_its_heading(tmp_path):
    two_windows(tmp_path)
    result = CliRunner().invoke(diff_app(), ["--root", str(tmp_path), "--before", "w1", "--after", "w2"])
    heading = "# Change between windows (before: w1; after: w2)"
    assert result.exit_code == 0 and result.stdout.startswith(heading)
