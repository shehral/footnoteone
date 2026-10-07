import gzip

import httpx
import pytest

from footnoteone import __version__
from footnoteone.config import ProjectConfig
from footnoteone.library import (
    discover_pages,
    feed_links_from_html,
    fetch_bytes,
    merge_pages,
    owned_set_for,
    parse_feed,
    parse_sitemap,
    parse_youtube_feed,
    read_pages,
    sitemaps_from_robots,
    write_pages,
    youtube_channel_id,
)
from footnoteone.schema import Page

SM = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{}</urlset>'
IDX = (
    '<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{}'
    "</sitemapindex>"
)


def loc(url, lastmod=None):
    extra = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
    return f"<url><loc>{url}</loc>{extra}</url>"


def site(**overrides):
    data = {
        "site": {"url": "https://example.org", **overrides},
        "engines": [{"provider": "openai", "model": "gpt-5-mini"}],
        "design": {"budget_usd_per_burst": 1.0},
    }
    return ProjectConfig.model_validate(data)


def test_sitemaps_from_robots_reads_any_case():
    assert sitemaps_from_robots(
        "User-agent: *\nsitemap: https://example.org/a.xml\nSITEMAP: https://example.org/b.xml\n"
    ) == ["https://example.org/a.xml", "https://example.org/b.xml"]


def test_parse_sitemap_handles_urlset_index_and_gzip():
    found, children = parse_sitemap(
        SM.format(loc("https://example.org/a", "2026-01-02") + loc("https://example.org/b")).encode()
    )
    assert [(f.url, f.lastmod) for f in found] == [
        ("https://example.org/a", "2026-01-02"),
        ("https://example.org/b", None),
    ]
    _, kids = parse_sitemap(IDX.format("<sitemap><loc>https://example.org/s1.xml</loc></sitemap>").encode())
    assert kids == ["https://example.org/s1.xml"]
    found, _ = parse_sitemap(gzip.compress(SM.format(loc("https://example.org/z")).encode()))
    assert found[0].url == "https://example.org/z"
    assert parse_sitemap(b"not xml") == ([], [])


def test_parse_feed_rss_and_atom():
    rss = (
        b"<rss><channel><item><title>T</title><link>https://example.org/p1</link>"
        b"<pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate></item></channel></rss>"
    )
    assert [(f.url, f.title) for f in parse_feed(rss)] == [("https://example.org/p1", "T")]
    atom = (
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title>'
        b'<link rel="alternate" href="https://example.org/p2"/>'
        b"<updated>2026-01-03T00:00:00Z</updated></entry></feed>"
    )
    [f] = parse_feed(atom)
    assert (f.url, f.title, f.lastmod, f.source) == (
        "https://example.org/p2",
        "A",
        "2026-01-03T00:00:00Z",
        "rss",
    )


def test_feed_links_from_html_resolves_relative():
    html = (
        '<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml">'
        '<link rel="stylesheet" href="/x.css"></head></html>'
    )
    assert feed_links_from_html(html, "https://example.org/") == ["https://example.org/feed.xml"]


def test_youtube_channel_id_forms():
    assert youtube_channel_id("UC1234567890123456789012") == "UC1234567890123456789012"
    assert (
        youtube_channel_id("https://www.youtube.com/channel/UC1234567890123456789012/videos")
        == "UC1234567890123456789012"
    )
    assert youtube_channel_id("@alichannel") is None


YT = (
    b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry>'
    b"<yt:videoId>dQw4w9WgXcQ</yt:videoId><title>V</title>"
    b"<published>2026-02-01T00:00:00+00:00</published></entry></feed>"
)


def test_parse_youtube_feed():
    [f] = parse_youtube_feed(YT)
    assert (f.url, f.title, f.source) == ("https://youtube.com/watch?v=dQw4w9WgXcQ", "V", "youtube")


