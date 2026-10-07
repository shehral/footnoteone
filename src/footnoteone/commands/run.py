"""footnote run: one burst of the design under the budget cap, or with --dry-run its calls and cost.

The budget is per burst label: a rerun with the same label leaves alone the calls whose last record is final
(ok, refused, truncated or a parse failure) and counts what the earlier runs of that label spent. The run
prints the label and its calls before the first one, a line after each call and one line per engine at the
end, and exits 1 when no call it made ended ok or when every call left was skipped for budget. Keys come from
the environment variables footnote.toml names and are never printed.
"""

from __future__ import annotations

import asyncio
import math
from typing import Annotated, get_args

import typer

from footnoteone.commands.common import RootOption, _count, fail, load_project
from footnoteone.config import engine_configs
from footnoteone.design import Call
from footnoteone.plan import PlanSummary, make_plan
from footnoteone.planning import PlanningAssumptions
from footnoteone.pricing import PriceTable
from footnoteone.runner import (
    BurstStart,
    CallDone,
    EngineTally,
    RunnerResult,
    default_adapters,
    keys_from_env,
    run_design,
)
from footnoteone.schema import EngineConfig, RunStatus, first_difference, utcnow

INTERRUPTED = "interrupted; the manifest is marked aborted and a rerun with the same label resumes"
NO_OK = (
    "No call ended ok: check the statuses above and footnote doctor, then rerun with the same label to ask "
    "the failed calls again (refused, cut-off and unparsable answers are kept)."
)
ALL_BUDGET = (
    "Every remaining call was skipped for budget: raise the budget or lower the design; see footnote plan."
)
DRY_RUN_FULL_LIST = 40  # a dry run lists every call to make up to this many, else the first DRY_RUN_HEAD
DRY_RUN_HEAD = 10
_STATUSES: tuple[str, ...] = get_args(RunStatus)  # the order the status counts are printed in
_WORDS = {"budget_skip": "skipped for budget"}  # every other status prints as its name


def _usd(x: float, budget: float) -> str:
    """An amount in USD as the run prints it: four decimals under a budget below 1 USD, where cents matter,
    else two. A call's cost and worst case always print with four."""
    return f"{x:.4f}" if budget < 1 else f"{x:.2f}"


def _engine(engine: EngineConfig) -> str:
    return f"{engine.surface} {engine.model_requested}"


def _outcome(status: str, kind: str | None) -> str:
    """A call's status in words, an error with its kind: "ok", "skipped for budget", "error (http)"."""
    words = _WORDS.get(status, status)
    return f"{words} ({kind.replace('_', ' ')})" if status == "error" and kind else words


def _start_line(start: BurstStart) -> str:
    b, planned, done = start.budget_usd, start.planned_calls, start.already_done
    return (
        f"Label {start.label}: {_count(planned, 'call')} planned, {done} already done, {planned - done} to "
        f"make; budget {_usd(b, b)} USD, of which earlier runs of the label spent "
        f"{_usd(start.prior_spent_usd, b)} USD."
    )


def _changed_lines(label: str, changed: list[tuple[EngineConfig, EngineConfig]]) -> list[str]:
    """A warning for each engine config the label ran earlier that footnote.toml no longer gives."""
    return [
        f"Warning: earlier runs of label {label} used {_engine(earlier)} as config {earlier.config_sha[:8]}, "
        f"with {first_difference(earlier, now)}; config {now.config_sha[:8]} starts afresh: those runs are "
        "not resumed, and the report and diff keep the two configs apart."
        for earlier, now in changed
    ]


def _started(start: BurstStart) -> None:
    typer.echo(_start_line(start))
    for line in _changed_lines(start.label, start.changed):
        typer.echo(line, err=True)


def _call_line(done: CallDone) -> str:
    return (
        f"[{done.index}/{done.total}] {_engine(done.engine)}: {_outcome(done.status, done.error_kind)}, "
        f"{done.cost_usd:.4f} USD"
    )


def _engine_line(tally: EngineTally) -> str:
    """One engine's share of the burst: its calls by status, those already done, and its worst case."""
    parts = [f"{n} {_WORDS.get(status, status)}" for status in _STATUSES if (n := tally.counts.get(status))]
    if tally.already_done:
        parts.append(f"{tally.already_done} already done")
    return (
        f"{_engine(tally.engine)} (config {tally.engine.config_sha[:8]}): {', '.join(parts) or 'no call'}; "
        f"worst case {tally.worst_case_usd:.4f} USD per call"
    )


def _money_line(label: str, result: RunnerResult, budget: float) -> str:
    total = result.prior_spent_usd + result.spent_usd
    spent, so_far, left = (_usd(x, budget) for x in (result.spent_usd, total, max(0.0, budget - total)))
    return (
        f"This run spent {spent} USD; label {label} has spent {so_far} USD so far; {left} USD of the "
        f"{_usd(budget, budget)} USD budget left."
    )


