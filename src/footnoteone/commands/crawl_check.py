"""footnote crawl-check: which AI crawlers robots.txt lets in, and what each one's user agent gets."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import httpx
import typer

from footnoteone.audit.probe import AccessReport, AccessRow, access_matrix
from footnoteone.canon import host_of
from footnoteone.commands.common import RootOption, fail
from footnoteone.config import ConfigError, load_config
from footnoteone.library import read_pages

_LIBRARY_PAGES = 10  # library pages checked beside the home page when no --path is given
_RULE_WIDTH = 60
_COLUMNS = ("bot", "purpose", "path", "robots", "matched rule", "probe")
_FOOTER = (
    "Probe = what a request carrying that user-agent string from this machine receives; "
    "bot-verifying firewalls may answer the real bot differently. "
    "Unreachable robots.txt means complete disallow (RFC 9309)."
)


def _flat(text: str) -> str:
    """One line: runs of whitespace, newlines included, become single spaces."""
    return " ".join(text.split())


def _path_of(url: str) -> str:
    """The path and query of a page URL, as robots.txt rules and the probe see them."""
    parts = urlsplit(url)
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def _default_paths(root: Path, site_url: str) -> list[str]:
    """The home page, then the paths of the first ten library pages on the site's own host, no repeats."""
    host = host_of(site_url)
    pages = [page for page in read_pages(root) if host_of(page.url) == host][:_LIBRARY_PAGES]
    return list(dict.fromkeys(["/", *(_path_of(page.url) for page in pages)]))


async def _check(site_url: str, paths: list[str], probe: bool) -> AccessReport:
    async with httpx.AsyncClient(max_redirects=5) as client:  # RFC 9309 2.3.1.2: five redirects, no more
        return await access_matrix(client, site_url, paths, probe=probe)


def _header(report: AccessReport) -> str:
    if report.robots_status is not None:
        found = f"HTTP {report.robots_status}"
    else:
        found = _flat(report.robots_error or "no response")
    return f"robots.txt: {found}, {report.robots_access}"


def _rule(row: AccessRow) -> str:
    text = _flat(row.matched_rule or "no matching rule")
    return text if len(text) <= _RULE_WIDTH else text[: _RULE_WIDTH - 3] + "..."


def _probe(row: AccessRow) -> str:
    """The status the probe got, else why there is none; `--no-probe` rows have neither."""
    if row.http_status is not None:
        return str(row.http_status)
    return _flat(row.probe_error or "not probed")


def _render(report: AccessReport) -> list[str]:
    body = [
        (
            row.bot,
            row.purpose,
            _flat(row.path),
            "allowed" if row.robots_allowed else "blocked",
            _rule(row),
            _probe(row),
        )
        for row in report.rows
    ]
    widths = [max(len(cells[i]) for cells in [_COLUMNS, *body]) for i in range(len(_COLUMNS))]

    def line(cells: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True)).rstrip()

    underline = line(tuple("-" * width for width in widths))
    return [_header(report), "", line(_COLUMNS), underline, *(line(cells) for cells in body), "", _FOOTER]


def register(app: typer.Typer) -> None:
    @app.command("crawl-check")
    def crawl_check(
        root: RootOption,
        path: Annotated[
            list[str] | None,
            typer.Option(
                "--path",
                help="A path to check, such as /posts/a. Repeat for several. "
                "Default: / and the first ten pages of your library.",
            ),
        ] = None,
        no_probe: Annotated[
            bool, typer.Option("--no-probe", help="Read robots.txt only; send no request to your pages.")
        ] = False,
    ) -> None:
        """Show which AI crawlers robots.txt lets in and what each one's user agent gets from your site."""
        try:
            config = load_config(root)
        except ConfigError as exc:
            fail(str(exc))
        try:  # a path that does not start with "/" and a damaged pages.jsonl are both a ValueError
            paths = path or _default_paths(root, config.site.url)
            report = asyncio.run(_check(config.site.url, paths, probe=not no_probe))
        except (ValueError, httpx.InvalidURL) as exc:
            fail(str(exc))
        for line in _render(report):
            typer.echo(line)