async def test_discover_follows_index_filters_hosts_dedupes_and_caps(httpx_mock):
    httpx_mock.add_response(
        url="https://example.org/robots.txt", text="Sitemap: https://example.org/idx.xml\n"
    )
    httpx_mock.add_response(
        url="https://example.org/idx.xml",
        text=IDX.format(
            "<sitemap><loc>https://example.org/s1.xml</loc></sitemap>"
            "<sitemap><loc>https://example.org/s2.xml.gz</loc></sitemap>"
            "<sitemap><loc>https://example.org/idx.xml</loc></sitemap>"
        ),
    )
    httpx_mock.add_response(
        url="https://example.org/s1.xml",
        text=SM.format(
            loc("https://example.org/a/") + loc("https://example.org/a") + loc("https://other.net/x")
        ),
    )
    httpx_mock.add_response(
        url="https://example.org/s2.xml.gz",
        content=gzip.compress(
            SM.format("".join(loc(f"https://example.org/p{i}") for i in range(10))).encode()
        ),
    )
    httpx_mock.add_response(url="https://example.org/", text="<html></html>")
    for path in ("/feed", "/rss.xml", "/atom.xml", "/feed.xml"):
        httpx_mock.add_response(url=f"https://example.org{path}", status_code=404)
    async with httpx.AsyncClient() as client:
        pages = (await discover_pages(client, site().site, limit=5)).pages
    assert [p.canonical_url for p in pages] == [
        "https://example.org/a",
        "https://example.org/p0",
        "https://example.org/p1",
        "https://example.org/p2",
        "https://example.org/p3",
    ]
    assert all(p.source == "sitemap" and p.id == Page.id_for(p.canonical_url) for p in pages)


