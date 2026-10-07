"""The page library: the creator's pages, found through sitemaps, RSS and Atom feeds and YouTube channel
feeds, and kept in `.footnote/pages.jsonl`.

Parsers are pure and never raise: input they cannot read gives an empty result. Fetches never raise either:
one that fails, ends outside 2xx or brings a body over its byte cap gives None and is skipped.
"""

from __future__ import annotations

import contextlib
import gzip
import io
import os
import re
import tempfile
import xml.etree.ElementTree as ET
import zlib
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from footnoteone import __version__
from footnoteone.canon import OwnedSet, canonicalize, classify_owner, host_of, youtube_video_id
from footnoteone.config import ProjectConfig, SiteConfig
from footnoteone.schema import Page, PageSource, utcnow
from footnoteone.store import JsonlStore

USER_AGENT = f"FootnoteOne/{__version__} (+https://github.com/shehral/footnoteone)"
MAX_SITEMAP_FETCHES = 50
# sitemaps.org caps an uncompressed sitemap at 50 MiB. A gzip body that inflates past that is skipped, so a
# small compressed body cannot expand without bound.
MAX_INFLATED_BYTES = 52_428_800
GZIP_MAGIC = b"\x1f\x8b"
SITEMAP_PATHS = ("/sitemap.xml", "/sitemap_index.xml")
FEED_PATHS = ("/feed", "/rss.xml", "/atom.xml", "/feed.xml")
FEED_TYPES = frozenset({"application/rss+xml", "application/atom+xml"})
YOUTUBE_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
WATCH_URL = "https://youtube.com/watch?v="  # canonicalize's form of a YouTube video URL
LASTMOD_TAGS = ("updated", "published", "pubDate", "date")  # Atom, then RSS 2.0, then dc:date
_CHANNEL_ID = re.compile(r"UC[\w-]{22}", re.ASCII)
_CHANNEL_PATH = re.compile(r"(?:^|/)channel/(UC[\w-]{22})(?:/|$)", re.ASCII)
# Where a channel page names its own id, most specific first. On the live pages checked on 2026-10-06 the
# canonical link and "externalId" named the page's own channel, while "channelId" (the plan's pattern) named
# only featured and related channels, or did not appear at all, so it is the last resort.
_OWN_CHANNEL_IN_PAGE = tuple(
    re.compile(pattern, re.ASCII)
    for pattern in (
        r'<link\s+rel="canonical"\s+href="https?://(?:www\.|m\.)?youtube\.com/channel/(UC[\w-]{22})"',
        r'"externalId":"(UC[\w-]{22})"',
        r'"channelId":"(UC[\w-]{22})"',
    )
)


@dataclass(frozen=True)
class Found:
    """One page as a sitemap, feed or channel feed lists it, before the host filter and dedupe. `lastmod` is
    the date text exactly as the source gives it (sitemap lastmod, Atom updated or published, RSS pubDate),
    or None."""

    url: str
    title: str | None
    lastmod: str | None
    source: PageSource


def _local(tag: str) -> str:
    """An element's local name: `{namespace}loc` and `loc` are both `loc`."""
    return tag.rsplit("}", 1)[-1]


def _text(element: ET.Element) -> str | None:
    return "".join(element.itertext()).strip() or None


def _child_text(element: ET.Element, *names: str) -> str | None:
    """Text of the first direct child whose local name is in `names` and whose text is not blank, trying the
    names in order."""
    for name in names:
        for child in element:
            if _local(child.tag) == name and (text := _text(child)):
                return text
    return None


def _inflate(data: bytes) -> bytes | None:
    """`data`, gunzipped when it starts with the gzip magic bytes; None when it does not inflate or inflates
    past MAX_INFLATED_BYTES."""
    if not data.startswith(GZIP_MAGIC):
        return data
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as fh:
            body = fh.read(MAX_INFLATED_BYTES + 1)
    except (OSError, EOFError, zlib.error):  # BadGzipFile is an OSError; a truncated stream is an EOFError
        return None
    return body if len(body) <= MAX_INFLATED_BYTES else None


