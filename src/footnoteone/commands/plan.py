"""footnote plan: what one burst and a month of bursts cost, and the smallest move a burst can detect.

Nothing runs and nothing is written; problems with the design are warnings in the printed plan.
"""

from __future__ import annotations

import typer

from footnoteone.commands.common import RootOption, load_project
from footnoteone.config import engine_configs
from footnoteone.plan import make_plan, render_plan_markdown
from footnoteone.planning import PlanningAssumptions
from footnoteone.pricing import PriceTable


def register(app: typer.Typer) -> None:
    @app.command("plan")
    def plan(root: RootOption) -> None:
        """Show what a burst and a month of bursts cost and the smallest move a burst can detect.

        Nothing runs and nothing is written."""
        config, intents = load_project(root)
        assumptions = PlanningAssumptions.load()
        engines = engine_configs(config, assumptions)  # the planning limits merged under each engine's params
        summary = make_plan(config, intents, engines, PriceTable.load(), assumptions)
        typer.echo(render_plan_markdown(summary), nl=False)
