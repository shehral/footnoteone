"""footnote init: write footnote.toml and intents.yaml for a site, then build its page library.

With --pages-only it writes neither file and refreshes the library from the footnote.toml already there, so
the youtube_channels and own_domains a user fills in after init are read and their edits are kept. The
refresh merges what discovery finds into the library by canonical URL (Ruling B31): new pages are added,
titles and dates updated, and pages it no longer finds kept, since a channel feed lists only the newest
videos; --prune rebuilds the library from what it finds instead. A discovery that finds nothing leaves an
existing library as it was and says so, with the number of fetches that failed.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from typing import Annotated

import httpx
import typer

from footnoteone.canon import canonicalize
from footnoteone.commands.common import RootOption, _count, fail
from footnoteone.config import ConfigError, SiteConfig, load_config, write_templates
from footnoteone.library import Discovery, discover_pages, merge_pages, read_pages, write_pages
from footnoteone.schema import Page

# The sources discovery reads, in its order, with the words the page count uses for each.
_SOURCES = (("sitemap", "sitemaps"), ("rss", "feeds"), ("youtube", "YouTube"))
NEXT_STEPS = "Next: edit intents.yaml, then run footnote doctor and footnote plan."


def _by_source(pages: list[Page]) -> str:
    counts = Counter(page.source for page in pages)
    return ", ".join(f"{counts[source]} from {words}" for source, words in _SOURCES)


def _found(discovery: Discovery) -> str:
    """What discovery found: the pages by source, or none and how many fetches failed."""
    pages, failed = discovery.pages, discovery.failed_fetches
    if pages:
        return f"Found {_count(len(pages), 'page')}: {_by_source(pages)}"
    if not failed:
        return "Found 0 pages"
    return f"Found 0 pages; {'1 fetch' if failed == 1 else f'{failed} fetches'} failed"


def _site(root: Path) -> SiteConfig:
    """The [site] table of the footnote.toml under `root`; a ConfigError is printed and exits with 2."""
    try:
        return load_config(root).site
    except ConfigError as exc:
        fail(str(exc))


def _existing_site(root: Path, site_url: str, force: bool, no_discover: bool) -> SiteConfig:
    """For --pages-only: the site of the footnote.toml already under `root`, which must be SITE_URL's site."""
    if force or no_discover:
        fail("--pages-only writes only the page library, so it takes neither --force nor --no-discover")
    site = _site(root)
    if canonicalize(site_url) != canonicalize(site.url):  # a spelling of the same URL is the same site
        fail(
            f"{root / 'footnote.toml'} is for {site.url}, not {site_url}; --pages-only finds the pages of "
            "the site footnote.toml names, so pass that URL or change [site] url there first"
        )
    return site


def _pages_only_hint(site_url: str) -> str:
    """The way past a footnote.toml that was there before init and that init will not write over without
    --force, which would overwrite the user's edits."""
    return (
        "\nTo keep your files as they are and rebuild only the page library, "
        f"run footnote init {site_url} --pages-only"
    )


async def _discover(site: SiteConfig, limit: int) -> Discovery:
    async with httpx.AsyncClient(max_redirects=5) as client:  # RFC 9309 2.3.1.2: five redirects, no more
        return await discover_pages(client, site, limit=limit)


def _build_library(root: Path, site: SiteConfig, limit: int, merge: bool) -> None:
    """Find the site's pages and write the library, merged into the one there when `merge`, else in its
    place; a discovery that finds nothing keeps a library that has pages. A library that cannot be read or
    written exits with 2."""
    found = asyncio.run(_discover(site, limit))
    try:
        existing = read_pages(root) if merge or not found.pages else []
        if not found.pages and existing:
            library = root / ".footnote" / "pages.jsonl"
            kept = f"{_count(len(existing), 'page')} in {library}"
            typer.echo(f"{_found(found)}; kept the library as it was ({kept})")
            return
        pages = merge_pages(existing, found.pages) if merge else found.pages
        library = write_pages(root, pages)
    except (OSError, ValueError) as exc:  # a damaged pages.jsonl, or a .footnote that is not a folder
        fail(str(exc))
    held = ""
    if merge:
        known = {canonicalize(page.url) for page in existing}
        new = sum(canonicalize(page.url) not in known for page in found.pages)
        held = f"; the library now holds {_count(len(pages), 'page')} ({new} new)"
    typer.echo(f"{_found(found)}{held}; wrote {library}")


def register(app: typer.Typer) -> None:
    @app.command("init")
    def init(
        site_url: Annotated[
            str, typer.Argument(metavar="SITE_URL", help="Your site's address, such as https://example.org.")
        ],
        root: RootOption,
        force: Annotated[
            bool, typer.Option("--force", help="Overwrite footnote.toml and intents.yaml if they exist.")
        ] = False,
        no_discover: Annotated[
            bool, typer.Option("--no-discover", help="Write the two files only; look for no pages.")
        ] = False,
        limit: Annotated[
            int,
            typer.Option(
                "--limit",
                min=1,
                help="Keep at most this many pages from sitemaps and feeds; the videos of your YouTube "
                "channels come on top.",
            ),
        ] = 500,
        pages_only: Annotated[
            bool,
            typer.Option(
                "--pages-only",
                help="Write neither file; refresh only the page library from the footnote.toml already "
                "there, for example after adding youtube_channels. Pages it finds are merged into the "
                "library and the rest kept.",
            ),
        ] = False,
        prune: Annotated[
            bool,
            typer.Option(
                "--prune",
                help="With --pages-only, rebuild the library from what is found instead of merging.",
            ),
        ] = False,
    ) -> None:
        """Write footnote.toml and intents.yaml for SITE_URL, then find its pages in sitemaps and feeds.

        With --pages-only, keep both files as they are and refresh only the page library from footnote.toml.
        """
        if prune and not pages_only:
            fail("--prune rebuilds the page library, so it works only with --pages-only")
        if pages_only:
            _build_library(root, _existing_site(root, site_url, force, no_discover), limit, merge=not prune)
            typer.echo(NEXT_STEPS)
            return
        there_before = (root / "footnote.toml").exists()
        try:  # a folder that does not exist, or one that cannot be written, is an OSError
            written = write_templates(root, site_url, force=force)
        except (ConfigError, OSError) as exc:  # a written footnote.toml that does not load is a ConfigError
            fail(str(exc) + (_pages_only_hint(site_url) if there_before and not force else ""))
        typer.echo("Wrote " + " and ".join(str(path) for path in written))
        if no_discover:
            typer.echo("Skipped page discovery (--no-discover); no page library was written")
        else:  # the site as the written footnote.toml gives it, so discovery sees what later commands see
            _build_library(root, _site(root), limit, merge=False)
        typer.echo(NEXT_STEPS)
