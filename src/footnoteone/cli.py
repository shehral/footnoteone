"""Command-line entry point: the `footnote` app with every command in `commands.ALL`."""

from __future__ import annotations

from typing import Annotated

import typer

from footnoteone import __version__
from footnoteone.commands import ALL

HELP = "FootnoteOne: see which of your pages AI answer engines search, read and cite."
# An unexpected error prints Python's plain traceback: Typer's Rich panel can list the local variables of
# every frame, and run_design holds the API keys in one.
app = typer.Typer(
    no_args_is_help=True, help=HELP, pretty_exceptions_enable=False, pretty_exceptions_show_locals=False
)
for module in ALL:
    module.register(app)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"footnote {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version and exit.")
    ] = False,
) -> None:
    """FootnoteOne command line."""
