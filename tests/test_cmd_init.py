from itertools import pairwise

import pytest
import typer
from typer.testing import CliRunner

from footnoteone import config as config_module
from footnoteone.canon import canonicalize
from footnoteone.commands.init import register
from footnoteone.config import write_templates
from footnoteone.library import read_pages, write_pages
from footnoteone.schema import Page

SM = (
    '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<url><loc>https://example.org/a</loc></url><url><loc>https://example.org/b</loc></url></urlset>"
)


def app_with_command():
    app = typer.Typer()
    register(app)
    return app


def test_init_writes_templates_and_discovers_pages(tmp_path, httpx_mock):
    httpx_mock.add_response(url="https://example.org/robots.txt", status_code=404)
    httpx_mock.add_response(url="https://example.org/sitemap.xml", text=SM)
    httpx_mock.add_response(url="https://example.org/sitemap_index.xml", status_code=404)
    httpx_mock.add_response(url="https://example.org/", text="<html></html>")
    for path in ("/feed", "/rss.xml", "/atom.xml", "/feed.xml"):
        httpx_mock.add_response(url=f"https://example.org{path}", status_code=404)
    result = CliRunner().invoke(app_with_command(), ["https://example.org", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "footnote.toml").exists() and (tmp_path / "intents.yaml").exists()
    assert len(read_pages(tmp_path)) == 2 and "2 pages" in result.output and "intents.yaml" in result.output


def test_init_refuses_to_overwrite_without_force(tmp_path):
    first = CliRunner().invoke(
        app_with_command(), ["https://example.org", "--root", str(tmp_path), "--no-discover"]
    )
    assert first.exit_code == 0 and read_pages(tmp_path) == []
    second = CliRunner().invoke(
        app_with_command(), ["https://example.org", "--root", str(tmp_path), "--no-discover"]
    )
    assert second.exit_code == 2 and "exists" in (second.output + str(second.exception or ""))
    third = CliRunner().invoke(
        app_with_command(), ["https://example.org", "--root", str(tmp_path), "--no-discover", "--force"]
    )
    assert third.exit_code == 0


# The tests below pin behaviour lines of the brief that the tests above leave unexercised.

NEXT_STEPS = "Next: edit intents.yaml, then run footnote doctor and footnote plan."
RSS = (
    '<?xml version="1.0"?><rss version="2.0"><channel><title>Blog</title>'
    "<item><title>Post</title><link>https://example.org/post</link></item></channel></rss>"
)
FEED_LINK = '<html><head><link rel="alternate" type="application/rss+xml" href="/rss"></head></html>'


def site_with_a_sitemap(httpx_mock):
    """The site of the first test: robots.txt and the feeds missing, /sitemap.xml listing two pages."""
    httpx_mock.add_response(url="https://example.org/robots.txt", status_code=404)
    httpx_mock.add_response(url="https://example.org/sitemap.xml", text=SM)
    httpx_mock.add_response(url="https://example.org/sitemap_index.xml", status_code=404)
    httpx_mock.add_response(url="https://example.org/", text="<html></html>")
    for path in ("/feed", "/rss.xml", "/atom.xml", "/feed.xml"):
        httpx_mock.add_response(url=f"https://example.org{path}", status_code=404)


def test_init_prints_the_written_files_the_pages_by_source_and_the_next_steps(tmp_path, httpx_mock):
    httpx_mock.add_response(url="https://example.org/robots.txt", status_code=404)
    httpx_mock.add_response(url="https://example.org/sitemap.xml", text=SM)
    httpx_mock.add_response(url="https://example.org/sitemap_index.xml", status_code=404)
    httpx_mock.add_response(url="https://example.org/", text=FEED_LINK)
    httpx_mock.add_response(url="https://example.org/rss", text=RSS)
    result = CliRunner().invoke(app_with_command(), ["https://example.org", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    library = tmp_path / ".footnote" / "pages.jsonl"
    assert result.output.splitlines() == [
        f"Wrote {tmp_path / 'footnote.toml'} and {tmp_path / 'intents.yaml'}",
        f"Found 3 pages: 2 from sitemaps, 1 from feeds, 0 from YouTube; wrote {library}",
        NEXT_STEPS,
    ]
    assert [page.source for page in read_pages(tmp_path)] == ["sitemap", "sitemap", "rss"]
    assert "\u2014" not in result.output


def test_init_without_discovery_sends_no_request_and_writes_no_library(tmp_path, httpx_mock):
    result = CliRunner().invoke(
        app_with_command(), ["https://example.org", "--root", str(tmp_path), "--no-discover"]
    )
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-1] == NEXT_STEPS and "--no-discover" in result.output
    assert not (tmp_path / ".footnote").exists()


def test_init_keeps_at_most_limit_pages(tmp_path, httpx_mock):
    site_with_a_sitemap(httpx_mock)
    result = CliRunner().invoke(
        app_with_command(), ["https://example.org", "--root", str(tmp_path), "--limit", "1"]
    )
    assert result.exit_code == 0, result.output
    assert [page.url for page in read_pages(tmp_path)] == ["https://example.org/a"]
    assert "Found 1 page: 1 from sitemaps, 0 from feeds, 0 from YouTube" in result.output


def test_init_follows_at_most_five_redirects(tmp_path, httpx_mock):
    httpx_mock.add_response(url="https://example.org/robots.txt", status_code=404)
    hops = ["/sitemap.xml", "/s1", "/s2", "/s3", "/s4", "/s5", "/s6"]
    for here, there in pairwise(hops):
        httpx_mock.add_response(
            url=f"https://example.org{here}", status_code=301, headers={"Location": f"https://example.org{there}"}
        )
    # Six redirects stand between /sitemap.xml and the sitemap, one more than the client follows.
    httpx_mock.add_response(url="https://example.org/s6", text=SM, is_optional=True)
    httpx_mock.add_response(url="https://example.org/sitemap_index.xml", status_code=404)
    httpx_mock.add_response(url="https://example.org/", text="<html></html>")
    for path in ("/feed", "/rss.xml", "/atom.xml", "/feed.xml"):
        httpx_mock.add_response(url=f"https://example.org{path}", status_code=404)
    result = CliRunner().invoke(app_with_command(), ["https://example.org", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Found 0 pages" in result.output and read_pages(tmp_path) == []
    assert (tmp_path / ".footnote" / "pages.jsonl").exists()


def test_init_rejects_a_site_url_without_a_scheme_before_writing_or_fetching(tmp_path, httpx_mock):
    result = CliRunner().invoke(app_with_command(), ["example.org", "--root", str(tmp_path)])
    assert result.exit_code == 2 and "missing scheme" in result.output
    assert list(tmp_path.iterdir()) == []


def test_init_into_a_folder_that_does_not_exist_exits_2(tmp_path, httpx_mock):
    missing = tmp_path / "missing"
    result = CliRunner().invoke(app_with_command(), ["https://example.org", "--root", str(missing)])
    assert result.exit_code == 2 and str(missing) in result.output
    assert not missing.exists()


def test_init_writes_into_the_current_folder_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app_with_command(), ["https://example.org", "--no-discover"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "footnote.toml").exists() and (tmp_path / "intents.yaml").exists()


# Fix wave: --pages-only rebuilds the page library from the footnote.toml already there and writes neither
# project file, so a channel listed after init reaches discovery and the user's edits survive.

CHANNEL = "UC1234567890123456789012"
CHANNEL_FEED = (
    '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
    "<entry><yt:videoId>dQw4w9WgXcQ</yt:videoId><title>V</title></entry></feed>"
)
PROJECT_FILES = ("footnote.toml", "intents.yaml")


def edit(path, old, new):
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def edited_project(root):
    """A project after init and the user's edits: a channel listed in footnote.toml, an intent renamed."""
    write_templates(root, "https://example.org")
    edit(root / "footnote.toml", "youtube_channels = []", f'youtube_channels = ["{CHANNEL}"]')
    edit(root / "intents.yaml", "id: example-topic", "id: my-topic")


def snapshot(root):
    """Each project file's bytes and modification time: equal snapshots mean neither file was rewritten."""
    return {name: ((root / name).read_bytes(), (root / name).stat().st_mtime_ns) for name in PROJECT_FILES}


def site_with_only_a_channel(httpx_mock):
    """No robots.txt, sitemap or feed on the site; the listed channel's feed lists one video."""
    httpx_mock.add_response(url="https://example.org/robots.txt", status_code=404)
    for path in ("/sitemap.xml", "/sitemap_index.xml", "/feed", "/rss.xml", "/atom.xml", "/feed.xml"):
        httpx_mock.add_response(url=f"https://example.org{path}", status_code=404)
    httpx_mock.add_response(url="https://example.org/", text="<html></html>")
    httpx_mock.add_response(
        url=f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}", text=CHANNEL_FEED
    )


def test_init_pages_only_reads_a_channel_listed_after_init_and_rewrites_neither_file(tmp_path, httpx_mock):
    edited_project(tmp_path)
    before = snapshot(tmp_path)
    site_with_only_a_channel(httpx_mock)  # every mocked response must be requested, the channel feed too
    result = CliRunner().invoke(
        app_with_command(), ["https://example.org", "--root", str(tmp_path), "--pages-only"]
    )
    assert result.exit_code == 0, result.output
    assert [(page.url, page.source) for page in read_pages(tmp_path)] == [
        ("https://youtube.com/watch?v=dQw4w9WgXcQ", "youtube")
    ]
    library = tmp_path / ".footnote" / "pages.jsonl"
    assert result.output.splitlines() == [
        f"Found 1 page: 0 from sitemaps, 0 from feeds, 1 from YouTube; the library now holds 1 page (1 new); "
        f"wrote {library}",
        NEXT_STEPS,
    ]
    assert snapshot(tmp_path) == before


def test_init_over_existing_files_names_pages_only_and_changes_nothing(tmp_path, httpx_mock):
    edited_project(tmp_path)
    before = snapshot(tmp_path)
    result = CliRunner().invoke(app_with_command(), ["https://example.org", "--root", str(tmp_path)])
    assert result.exit_code == 2 and "already exists" in result.output
    assert "footnote init https://example.org --pages-only" in result.output
    assert snapshot(tmp_path) == before and not (tmp_path / ".footnote").exists()


def test_init_pages_only_without_a_footnote_toml_exits_2_and_writes_nothing(tmp_path, httpx_mock):
    result = CliRunner().invoke(
        app_with_command(), ["https://example.org", "--root", str(tmp_path), "--pages-only"]
    )
    assert result.exit_code == 2 and "footnote.toml not found" in result.output
    assert list(tmp_path.iterdir()) == []


def test_init_pages_only_exits_2_when_the_edited_footnote_toml_does_not_load(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    edit(tmp_path / "footnote.toml", "youtube_channels = []", 'youtube_channels = "@me"')  # not a list
    before = snapshot(tmp_path)
    result = CliRunner().invoke(
        app_with_command(), ["https://example.org", "--root", str(tmp_path), "--pages-only"]
    )
    assert result.exit_code == 2 and "site.youtube_channels" in result.output
    assert snapshot(tmp_path) == before and not (tmp_path / ".footnote").exists()


def test_init_pages_only_refuses_a_site_url_footnote_toml_does_not_name(tmp_path, httpx_mock):
    edited_project(tmp_path)
    before = snapshot(tmp_path)
    result = CliRunner().invoke(
        app_with_command(), ["https://other.example", "--root", str(tmp_path), "--pages-only"]
    )
    assert result.exit_code == 2
    assert "https://example.org" in result.output and "https://other.example" in result.output
    assert snapshot(tmp_path) == before and not (tmp_path / ".footnote").exists()


def test_init_pages_only_takes_the_site_url_in_another_spelling(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    site_with_a_sitemap(httpx_mock)  # discovery reads the site as footnote.toml spells it
    result = CliRunner().invoke(
        app_with_command(), ["https://WWW.Example.org/", "--root", str(tmp_path), "--pages-only"]
    )
    assert result.exit_code == 0, result.output
    assert [page.url for page in read_pages(tmp_path)] == ["https://example.org/a", "https://example.org/b"]


@pytest.mark.parametrize("flag", ["--force", "--no-discover"])
def test_init_pages_only_takes_neither_force_nor_no_discover(tmp_path, httpx_mock, flag):
    edited_project(tmp_path)
    before = snapshot(tmp_path)
    result = CliRunner().invoke(
        app_with_command(), ["https://example.org", "--root", str(tmp_path), "--pages-only", flag]
    )
    assert result.exit_code == 2 and "--pages-only" in result.output and flag in result.output
    assert snapshot(tmp_path) == before and not (tmp_path / ".footnote").exists()


# E2 (Ruling B31): --pages-only merges what it finds into the library by canonical URL; --prune rebuilds it.
# A discovery that finds nothing keeps the library as it was and says so. E3: init says how many fetches
# failed when it found no page.


def page(url, title=None, lastmod=None, source="sitemap"):
    key = canonicalize(url)
    return Page(id=Page.id_for(key), url=url, canonical_url=key, title=title, source=source, lastmod=lastmod)


def library_with_an_old_video(root):
    """The library before the refresh: /a (an older title) and a video the channel feed no longer lists."""
    write_pages(
        root,
        [
            page("https://example.org/a", title="Old A", lastmod="2026-01-01"),
            page("https://youtube.com/watch?v=OLDvideo123", title="Old video", source="youtube"),
        ],
    )
    return read_pages(root)


LISTED = (
    '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<url><loc>https://example.org/a</loc><lastmod>2026-10-01</lastmod></url>"
    "<url><loc>https://example.org/c</loc></url></urlset>"
)


def site_listing_a_and_c(httpx_mock):
    httpx_mock.add_response(url="https://example.org/robots.txt", status_code=404)
    httpx_mock.add_response(url="https://example.org/sitemap.xml", text=LISTED)
    httpx_mock.add_response(url="https://example.org/sitemap_index.xml", status_code=404)
    httpx_mock.add_response(url="https://example.org/", text="<html></html>")
    for path in ("/feed", "/rss.xml", "/atom.xml", "/feed.xml"):
        httpx_mock.add_response(url=f"https://example.org{path}", status_code=404)


def pages_only(root, *flags):
    args = ["https://example.org", "--root", str(root), "--pages-only", *flags]
    return CliRunner().invoke(app_with_command(), args)


def test_pages_only_merges_by_canonical_url_and_keeps_pages_it_no_longer_finds(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    old_a, old_video = library_with_an_old_video(tmp_path)
    site_listing_a_and_c(httpx_mock)
    result = pages_only(tmp_path)
    assert result.exit_code == 0, result.output
    a, video, c = read_pages(tmp_path)
    assert (a.id, a.title, a.lastmod) == (old_a.id, "Old A", "2026-10-01")  # the sitemap gives no title
    assert a.discovered_at == old_a.discovered_at and video == old_video and c.url == "https://example.org/c"
    found = "Found 2 pages: 2 from sitemaps, 0 from feeds, 0 from YouTube"
    assert f"{found}; the library now holds 3 pages (1 new)" in result.output


def test_pages_only_with_prune_rebuilds_the_library_from_what_it_finds(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    library_with_an_old_video(tmp_path)
    site_listing_a_and_c(httpx_mock)
    result = pages_only(tmp_path, "--prune")
    assert result.exit_code == 0, result.output
    assert [p.url for p in read_pages(tmp_path)] == ["https://example.org/a", "https://example.org/c"]
    assert "Found 2 pages: 2 from sitemaps, 0 from feeds, 0 from YouTube; wrote " in result.output


def nothing_answers(httpx_mock):
    for path in ("/robots.txt", "/sitemap.xml", "/sitemap_index.xml", "/"):
        httpx_mock.add_response(url=f"https://example.org{path}", status_code=503)
    for path in ("/feed", "/rss.xml", "/atom.xml", "/feed.xml"):
        httpx_mock.add_response(url=f"https://example.org{path}", status_code=404)


@pytest.mark.parametrize("flags", [(), ("--prune",)], ids=["merge", "prune"])
def test_a_discovery_that_finds_nothing_keeps_the_library_and_says_so(tmp_path, httpx_mock, flags):
    write_templates(tmp_path, "https://example.org")
    library_with_an_old_video(tmp_path)
    before = (tmp_path / ".footnote" / "pages.jsonl").read_bytes()
    nothing_answers(httpx_mock)
    result = pages_only(tmp_path, *flags)
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".footnote" / "pages.jsonl").read_bytes() == before
    library = tmp_path / ".footnote" / "pages.jsonl"
    assert result.output.splitlines()[0] == (
        f"Found 0 pages; 8 fetches failed; kept the library as it was (2 pages in {library})"
    )


def test_init_says_how_many_fetches_failed_when_it_finds_no_page(tmp_path, httpx_mock):
    nothing_answers(httpx_mock)
    result = CliRunner().invoke(app_with_command(), ["https://example.org", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    library = tmp_path / ".footnote" / "pages.jsonl"
    assert result.output.splitlines()[1] == f"Found 0 pages; 8 fetches failed; wrote {library}"


def test_prune_needs_pages_only(tmp_path, httpx_mock):
    args = ["https://example.org", "--root", str(tmp_path), "--prune"]
    result = CliRunner().invoke(app_with_command(), args)
    assert result.exit_code == 2 and "--prune" in result.output and "--pages-only" in result.output
    assert list(tmp_path.iterdir()) == []


# E4: the usage line names SITE_URL, --limit is at least 1, and a library that cannot be written exits 2.


def test_the_usage_line_names_site_url_and_the_limit_help_says_what_it_counts():
    result = CliRunner().invoke(app_with_command(), ["--help"])
    assert result.exit_code == 0 and "SITE_URL" in result.output and "{site_url}" not in result.output
    assert "YouTube" in result.output  # --limit counts sitemap and feed pages; videos come on top


@pytest.mark.parametrize("limit", ["0", "-5"])
def test_a_limit_below_1_exits_2_before_any_fetch(tmp_path, httpx_mock, limit):
    result = CliRunner().invoke(
        app_with_command(), ["https://example.org", "--root", str(tmp_path), "--limit", limit]
    )
    assert result.exit_code == 2 and "--limit" in result.output
    assert list(tmp_path.iterdir()) == []


def test_a_written_footnote_toml_that_does_not_load_exits_2_without_the_pages_only_hint(
    tmp_path, httpx_mock, monkeypatch
):
    """E6: write_templates loads what it wrote, so a template problem exits 2 before discovery; the hint about
    --pages-only is for a footnote.toml that was there before init, not one init just wrote."""
    broken = config_module.TOML_TEMPLATE.replace("reps = 2", "reps = 0")
    monkeypatch.setattr(config_module, "TOML_TEMPLATE", broken)
    result = CliRunner().invoke(app_with_command(), ["https://example.org", "--root", str(tmp_path)])
    assert result.exit_code == 2 and "design.reps" in result.output and "--pages-only" not in result.output


def test_init_refuses_a_site_url_with_a_quote_before_writing(tmp_path, httpx_mock):
    result = CliRunner().invoke(app_with_command(), ['https://example.org/"x', "--root", str(tmp_path)])
    assert result.exit_code == 2 and "quote" in result.output and list(tmp_path.iterdir()) == []


def test_a_library_that_cannot_be_written_exits_2(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    (tmp_path / ".footnote").write_text("a file where the store folder belongs")
    site_with_a_sitemap(httpx_mock)
    result = pages_only(tmp_path)
    assert result.exit_code == 2 and ".footnote" in result.output