def _xml_root(data: bytes) -> ET.Element | None:
    body = _inflate(data)
    if body is None:
        return None
    try:
        return ET.fromstring(body)
    except (ET.ParseError, LookupError, ValueError):  # expat raises the last two for some declared encodings
        return None


def parse_sitemap(data: bytes) -> tuple[list[Found], list[str]]:
    """A urlset's pages and a sitemapindex's child sitemap URLs, in document order, gzip inflated first.

    Only the direct `<loc>` of each `<url>` or `<sitemap>` counts, so an image:loc inside a `<url>` is not a
    page. A sitemap with no `<loc>` elements, and input that is not a sitemap, both give ([], []).
    """
    root = _xml_root(data)
    if root is None:
        return [], []
    kind = _local(root.tag)
    if kind == "urlset":
        found = [
            Found(url, None, _child_text(entry, "lastmod"), "sitemap")
            for entry in root
            if _local(entry.tag) == "url" and (url := _child_text(entry, "loc"))
        ]
        return found, []
    if kind == "sitemapindex":
        children = [
            url for entry in root if _local(entry.tag) == "sitemap" and (url := _child_text(entry, "loc"))
        ]
        return [], children
    return [], []


def _entry_url(entry: ET.Element) -> str | None:
    """An RSS item's `<link>` text, or the href of an Atom entry's alternate `<link>` (rel defaults to
    alternate, as in RFC 4287)."""
    for child in entry:
        if _local(child.tag) != "link":
            continue
        href = (child.get("href") or "").strip()
        if href:
            if (child.get("rel") or "alternate").strip().lower() == "alternate":
                return href
        elif text := _text(child):
            return text
    return None


def parse_feed(data: bytes) -> list[Found]:
    """The entries of an RSS (0.9x, 1.0 or 2.0) or Atom feed in document order, each with source "rss".

    An entry without a URL is skipped. lastmod is the first of Atom updated and published, RSS pubDate and
    dc:date. Input that is not a feed gives [].
    """
    root = _xml_root(data)
    if root is None:
        return []
    return [
        Found(url, _child_text(entry, "title"), _child_text(entry, *LASTMOD_TAGS), "rss")
        for entry in root.iter()
        if _local(entry.tag) in ("item", "entry") and (url := _entry_url(entry))
    ]


def parse_youtube_feed(data: bytes) -> list[Found]:
    """The videos of a YouTube channel feed in document order, each as https://youtube.com/watch?v=<id> (the
    canonical form) with source "youtube". An entry whose yt:videoId is not a video id is skipped. Input
    that is not a feed gives []."""
    root = _xml_root(data)
    if root is None:
        return []
    found = []
    for entry in root.iter():
        if _local(entry.tag) != "entry":
            continue
        video = _child_text(entry, "videoId")
        if video and youtube_video_id(WATCH_URL + video) == video:
            title, lastmod = _child_text(entry, "title"), _child_text(entry, *LASTMOD_TAGS)
            found.append(Found(WATCH_URL + video, title, lastmod, "youtube"))
    return found


def _web_url(url: str) -> str | None:
    """`url` when it is an http or https URL with a host, else None. Never raises."""
    try:
        parts = urlsplit(url)
        usable = parts.scheme in ("http", "https") and bool(parts.hostname)
    except ValueError:  # an unbalanced IPv6 bracket
        return None
    return url if usable else None


def _join(base: str, ref: str) -> str | None:
    """`ref` resolved against `base` when that gives an http or https URL, else None. Never raises."""
    try:
        return _web_url(urljoin(base, ref.strip()))
    except ValueError:
        return None


