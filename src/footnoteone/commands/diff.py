"""footnote diff: did the cited rate move between two windows of bursts? One plain-word verdict per engine.

A label that names no burst, or one given for both windows, exits 2 naming it (Ruling B22).
"""

from __future__ import annotations

from typing import Annotated

import typer

from footnoteone.commands.common import RootOption, fail, load_project, stored_manifests, unknown_labels
from footnoteone.config import engine_configs
from footnoteone.diff import compute_diff, render_diff_markdown
from footnoteone.library import read_pages
from footnoteone.planning import PlanningAssumptions
from footnoteone.runner import default_adapters


def register(app: typer.Typer) -> None:
    @app.command("diff")
    def diff(
        root: RootOption,
        before: Annotated[
            list[str],
            typer.Option("--before", help="Label of a burst in the earlier window. Repeat for several."),
        ],
        after: Annotated[
            list[str],
            typer.Option("--after", help="Label of a burst in the later window. Repeat for several."),
        ],
    ) -> None:
        """Compare the cited rate between two windows of bursts: Moved, No change or Can't tell yet."""
        config, intents = load_project(root)
        engines = engine_configs(config, PlanningAssumptions.load())
        both = [label for label in dict.fromkeys(before) if label in after]
        if both:
            fail(f"{', '.join(both)} is in both --before and --after; a burst belongs to one window only")
        try:  # a damaged record file under .footnote/ is a ValueError naming the file and line
            problem = unknown_labels([*before, *after], stored_manifests(root))
            if problem:
                fail(problem)
            pages = read_pages(root)
            report = compute_diff(root, config, intents, engines, pages, default_adapters(), before, after)
        except (OSError, ValueError) as exc:
            fail(str(exc))
        typer.echo(render_diff_markdown(report))
