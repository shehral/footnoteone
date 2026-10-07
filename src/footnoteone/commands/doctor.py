"""footnote doctor: one line for each check of the project's files, keys, prices and store.

Nothing here prints the value of an environment variable, and nothing here writes to the project.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

import typer

from footnoteone.commands.common import RootOption, _count
from footnoteone.config import (
    ConfigError,
    ProjectConfig,
    engine_configs,
    load_config,
    load_intents,
    shared_platform,
)
from footnoteone.design import headline_intents
from footnoteone.library import read_pages
from footnoteone.plan import make_plan
from footnoteone.planning import PlanningAssumptions
from footnoteone.pricing import PriceTable
from footnoteone.runner import default_adapters, keys_from_env
from footnoteone.schema import EngineConfig, Intent, Manifest, Run, SourceRecord, utcnow
from footnoteone.store import JsonlStore, RawStore

_Line = tuple[str, str]  # (level, message); the level is "ok", "warn" or "fail"
_PRICES_STALE_AFTER_DAYS = 90
# load_config and load_intents raise ConfigError, a file that is not UTF-8 or cannot be read included; the
# other two stay as a guard, since a doctor should name any such problem rather than stop.
_LOAD_ERRORS = (ConfigError, OSError, UnicodeDecodeError)


def _one_line(text: str, drop: str = "") -> str:
    """`text` on one line: its lines joined by "; ", each without a leading `drop` (a file path) or colon."""
    parts = (line.strip().removeprefix(drop).lstrip(": ") for line in text.splitlines())
    return "; ".join(part for part in parts if part)


def _load_config(root: Path) -> tuple[ProjectConfig | None, _Line]:
    try:
        config = load_config(root)
    except _LOAD_ERRORS as exc:
        return None, ("fail", f"footnote.toml: {_one_line(str(exc), str(root / 'footnote.toml'))}")
    return config, ("ok", f"footnote.toml loads: {config.site.url}, {_count(len(config.engines), 'engine')}")


def _site_line(config: ProjectConfig) -> _Line | None:
    """A warning when what counts as the user's is not their own domain (Ruling B27): own_domains lists a
    platform many creators share, which hands the user every page on it, or own_domains is empty, so only
    the off-site prefixes count. None otherwise."""
    site = config.site
    platforms = [d for d in site.own_domains if shared_platform(f"https://{d}")]
    if platforms:
        return "warn", (
            f"site: own_domains lists {platforms[0]}, a platform shared by many creators, so every page on "
            "it counts as yours; list your profile there in offsite_prefixes instead"
        )
    if site.own_domains:
        return None
    where = "your site is on a shared platform" if shared_platform(site.url) else "own_domains is empty"
    prefixes = ", ".join(site.offsite_prefixes)
    return "warn", f"site: {where}; only the listed prefixes count as yours: {prefixes}"


def _load_intents(root: Path) -> tuple[list[Intent] | None, _Line]:
    try:
        intents = load_intents(root)
    except _LOAD_ERRORS as exc:
        return None, ("fail", f"intents.yaml: {_one_line(str(exc), str(root / 'intents.yaml'))}")
    kinds = Counter(intent.kind for intent in intents)
    by_kind = ", ".join(f"{kinds[kind]} {kind}" for kind in ("unbranded", "branded", "placebo"))
    return intents, ("ok", f"intents.yaml loads: {_count(len(intents), 'intent')} ({by_kind})")


def _pages_line(root: Path, site_url: str | None) -> _Line:
    """The library's page count, or the command that builds it. `site_url` is the site footnote.toml names,
    None when that file did not load."""
    try:
        pages = read_pages(root)
    except (OSError, ValueError) as exc:
        return "fail", f"pages: {_one_line(str(exc))}"
    if pages:
        return "ok", f"pages: {_count(len(pages), 'page')} in the library"
    # Once footnote.toml exists a plain init refuses to run and --force would overwrite the user's edits;
    # --pages-only rebuilds the library from footnote.toml as it is.
    rebuild = f"footnote init {site_url or '<site url>'} --pages-only"
    # A library file with no page means init ran and found none, so the hint says what to edit first.
    if (root / ".footnote" / "pages.jsonl").exists():
        return "warn", (
            "pages: the library is empty; after adding youtube_channels or own_domains to footnote.toml, "
            f"run {rebuild}"
        )
    if (root / "footnote.toml").exists():
        return "warn", f"pages: no library yet; run {rebuild}, which keeps footnote.toml and intents.yaml"
    return "warn", "pages: no library yet; run footnote init"


def _key_lines(config: ProjectConfig) -> list[_Line]:
    """Whether the variable each engine's provider reads is set, as runner.keys_from_env reads it (a blank
    value is missing). Only the variable's name is printed: footnote.toml refuses a [keys] entry that is not
    a variable's name, so a pasted key never gets here."""
    keys = keys_from_env(config)
    lines: list[_Line] = []
    for provider in dict.fromkeys(spec.provider for spec in config.engines):
        name = config.keys.env_name(provider)
        if keys.get(provider):
            lines.append(("ok", f"key {name} set ({provider})"))
        else:
            lines.append(("warn", f"key {name} missing ({provider} engines cannot run without it)"))
    return lines


def _price_line(table: PriceTable, today: date) -> _Line:
    try:
        days = max(0, (today - date.fromisoformat(table.version)).days)
    except ValueError:
        return "warn", f"pricing.yaml {table.version}: the version is not a date, so its age is unknown"
    age = f"pricing.yaml {table.version}, {_count(days, 'day')} old"
    if days > _PRICES_STALE_AFTER_DAYS:
        return "warn", f"{age}; list prices may have changed, so costs may be off"
    return "ok", age