class _FeedLinks(HTMLParser):
    """Collects the href of every `<link rel="alternate">` whose type is an RSS or Atom feed."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        values: dict[str, str] = {}
        for name, value in attrs:
            values.setdefault(name, value or "")  # the first of a repeated attribute wins, as in browsers
        rels = values.get("rel", "").lower().split()
        kind = values.get("type", "").split(";", 1)[0].strip().lower()
        href = values.get("href", "").strip()
        if "alternate" in rels and kind in FEED_TYPES and href:
            self.hrefs.append(href)


def feed_links_from_html(html_text: str, base_url: str) -> list[str]:
    """The feeds a page advertises with `<link rel="alternate" type="application/rss+xml">` (or
    application/atom+xml), resolved against `base_url`, in page order without repeats.

    Only http and https URLs are kept. Malformed HTML gives the links found before the parser stopped.
    """
    parser = _FeedLinks()
    # Python 3.12's parser raises AssertionError on some malformed markup, such as "<![ x [".
    with contextlib.suppress(AssertionError):
        parser.feed(html_text)
        parser.close()
    links = (_join(base_url, href) for href in parser.hrefs)
    return list(dict.fromkeys(link for link in links if link))


def youtube_channel_id(ref: str) -> str | None:
    """The channel id a reference names without a fetch: the id itself (UC plus 22 characters) or a URL
    whose path holds /channel/<id>. A handle, a custom URL and anything else give None."""
    ref = ref.strip()
    if _CHANNEL_ID.fullmatch(ref):
        return ref
    try:
        path = urlsplit(ref).path
    except ValueError:
        return None
    match = _CHANNEL_PATH.search(path)
    return match.group(1) if match else None


def sitemaps_from_robots(text: str) -> list[str]:
    """Sitemap URLs from robots.txt `Sitemap:` lines in file order, whatever the key's case, with comments
    dropped and repeats removed."""
    urls = []
    for line in text.removeprefix("\ufeff").splitlines():
        key, colon, value = line.split("#", 1)[0].partition(":")
        if colon and key.strip().lower() == "sitemap" and value.strip():
            urls.append(value.strip())
    return list(dict.fromkeys(urls))


async def fetch_bytes(client: httpx.AsyncClient, url: str, max_bytes: int = 10_000_000) -> bytes | None:
    """GET `url` following redirects, with FootnoteOne's user agent and a 20 s timeout.

    Returns the body, or None when the request fails, the final status is not 2xx or the body is over
    `max_bytes`. The body is read whole before its size is checked.
    """
    try:
        resp = await client.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=20.0)
    except (httpx.HTTPError, httpx.InvalidURL, ValueError):
        # A malformed URL from robots.txt, a sitemap index or a feed link fails while the request is built
        # (InvalidURL) or sent (a ValueError such as a bad IDNA label): a failed fetch like any other.
        return None
    if not resp.is_success or len(resp.content) > max_bytes:
        return None
    return resp.content


def _home_url(site_url: str) -> str:
    """The site URL with an empty path written as "/"."""
    parts = urlsplit(site_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def _on_own_host(url: str, domains: Iterable[str]) -> bool:
    """True when the URL's host is one of `domains` or a subdomain of one: canon.classify_owner's own_site
    rule."""
    host = host_of(url)
    return bool(host) and any(host == d or host.endswith("." + d) for d in domains)


Fetch = Callable[[str], Awaitable[bytes | None]]  # fetch_bytes with its client bound, as discovery counts it


async def _sitemap_batches(fetch: Fetch, home: str, own: tuple[str, ...]) -> AsyncIterator[list[Found]]:
    """The pages of each sitemap, breadth-first through sitemap indexes. The walk starts from robots.txt's
    Sitemap lines and goes on to /sitemap.xml and /sitemap_index.xml when robots.txt names none, or when
    the sitemaps it names (with their children) list no page on an own host. No URL is fetched twice, and
    at most MAX_SITEMAP_FETCHES are fetched in all, the defaults included."""
    robots_url = urljoin(home, "/robots.txt")
    robots = await fetch(robots_url)
    named = sitemaps_from_robots(robots.decode("utf-8", "replace")) if robots is not None else []
    queue = deque(url for url in (_join(robots_url, ref) for ref in named) if url)
    defaults = [urljoin(home, path) for path in SITEMAP_PATHS]
    visited: set[str] = set()
    found_own = False
    while len(visited) < MAX_SITEMAP_FETCHES:
        if not queue:
            if found_own or not defaults:
                return
            queue.extend(defaults)  # once, when the named sitemaps and their children are done
            defaults = []
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        data = await fetch(url)
        if data is None:
            continue
        found, children = parse_sitemap(data)
        found_own = found_own or any(_on_own_host(item.url, own) for item in found)
        resolved = (_join(url, ref) for ref in children)
        queue.extend(child for child in resolved if child)
        yield found


async def _feed_batches(fetch: Fetch, home: str) -> AsyncIterator[list[Found]]:
    """The entries of each feed the home page links to, or of /feed, /rss.xml, /atom.xml and /feed.xml when
    it links none."""
    page = await fetch(home)
    linked = feed_links_from_html(page.decode("utf-8", "replace"), home) if page is not None else []
    for url in linked or [urljoin(home, path) for path in FEED_PATHS]:
        data = await fetch(url)
        if data is not None:
            yield parse_feed(data)


def _channel_page(ref: str) -> str | None:
    """The page to read a channel reference's id from: a handle's page on youtube.com (a bare word is taken
    as a handle), or the reference itself when it is a URL, with https:// added when the scheme is
    missing."""
    ref = ref.strip()
    if not ref:
        return None
    if ref.startswith("@"):
        return "https://www.youtube.com/" + ref
    if "/" in ref:
        return _web_url(ref if "://" in ref else "https://" + ref)
    return "https://www.youtube.com/@" + ref


async def _channel_id(fetch: Fetch, ref: str) -> str | None:
    """youtube_channel_id(ref), else the id the reference's channel page names as its own: the /channel/<id>
    of its canonical link, else its "externalId", else its first "channelId"."""
    channel = youtube_channel_id(ref)
    if channel is not None:
        return channel
    url = _channel_page(ref)
    page = await fetch(url) if url else None
    if page is None:
        return None
    text = page.decode("utf-8", "replace")
    for pattern in _OWN_CHANNEL_IN_PAGE:
        if match := pattern.search(text):
            return match.group(1)
    return None


async def _youtube_batches(fetch: Fetch, refs: list[str]) -> AsyncIterator[list[Found]]:
    """The videos in each channel's feed. A channel that several references name is read once."""
    seen: set[str] = set()
    for ref in refs:
        channel = await _channel_id(fetch, ref)
        if channel is None or channel in seen:
            continue
        seen.add(channel)
        data = await fetch(YOUTUBE_FEED.format(channel))
        if data is not None:
            yield parse_youtube_feed(data)