def _call_list(calls: list[Call]) -> list[str]:
    """The calls a dry run would make, each as intent id, wording, repeat, surface and model: every one up
    to DRY_RUN_FULL_LIST, else the first DRY_RUN_HEAD and how many more."""
    shown = calls if len(calls) <= DRY_RUN_FULL_LIST else calls[:DRY_RUN_HEAD]
    lines = [
        f"  {c.intent.id}, wording {c.prompt.paraphrase_idx + 1}, repeat {c.rep_idx + 1}, {_engine(c.engine)}"
        for c in shown
    ]
    if len(shown) < len(calls):
        lines.append(f"  ... and {len(calls) - len(shown)} more ({len(calls)} calls to make)")
    return lines


def _dry_run_lines(label: str, result: RunnerResult, plan: PlanSummary, budget: float) -> list[str]:
    fit = "fits" if plan.worst_total_usd <= budget else "does not fit"
    usd = {
        name: _usd(x, budget)
        for name, x in (
            ("typical", plan.typical_total_usd), ("worst", plan.worst_total_usd), ("budget", budget),
            ("typical_month", plan.typical_month_usd), ("worst_month", plan.worst_month_usd),
            ("spent", result.prior_spent_usd), ("left", max(0.0, budget - result.prior_spent_usd)),
        )
    }
    return [
        f"Dry run for label {label}: {_count(result.planned_calls, 'call')} planned, "
        f"{result.skipped_existing} already done, {len(result.to_call)} to make; no call made.",
        *_call_list(result.to_call),
        f"Per burst: typical {usd['typical']} USD; worst case {usd['worst']} USD; budget {usd['budget']} USD "
        f"({fit} the worst case).",
        f"Per month at {_count(plan.bursts_per_month, 'burst')}: typical {usd['typical_month']} USD; worst "
        f"case {usd['worst_month']} USD.",
        f"Label {label} has spent {usd['spent']} USD so far; {usd['left']} USD of the {usd['budget']} USD "
        "budget left.",
    ]


def _next_step(result: RunnerResult) -> str | None:
    """Why the run should exit 1, or None: at least one call was made and none ended ok, or every call left
    to make was skipped for budget."""
    made = sum(n for status, n in result.counts.items() if status != "budget_skip")
    remaining = result.planned_calls - result.skipped_existing
    if made and not result.counts.get("ok"):
        return NO_OK
    if remaining and result.counts.get("budget_skip", 0) == remaining:
        return ALL_BUDGET
    return None


def register(app: typer.Typer) -> None:
    @app.command("run")
    def run(
        root: RootOption,
        label: Annotated[
            str | None,
            typer.Option(
                "--label",
                help="Name of the burst; a rerun with the same label resumes it. "
                "Default: today's date (UTC).",
            ),
        ] = None,
        budget: Annotated[
            float | None,
            typer.Option(
                "--budget",
                help="Cap in USD for the burst, earlier runs of the same label included. "
                "Default: budget_usd_per_burst in footnote.toml.",
            ),
        ] = None,
        dry_run: Annotated[
            bool,
            typer.Option(
                "--dry-run",
                help="Make no call: list the calls the burst would make, its cost and what the label has "
                "already spent.",
            ),
        ] = False,
    ) -> None:
        """Run one burst of the design under the budget cap; --dry-run shows its calls and cost instead."""
        config, intents = load_project(root)
        label = label or utcnow().date().isoformat()
        budget = config.design.budget_usd_per_burst if budget is None else budget
        # What DesignConfig asks of budget_usd_per_burst: at 0 or below every call would be recorded as a
        # budget_skip, and the manifest refuses nan and inf with a traceback.
        if not (math.isfinite(budget) and budget > 0):
            fail(f"--budget must be a positive number of USD, got {budget:g}")
        try:
            table, assumptions = PriceTable.load(), PlanningAssumptions.load()
            engines = engine_configs(config, assumptions)
            result = asyncio.run(
                run_design(
                    root, config, intents, engines, label, budget, dry_run, default_adapters(),
                    keys_from_env(config), table, assumptions,
                    on_start=_started,
                    on_call=lambda done: typer.echo(_call_line(done)),
                )
            )
        except KeyboardInterrupt:
            typer.echo(INTERRUPTED, err=True)
            raise typer.Exit(130) from None
        # A RunnerError (an unpriced engine, a missing key; nothing was written), a damaged record file (a
        # ValueError naming its file and line) or a store that cannot be read or written (an OSError). Any
        # other exception propagates; the app prints it without local variables.
        except (OSError, ValueError) as exc:
            fail(str(exc))
        if dry_run:
            plan = make_plan(config, intents, engines, table, assumptions)
            lines = _dry_run_lines(label, result, plan, budget)
            typer.echo(lines[0])
            for line in _changed_lines(label, result.changed):
                typer.echo(line, err=True)
            for line in lines[1:]:
                typer.echo(line)
            return
        for tally in result.engines:
            typer.echo(_engine_line(tally))
        typer.echo(_money_line(label, result, budget))
        typer.echo(f"Manifest {result.manifest.id if result.manifest else 'not written'}")
        step = _next_step(result)
        if step:
            typer.echo(step, err=True)
            raise typer.Exit(1)
