"""What the commands share (F2): the project loader's exit 2 for files that cannot be read, and one _count."""

import pytest
import typer
from typer.testing import CliRunner

from footnoteone.commands import common, doctor, init, plan, run
from footnoteone.config import write_templates


def plan_app():
    app = typer.Typer()
    plan.register(app)
    return app


def test_a_footnote_toml_that_is_not_utf8_exits_2_naming_it(tmp_path):
    write_templates(tmp_path, "https://example.org")
    (tmp_path / "footnote.toml").write_bytes(b"\xff\xfe not text")
    result = CliRunner().invoke(plan_app(), ["--root", str(tmp_path)])
    assert result.exit_code == 2 and str(tmp_path / "footnote.toml") in result.stderr
    assert "not UTF-8" in result.stderr


def test_an_intents_file_that_cannot_be_read_exits_2_naming_it(tmp_path):
    write_templates(tmp_path, "https://example.org")
    (tmp_path / "intents.yaml").unlink()
    (tmp_path / "intents.yaml").mkdir()  # exists, but reading it is an OSError
    result = CliRunner().invoke(plan_app(), ["--root", str(tmp_path)])
    assert result.exit_code == 2 and str(tmp_path / "intents.yaml") in result.stderr
    assert "cannot be read" in result.stderr


@pytest.mark.parametrize(
    "raised", [UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), OSError(5, "I/O error")]
)
def test_load_project_turns_a_read_error_it_gets_into_exit_2(tmp_path, monkeypatch, raised):
    def unreadable(root):
        raise raised

    monkeypatch.setattr(common, "load_config", unreadable)
    with pytest.raises(typer.Exit) as info:
        common.load_project(tmp_path)
    assert info.value.exit_code == 2


def test_count_is_defined_once_and_shared():
    assert init._count is common._count and run._count is common._count and doctor._count is common._count
    assert (common._count(1, "page"), common._count(0, "page"), common._count(3, "page")) == (
        "1 page", "0 pages", "3 pages"
    )
