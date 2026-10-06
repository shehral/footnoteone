from typer.testing import CliRunner

from footnoteone import __version__
from footnoteone.cli import app


def test_version_flag_prints_version():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output
