"""footnote report: the funnel per engine over the chosen bursts, written as report.md and report.html.

With no --label and no dates it covers the most recent burst only, and says so: pooling every burst ever run
would mix designs. A --label that no burst carries exits 2 naming it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Annotated

import typer

from footnoteone.commands.common import RootOption, fail, load_project, stored_manifests, unknown_labels
from footnoteone.config import engine_configs
from footnoteone.library import read_pages
from footnoteone.metrics import compute, latest_label
from footnoteone.planning import PlanningAssumptions
from footnoteone.report import write_report
from footnoteone.runner import default_adapters

LATEST = "the most recent burst; --label picks others"


def _day(value: str | None, option: str, last_moment: bool = False) -> datetime | None:
    """An ISO date as an aware UTC datetime: the midnight that starts the day, or with `last_moment` the last
    microsecond before the next midnight, so an --until date includes the bursts started that day. A value
    that is not an ISO date exits 2."""
    if value is None:
        return None
    try:
        day = date.fromisoformat(value)
    except ValueError:
        fail(f"{option} takes an ISO date such as 2026-10-01, not {value!r}")
    return datetime.combine(day, time.max if last_moment else time.min, tzinfo=UTC)


def register(app: typer.Typer) -> None:
    @app.command("report")
    def report(
        root: RootOption,
        label: Annotated[
            list[str] | None,
            typer.Option(
                "--label",
                help="Burst label to include. Repeat for several. Default: the most recent burst, or every "
                "burst in --since and --until when either is given.",
            ),
        ] = None,
        since: Annotated[
            str | None, typer.Option("--since", help="First day of bursts to include, an ISO date (UTC).")
        ] = None,
        until: Annotated[
            str | None, typer.Option("--until", help="Last day of bursts to include, an ISO date (UTC).")
        ] = None,
        out: Annotated[
            Path | None,
            typer.Option(
                "--out", help="Folder for report.md and report.html. Default: reports/<label or date>."
            ),
        ] = None,
    ) -> None:
        """Write the funnel per engine for the chosen bursts as report.md and report.html."""
        start, end = _day(since, "--since"), _day(until, "--until", last_moment=True)
        if start is not None and end is not None and start > end:
            fail(f"--since {since} is after --until {until}")
        config, intents = load_project(root)
        engines = engine_configs(config, PlanningAssumptions.load())
        try:  # a damaged record file is a ValueError naming it; an unwritable --out folder an OSError
            manifests = stored_manifests(root)
            labels, selection = label or None, ""
            if labels:
                problem = unknown_labels(labels, manifests)
                if problem:
                    fail(problem)
            elif start is None and end is None and manifests:
                labels, selection = [latest_label(manifests)], LATEST
            pages = read_pages(root)
            result = compute(
                root, config, intents, engines, pages, default_adapters(), labels=labels, since=start,
                until=end,
            )
            result.selection = selection
            md_path, html_path = write_report(root, result, out_dir=out)
        except (OSError, ValueError) as exc:
            fail(str(exc))
        if result.note:
            typer.echo(f"{result.note}; the report has no data", err=True)
        typer.echo(f"Wrote {md_path}")
        typer.echo(f"Wrote {html_path}")