@dataclass(frozen=True)
class Discovery:
    """What discover_pages found, and how many of its fetches failed (no response, a status outside 2xx, or
    a body over its byte cap), so a caller can say why a library came back empty."""

    pages: list[Page]
    failed_fetches: int


async def discover_pages(
    client: httpx.AsyncClient, site: SiteConfig, limit: int = 500, clock: Callable[[], datetime] = utcnow
) -> Discovery:
    """The site's page library in discovery order, deduplicated by canonical URL (the first wins): sitemap
    pages, then feed entries, at most `limit` of the two together, then every YouTube video of the listed
    channels on top of them (Ruling B9: a channel feed lists at most its 15 newest videos, and keeping
    them past the limit keeps owned video ids on large sites).

    Sitemap and feed pages are kept only when they are the user's: on one of `site.own_domains` (or a
    subdomain of one) or under one of `site.offsite_prefixes`. With no own domain, a site on a shared
    platform (Ruling B27), the sitemap walk is skipped: robots.txt there names the platform's sitemaps, which
    list other creators. YouTube videos are kept whatever their host. Every source is read even when
    earlier ones already fill `limit`. A fetch that fails is skipped and counted, so a site with nothing
    readable gives no page and says how many fetches failed.
    """
    own = OwnedSet.build(site.own_domains, ()).domains  # normalized as the owned set normalizes them
    owned = OwnedSet.build(site.own_domains, site.offsite_prefixes)
    home = _home_url(site.url)
    failed = 0

    async def fetch(url: str) -> bytes | None:
        nonlocal failed
        data = await fetch_bytes(client, url)
        if data is None:
            failed += 1
        return data

    picked: dict[str, Found] = {}
    counted = 0  # sitemap and feed pages picked: only these count toward `limit`
    sources = [(_sitemap_batches(fetch, home, own), True)] if own else []
    sources += [(_feed_batches(fetch, home), True), (_youtube_batches(fetch, site.youtube_channels), False)]
    for batches, site_pages in sources:
        async for found in batches:
            for item in found:
                key = canonicalize(item.url)
                if key in picked:
                    continue
                if site_pages:
                    if counted >= limit or classify_owner(item.url, owned) == "other":
                        continue
                    counted += 1
                picked[key] = item
    now = clock()
    pages = [
        Page(
            id=Page.id_for(key),
            url=item.url,
            canonical_url=key,
            title=item.title,
            source=item.source,
            lastmod=item.lastmod,
            discovered_at=now,
        )
        for key, item in picked.items()
    ]
    return Discovery(pages, failed)


