"""Command-line entry point. Commands are added by the implementation plan tasks."""

from __future__ import annotations

from typing import Annotated

import typer

from footnoteone import __version__

HELP = "FootnoteOne: see which of your pages AI answer engines search, read and cite."
app = typer.Typer(no_args_is_help=True, help=HELP)


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