async def test_discover_falls_back_to_feeds_and_youtube(httpx_mock):
    httpx_mock.add_response(url="https://example.org/robots.txt", status_code=404)
    httpx_mock.add_response(url="https://example.org/sitemap.xml", status_code=404)
    httpx_mock.add_response(url="https://example.org/sitemap_index.xml", status_code=404)
    httpx_mock.add_response(
        url="https://example.org/",
        text='<link rel="alternate" type="application/atom+xml" href="https://example.org/atom">',
    )
    httpx_mock.add_response(
        url="https://example.org/atom",
        content=(
            b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title>'
            b'<link rel="alternate" href="https://example.org/p2"/></entry></feed>'
        ),
    )
    httpx_mock.add_response(
        url="https://www.youtube.com/@alichannel", text='... "channelId":"UC1234567890123456789012" ...'
    )
    httpx_mock.add_response(
        url="https://www.youtube.com/feeds/videos.xml?channel_id=UC1234567890123456789012", content=YT
    )
    async with httpx.AsyncClient() as client:
        pages = (await discover_pages(client, site(youtube_channels=["@alichannel"]).site)).pages
    assert [(p.canonical_url, p.source) for p in pages] == [
        ("https://example.org/p2", "rss"),
        ("https://youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
    ]


def test_write_and_read_pages_round_trip_and_rewrite(tmp_path):
    a = Page(
        id=Page.id_for("https://example.org/a"),
        url="https://example.org/a",
        canonical_url="https://example.org/a",
        source="manual",
    )
    b = Page(
        id=Page.id_for("https://example.org/b"),
        url="https://example.org/b",
        canonical_url="https://example.org/b",
        source="manual",
    )
    write_pages(tmp_path, [a, b])
    assert [p.id for p in read_pages(tmp_path)] == [a.id, b.id]
    write_pages(tmp_path, [b])
    assert [p.id for p in read_pages(tmp_path)] == [b.id]
    assert read_pages(tmp_path / "nowhere") == []


def test_owned_set_for_collects_offsite_pages_and_video_ids():
    cfg = site(offsite_prefixes=["https://medium.com/@ali"])
    pages = [
        Page(id="1", url="https://example.org/a", canonical_url="https://example.org/a", source="sitemap"),
        Page(
            id="2",
            url="https://youtu.be/dQw4w9WgXcQ",
            canonical_url="https://youtube.com/watch?v=dQw4w9WgXcQ",
            source="youtube",
        ),
        Page(id="3", url="https://dev.to/ali/post", canonical_url="https://dev.to/ali/post", source="manual"),
    ]
    owned = owned_set_for(cfg, pages)
    assert owned.domains == ("example.org",) and owned.prefixes == ("https://medium.com/@ali",)
    assert (
        owned.urls == frozenset({"https://dev.to/ali/post"})
        and owned.youtube_video_ids == frozenset({"dQw4w9WgXcQ"})
    )


UA = f"FootnoteOne/{__version__} (+https://github.com/shehral/footnoteone)"


async def test_fetch_bytes_follows_redirects_and_skips_failures_non_2xx_and_oversize(httpx_mock):
    httpx_mock.add_response(
        url="https://example.org/ok", content=b"0123456789", match_headers={"User-Agent": UA}
    )
    httpx_mock.add_response(url="https://example.org/old", status_code=301, headers={"Location": "/new"})
    httpx_mock.add_response(url="https://example.org/new", content=b"moved")
    httpx_mock.add_response(url="https://example.org/big", content=b"0123456789!")
    httpx_mock.add_response(url="https://example.org/gone", status_code=410)
    httpx_mock.add_exception(httpx.ConnectError("refused"), url="https://example.org/down")
    async with httpx.AsyncClient() as client:
        assert await fetch_bytes(client, "https://example.org/ok", max_bytes=10) == b"0123456789"
        assert await fetch_bytes(client, "https://example.org/old") == b"moved"
        assert await fetch_bytes(client, "https://example.org/big", max_bytes=10) is None
        assert await fetch_bytes(client, "https://example.org/gone") is None
        assert await fetch_bytes(client, "https://example.org/down") is None
        assert await fetch_bytes(client, "https://[::1/sitemap.xml") is None  # malformed: nothing is sent


async def test_discover_fetches_at_most_50_sitemaps(httpx_mock):
    httpx_mock.add_response(
        url="https://example.org/robots.txt", text="Sitemap: https://example.org/s0.xml\n"
    )
    for i in range(50):  # each index names the next; s50.xml would be the 51st fetch, so it is never sent
        child = f"<sitemap><loc>https://example.org/s{i + 1}.xml</loc></sitemap>"
        httpx_mock.add_response(url=f"https://example.org/s{i}.xml", text=IDX.format(child))
    httpx_mock.add_response(url="https://example.org/", text="<html></html>")
    for path in ("/feed", "/rss.xml", "/atom.xml", "/feed.xml"):
        httpx_mock.add_response(url=f"https://example.org{path}", status_code=404)
    async with httpx.AsyncClient() as client:
        assert (await discover_pages(client, site().site)).pages == []


def test_read_pages_of_a_missing_library_creates_nothing(tmp_path):
    assert read_pages(tmp_path) == []
    assert not (tmp_path / ".footnote").exists()


def test_parsers_never_raise_and_skip_what_they_cannot_read():
    padded = SM.format(loc("https://example.org/a") + " " * 60_000_000).encode()  # valid, past 50 MiB
    for data in (
        b"",
        b"\x1f\x8b\x08 not gzip",
        gzip.compress(padded, compresslevel=1),
        b'<?xml version="1.0" encoding="nope"?><urlset/>',  # LookupError inside expat
        b'<?xml version="1.0" encoding="shift_jis"?><urlset/>',  # ValueError inside expat
    ):
        assert parse_sitemap(data) == ([], [])
        assert parse_feed(data) == []
        assert parse_youtube_feed(data) == []
    assert parse_sitemap(SM.format("").encode()) == ([], [])  # no <loc> elements is not an error
    assert feed_links_from_html("<![ x [", "https://example.org/") == []  # AssertionError on Python 3.12
    bad_href = '<link rel="alternate" type="application/rss+xml" href="https://[::1">'
    assert feed_links_from_html(bad_href, "https://example.org/") == []  # urljoin raises ValueError


def no_feeds(httpx_mock):
    """The home page links no feed and none of the fallback feeds exists."""
    httpx_mock.add_response(url="https://example.org/", text="<html></html>")
    for path in ("/feed", "/rss.xml", "/atom.xml", "/feed.xml"):
        httpx_mock.add_response(url=f"https://example.org{path}", status_code=404)


async def test_discover_tries_the_default_sitemaps_when_the_named_ones_fail(httpx_mock):
    httpx_mock.add_response(
        url="https://example.org/robots.txt", text="Sitemap: https://example.org/old-sitemap.xml\n"
    )
    httpx_mock.add_response(url="https://example.org/old-sitemap.xml", status_code=404)
    httpx_mock.add_response(
        url="https://example.org/sitemap.xml", text=SM.format(loc("https://example.org/a"))
    )
    httpx_mock.add_response(url="https://example.org/sitemap_index.xml", status_code=404)
    no_feeds(httpx_mock)
    async with httpx.AsyncClient() as client:
        pages = (await discover_pages(client, site().site)).pages
    assert [p.canonical_url for p in pages] == ["https://example.org/a"]


async def test_discover_tries_the_default_sitemaps_when_the_named_ones_list_no_own_page(httpx_mock):
    # robots.txt names /sitemap.xml itself, and it lists only another site's page. The walk goes on to
    # /sitemap_index.xml and does not fetch /sitemap.xml again: pytest-httpx serves each response once.
    httpx_mock.add_response(
        url="https://example.org/robots.txt", text="Sitemap: https://example.org/sitemap.xml\n"
    )
    httpx_mock.add_response(url="https://example.org/sitemap.xml", text=SM.format(loc("https://other.net/x")))
    httpx_mock.add_response(
        url="https://example.org/sitemap_index.xml",
        text=IDX.format("<sitemap><loc>https://example.org/posts.xml</loc></sitemap>"),
    )
    httpx_mock.add_response(url="https://example.org/posts.xml", text=SM.format(loc("https://example.org/b")))
    no_feeds(httpx_mock)
    async with httpx.AsyncClient() as client:
        pages = (await discover_pages(client, site().site)).pages
    assert [p.canonical_url for p in pages] == ["https://example.org/b"]


async def test_discover_counts_the_default_sitemaps_toward_the_50_fetch_cap(httpx_mock):
    httpx_mock.add_response(
        url="https://example.org/robots.txt", text="Sitemap: https://example.org/s0.xml\n"
    )
    for i in range(49):  # 49 named fetches and no page: the last index names no child
        child = f"<sitemap><loc>https://example.org/s{i + 1}.xml</loc></sitemap>" if i < 48 else ""
        httpx_mock.add_response(url=f"https://example.org/s{i}.xml", text=IDX.format(child))
    httpx_mock.add_response(
        url="https://example.org/sitemap.xml", text=SM.format(loc("https://example.org/a"))
    )  # the 50th fetch; /sitemap_index.xml would be the 51st, so it is never sent
    no_feeds(httpx_mock)
    async with httpx.AsyncClient() as client:
        pages = (await discover_pages(client, site().site)).pages
    assert [p.canonical_url for p in pages] == ["https://example.org/a"]


OWNER, FEATURED = "UC" + "o" * 22, "UC" + "f" * 22
CANONICAL = f'<link rel="canonical" href="https://www.youtube.com/channel/{OWNER}">'


# Live channel pages (checked 2026-10-06) name their own channel in the canonical link and in "externalId".
# "channelId" named only featured or related channels there, and some pages had none.
@pytest.mark.parametrize(
    "page",
    [
        f'{CANONICAL} ... "channelId":"{FEATURED}" ... "externalId":"{OWNER}" ...',
        f'... "channelId":"{FEATURED}" ... "externalId":"{OWNER}" ...',
        f"{CANONICAL} ...",
    ],
    ids=["as-served", "external-id", "canonical-only"],
)
async def test_discover_reads_the_channel_id_the_page_names_as_its_own(httpx_mock, page):
    for path in ("/robots.txt", "/sitemap.xml", "/sitemap_index.xml"):
        httpx_mock.add_response(url=f"https://example.org{path}", status_code=404)
    no_feeds(httpx_mock)
    httpx_mock.add_response(url="https://www.youtube.com/@ali", text=page)
    httpx_mock.add_response(url=f"https://www.youtube.com/feeds/videos.xml?channel_id={OWNER}", content=YT)
    async with httpx.AsyncClient() as client:
        pages = (await discover_pages(client, site(youtube_channels=["@ali"]).site)).pages
    assert [p.canonical_url for p in pages] == ["https://youtube.com/watch?v=dQw4w9WgXcQ"]


# E1 (Ruling B9): the limit caps sitemap and feed pages only; a channel's videos (at most 15 from its feed)
# come on top, so owned video ids survive on a large site.


def channel_feed(n):
    entries = "".join(
        f"<entry><yt:videoId>video{k:06d}</yt:videoId><title>V{k}</title></entry>" for k in range(n)
    )
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
        f"{entries}</feed>"
    ).encode()