def _engine_lines(engines: list[EngineConfig], table: PriceTable) -> list[_Line]:
    lines: list[_Line] = []
    for engine in engines:
        name = f"{engine.provider}/{engine.model_requested}"
        if table.is_known(engine.provider, engine.model_requested):
            lines.append(("ok", f"engine {name} priced"))
        else:
            lines.append(
                (
                    "fail",
                    f"engine {name} not priced in pricing.yaml {table.version}; "
                    "footnote run refuses unpriced engines",
                )
            )
    return lines


def _budget_line(
    config: ProjectConfig,
    intents: list[Intent],
    engines: list[EngineConfig],
    table: PriceTable,
    assumptions: PlanningAssumptions,
) -> _Line:
    plan = make_plan(config, intents, engines, table, assumptions)
    budget, worst = f"{plan.budget_usd:.2f} USD per burst", f"{plan.worst_total_usd:.2f} USD"
    if plan.fits_worst:
        return "ok", f"budget {budget} covers the worst case ({worst})"
    return "warn", (
        f"budget {budget} is below the worst case ({worst}); "
        "a run records budget_skip once the reserve is spent"
    )


def _unbranded_line(config: ProjectConfig, intents: list[Intent]) -> _Line:
    needed, have = config.design.min_shared_intents, len(headline_intents(intents))
    if have < needed:
        return "warn", (
            f"unbranded intents: {have}, below min_shared_intents = {needed}; "
            f"no verdict is possible until you have {needed}"
        )
    return "ok", f"unbranded intents: {have}, enough for a verdict (min_shared_intents = {needed})"


def _store(root: Path) -> tuple[_Line, dict[str, Run] | None]:
    """The store line and each run's last record; None for the runs when the store cannot be read.

    Run and manifest ids repeat in the files (the runner records a manifest at its start and its end, and
    a retried call again), and the last record of an id is the one that counts.
    """
    directory = root / ".footnote"
    if not directory.is_dir():
        return ("warn", "store: no .footnote/ folder yet; footnote init and footnote run create it"), {}
    store = JsonlStore(directory)  # the folder exists, so this creates nothing
    try:
        manifests = {record.id for record in store.iter("manifests", Manifest)}
        runs = {record.id: record for record in store.iter("runs", Run)}
        sources = sum(1 for _ in store.iter("sources", SourceRecord))
        blobs = sum(1 for _ in (directory / "raw").glob("*.json"))
    except (OSError, ValueError) as exc:
        return ("fail", f"store: {_one_line(str(exc))}"), None
    counts = ", ".join(
        [
            _count(len(manifests), "manifest"),
            _count(len(runs), "run"),
            _count(sources, "source"),
            _count(blobs, "raw response"),
        ]
    )
    return ("ok", f"store .footnote/: {counts}"), runs


def _parser_line(root: Path, runs: dict[str, Run]) -> _Line:
    """Runs a report will re-read with the current parser: read by an older adapter version than
    runner.default_adapters gives, with their raw response stored (M6: a run without its blob cannot be
    replayed and keeps its stored reading)."""
    current = {adapter.version for adapter in default_adapters().values()}
    raw = RawStore(root / ".footnote") if (root / ".footnote" / "raw").is_dir() else None
    older = sum(
        1
        for run in runs.values()
        if run.parser_version
        and run.parser_version not in current
        and run.raw_sha256
        and raw is not None
        and raw.exists(run.raw_sha256)
    )
    if older:
        return "ok", f"parsers: {_count(older, 'run')} will be replayed with the current parser"
    return "ok", "parsers: no stored run needs replaying"


def checks(root: Path, today: date | None = None) -> list[tuple[str, str]]:
    """Every check in order, as (level, message): footnote.toml, intents.yaml, the page library, one key per
    provider in use, pricing.yaml, planning.yaml, each engine priced, the budget, the unbranded intents,
    the store and the parsers. A check that needs a file that did not load is left out. `today` stands in
    for the current UTC date when the age of the prices is worked out."""
    root = Path(root)
    today = today or utcnow().date()
    table, assumptions = PriceTable.load(), PlanningAssumptions.load()
    config, config_line = _load_config(root)
    intents, intents_line = _load_intents(root)
    site_line = _site_line(config) if config is not None else None
    lines = [config_line, *([site_line] if site_line else []), intents_line]
    lines.append(_pages_line(root, config.site.url if config else None))
    if config is not None:
        lines += _key_lines(config)
    lines += [_price_line(table, today), ("ok", f"planning.yaml {assumptions.version}")]
    if config is not None:
        engines = engine_configs(config, assumptions)
        lines += _engine_lines(engines, table)
        if intents is not None:
            lines += [
                _budget_line(config, intents, engines, table, assumptions),
                _unbranded_line(config, intents),
            ]
    store_line, runs = _store(root)
    lines.append(store_line)
    if runs is not None:
        lines.append(_parser_line(root, runs))
    return lines


def register(app: typer.Typer) -> None:
    @app.command("doctor")
    def doctor(root: RootOption) -> None:
        """Check the project's files, API keys, prices and .footnote store, one line for each check."""
        lines = checks(root)
        for level, message in lines:
            typer.echo(f"{level:4} {message}")
        if any(level == "fail" for level, _ in lines):
            raise typer.Exit(1)
