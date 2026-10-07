"""Command modules: each exposes `register(app)`, which adds its command to the CLI."""

from __future__ import annotations

from types import ModuleType

from footnoteone.commands import crawl_check, diff, doctor, init, plan, report, run

# Every command module in the order cli.py registers them, which is the order `footnote --help` lists them.
ALL: tuple[ModuleType, ...] = (init, plan, run, report, diff, crawl_check, doctor)