async def test_the_limit_caps_site_pages_and_keeps_every_video_beyond_it(httpx_mock):
    httpx_mock.add_response(url="https://example.org/robots.txt", status_code=404)
    urls = "".join(loc(f"https://example.org/p{i}") for i in range(600))
    httpx_mock.add_response(url="https://example.org/sitemap.xml", text=SM.format(urls))
    httpx_mock.add_response(url="https://example.org/sitemap_index.xml", status_code=404)
    no_feeds(httpx_mock)
    channel = "UC1234567890123456789012"
    httpx_mock.add_response(
        url=f"https://www.youtube.com/feeds/videos.xml?channel_id={channel}", content=channel_feed(3)
    )
    async with httpx.AsyncClient() as client:
        found = await discover_pages(client, site(youtube_channels=[channel]).site, limit=500)
    sources = [page.source for page in found.pages]
    assert sources.count("sitemap") == 500 and sources.count("youtube") == 3 and len(found.pages) == 503
    assert found.pages[-1].canonical_url == "https://youtube.com/watch?v=video000002"


# E3: discovery says how many fetches failed, so init can say why a library came back empty.


async def test_discovery_counts_the_fetches_that_failed(httpx_mock):
    for path in ("/robots.txt", "/sitemap.xml", "/sitemap_index.xml", "/"):
        httpx_mock.add_response(url=f"https://example.org{path}", status_code=503)
    for path in ("/feed", "/rss.xml", "/atom.xml"):
        httpx_mock.add_response(url=f"https://example.org{path}", status_code=404)
    httpx_mock.add_exception(httpx.ConnectError("refused"), url="https://example.org/feed.xml")
    async with httpx.AsyncClient() as client:
        found = await discover_pages(client, site().site)
    assert found.pages == [] and found.failed_fetches == 8