def write_pages(root: Path, pages: list[Page]) -> Path:
    """Replace `.footnote/pages.jsonl` under `root` with `pages`, one JSON line each, and return its path.

    The library is derived data, so it is rewritten rather than appended. The write is atomic: a temp file in
    the same directory is flushed to disk and then renamed over the old file, so a reader sees the old
    library or the new one, never a mix.
    """
    store = JsonlStore(Path(root) / ".footnote")
    path = store.path("pages")
    with tempfile.NamedTemporaryFile("wb", dir=store.root, suffix=".tmp", delete_on_close=False) as fh:
        for page in pages:
            fh.write((page.model_dump_json() + "\n").encode())
        fh.flush()
        os.fsync(fh.fileno())
        fh.close()
        os.replace(fh.name, path)
    return path


def merge_pages(existing: list[Page], found: list[Page]) -> list[Page]:
    """The library after a refresh that keeps what it no longer finds (Ruling B31): every existing page, in
    its order and with its id and discovered_at, taking the title and lastmod a fresh discovery gives it
    (each kept when the discovery gives none), then each page found anew in discovery order. Pages match by
    canonical URL recomputed from Page.url with the current rules, since the stored canonical_url is a
    cache; a refreshed page stores that key. A channel feed lists only its newest videos, so the older ones
    a library already holds stay owned."""
    fresh = {canonicalize(page.url): page for page in found}
    merged: list[Page] = []
    seen: set[str] = set()
    for page in existing:
        key = canonicalize(page.url)
        if key in seen:
            continue
        seen.add(key)
        if key in fresh:
            update = {"canonical_url": key, "title": fresh[key].title or page.title}
            update["lastmod"] = fresh[key].lastmod or page.lastmod
            page = page.model_copy(update=update)
        merged.append(page)
    merged += [page for key, page in fresh.items() if key not in seen]
    return merged


def read_pages(root: Path) -> list[Page]:
    """The library in file order; [] when `.footnote/pages.jsonl` under `root` does not exist. A read never
    creates `.footnote/`."""
    directory = Path(root) / ".footnote"
    if not directory.is_dir():
        return []
    return list(JsonlStore(directory).iter("pages", Page))


def owned_set_for(config: ProjectConfig, pages: list[Page]) -> OwnedSet:
    """The owned set from the config's own domains and off-site prefixes, plus each library page off those
    domains (manual or feed-found off-site posts): a YouTube video by its video id, anything else by its
    exact URL. Pages are keyed from `Page.url`, canonicalized afresh, because the stored canonical_url is a
    cache (METRICS.md 0.2.0)."""
    site = config.site
    own = OwnedSet.build(site.own_domains, ()).domains
    urls: list[str] = []
    videos: list[str] = []
    for page in pages:
        if _on_own_host(page.url, own):
            continue
        video = youtube_video_id(page.url)
        if video:
            videos.append(video)
        else:
            urls.append(page.url)
    return OwnedSet.build(site.own_domains, site.offsite_prefixes, urls, videos)