async def test_a_readable_site_has_its_failed_fetches_counted_too(httpx_mock):
    httpx_mock.add_response(url="https://example.org/robots.txt", status_code=404)
    httpx_mock.add_response(url="https://example.org/sitemap.xml", text=SM.format(loc("https://example.org/a")))
    httpx_mock.add_response(url="https://example.org/sitemap_index.xml", status_code=404)
    no_feeds(httpx_mock)
    async with httpx.AsyncClient() as client:
        found = await discover_pages(client, site().site)
    # robots.txt, /sitemap_index.xml and the four feed paths answer 404
    assert [p.canonical_url for p in found.pages] == ["https://example.org/a"] and found.failed_fetches == 6


# E2 (Ruling B31): merge_pages, the library after a refresh that keeps what it no longer finds.


def a_page(url, title=None, lastmod=None, source="sitemap"):
    return Page(id=Page.id_for(url), url=url, canonical_url=url, title=title, source=source, lastmod=lastmod)


def test_merge_pages_keeps_every_page_refreshes_titles_and_dates_and_appends_new_ones():
    old_a = a_page("https://example.org/a", title="Old A", lastmod="2026-01-01")
    video = a_page("https://youtube.com/watch?v=OLDvideo123", title="V", source="youtube")
    fresh_a = a_page("https://www.example.org/a/", title="New A", lastmod="2026-10-01")  # another spelling
    untitled_a = a_page("https://example.org/a", lastmod="2026-10-02")
    new = a_page("https://example.org/c", title="C")
    merged = merge_pages([old_a, video], [fresh_a, new])
    assert [p.url for p in merged] == ["https://example.org/a", video.url, "https://example.org/c"]
    assert (merged[0].id, merged[0].title, merged[0].lastmod) == (old_a.id, "New A", "2026-10-01")
    assert merged[0].discovered_at == old_a.discovered_at and merged[1] == video and merged[2] == new
    kept_title = merge_pages([old_a], [untitled_a])[0]  # a source with no title keeps the one there was
    assert (kept_title.title, kept_title.lastmod) == ("Old A", "2026-10-02")
    assert merge_pages([], [new]) == [new] and merge_pages([old_a], []) == [old_a]


# E5: a site on a shared platform owns no domain, so discovery reads its feed for the pages under its prefix
# and never walks the platform's own sitemaps (pytest-httpx fails on any request left unregistered).


async def test_a_shared_platform_site_reads_its_feed_and_never_the_platform_sitemaps(httpx_mock):
    profile = "https://medium.com/@ali"
    feed = '<link rel="alternate" type="application/rss+xml" href="https://medium.com/feed/@ali">'
    httpx_mock.add_response(url=profile, text=feed)
    items = "".join(
        f"<item><title>{name}</title><link>https://medium.com/{name}?source=rss</link></item>"
        for name in ("@ali/my-post-1", "@bob/his-post-2")
    )
    httpx_mock.add_response(url="https://medium.com/feed/@ali", text=f"<rss><channel>{items}</channel></rss>")
    shared = ProjectConfig.model_validate(
        {
            "site": {"url": profile, "own_domains": [], "offsite_prefixes": [profile]},
            "engines": [{"provider": "openai", "model": "gpt-5-mini"}],
            "design": {"budget_usd_per_burst": 1.0},
        }
    )
    async with httpx.AsyncClient() as client:
        found = await discover_pages(client, shared.site)
    assert [(p.canonical_url, p.source) for p in found.pages] == [
        ("https://medium.com/@ali/my-post-1", "rss")
    ]
