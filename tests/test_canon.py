import itertools

import pytest

from footnoteone.canon import CANON_VERSION, OwnedSet, canonicalize, classify_owner, host_of, youtube_video_id


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("HTTPS://WWW.Example.com/Guide/", "https://example.com/Guide"),
        ("https://example.com/guide?utm_source=x&utm_medium=y", "https://example.com/guide"),
        ("https://example.com/guide?b=2&a=1&fbclid=zz", "https://example.com/guide?a=1&b=2"),
        ("https://example.com/guide#section-3", "https://example.com/guide"),
        ("https://example.com/guide/amp", "https://example.com/guide"),
        ("https://example.com/guide/amp/", "https://example.com/guide"),
        ("https://example.com/guide/amp/amp", "https://example.com/guide"),
        ("https://example.com/guide?amp=1", "https://example.com/guide"),
        ("https://example.com:443/guide", "https://example.com/guide"),
        ("https://example.com/", "https://example.com/"),
        ("https://example.com", "https://example.com/"),
        ("http://Example.com/a%7Eb", "https://example.com/a~b"),
    ],
)
def test_canonicalize(raw, expected):
    assert canonicalize(raw) == expected


def test_canonicalize_is_idempotent():
    url = "https://WWW.example.com/Guide/?utm_campaign=c&z=1#frag"
    assert canonicalize(canonicalize(url)) == canonicalize(url)
    amp_url = "https://example.com/post/amp/"
    assert canonicalize(canonicalize(amp_url)) == canonicalize(amp_url)


def test_canonicalize_keeps_an_ipv6_literal_host_bracketed():
    assert canonicalize("http://[::1]:8080/x") == "https://[::1]:8080/x"


def test_canonicalize_is_idempotent_for_an_ipv6_literal_host():
    url = "http://[2001:db8::1]/x"
    assert canonicalize(canonicalize(url)) == canonicalize(url)


def test_host_of_strips_www_and_lowercases():
    assert host_of("https://WWW.Example.com/x") == "example.com"


@pytest.mark.parametrize("raw", ["https://Example.com:abc/x", "https://Example.com:99999/x"])
def test_canonicalize_drops_an_invalid_port(raw):
    assert canonicalize(raw) == "https://example.com/x"


def test_canonicalize_returns_an_unparseable_url_unchanged():
    assert canonicalize("https://[::1/x") == "https://[::1/x"
    assert canonicalize("  https://[::1/x\n") == "https://[::1/x"


def test_canonicalize_returns_an_unencodable_url_unchanged():
    assert canonicalize("https://example.com/\ud800") == "https://example.com/\ud800"


def test_host_of_returns_empty_string_for_an_unparseable_url():
    assert host_of("https://[::1/x") == ""


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://Example.com:abc/x", "own_site"),
        ("https://Example.com:99999/x", "own_site"),
        ("https://[::1/x", "other"),
        ("  https://[::1/x\n", "other"),
        ("https://example.com/\ud800", "own_site"),
    ],
)
def test_classify_owner_never_raises_on_malformed_urls(raw, expected):
    owned = OwnedSet.build(["example.com"], ["https://medium.com/@ali"])
    assert classify_owner(raw, owned) == expected


NEW_ROWS = [
    ("http://example.com/x", "https://example.com/x"),
    ("https://example.com./x", "https://example.com/x"),
    ("https://www.www.example.com/x", "https://example.com/x"),
    ("https://example.com/a%2Fb", "https://example.com/a%2Fb"),
    ("https://example.com/a%7eb%2f", "https://example.com/a~b%2F"),
    ("https://example.com/index.html", "https://example.com/"),
    ("https://example.com/docs/index.htm", "https://example.com/docs"),
    ("https://example.com/p?a=1&amp;utm_source=x", "https://example.com/p?a=1"),
    ("https://example.com/p?gbraid=1&si=2&_hsenc=3&mkt_tok=4&b=1", "https://example.com/p?b=1"),
    ("https://medium.com/@ali/post-1?source=rss", "https://medium.com/@ali/post-1"),
    ("https://example.com/p?source=rss", "https://example.com/p?source=rss"),
    ("https://youtu.be/dQw4w9WgXcQ?t=42", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
]


@pytest.mark.parametrize("raw, expected", NEW_ROWS)
def test_canonicalize_v2_rows(raw, expected):
    assert canonicalize(raw) == expected


@pytest.mark.parametrize("raw, expected", NEW_ROWS)
def test_canonicalize_v2_is_idempotent(raw, expected):
    assert canonicalize(expected) == expected


def test_canon_version_is_two():
    assert CANON_VERSION == 2


def test_youtube_video_id():
    assert youtube_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube_video_id("https://youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube_video_id("https://youtube.com/@ali") is None
    assert youtube_video_id("https://example.com/watch?v=dQw4w9WgXcQ") is None


OWNED = OwnedSet.build(
    own_domains=["example.org", "notes.example.org"],
    offsite_prefixes=[
        "https://medium.com/@Ali",
        "https://www.youtube.com/@alichannel",
        "https://ali.substack.com",
    ],
    owned_urls=["https://www.facebook.com/profile.php?id=123"],
    youtube_video_ids=["dQw4w9WgXcQ"],
)


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://example.org/p", "own_site"),
        ("http://WWW.example.org/p", "own_site"),
        ("https://deep.notes.example.org/p", "own_site"),
        ("https://example.org./p", "own_site"),
        ("https://notexample.org/p", "other"),
        ("https://example.org.evil.com/p", "other"),
        ("http://medium.com/@ali/why-geo-fails", "own_offsite"),
        ("https://medium.com/@ALI?source=x", "own_offsite"),
        ("https://medium.com/@alice/post", "other"),
        ("https://youtube.com/@AliChannel/videos", "own_offsite"),
        ("https://youtu.be/dQw4w9WgXcQ", "own_offsite"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "own_offsite"),
        ("https://www.youtube.com/watch?v=otherVideo1", "other"),
        ("https://ali.substack.com/p/first", "own_offsite"),
        ("https://facebook.com/profile.php?id=123", "own_offsite"),
        ("https://facebook.com/profile.php?id=124", "other"),
        ("", "other"),
        ("https://[::1/x", "other"),
    ],
)
def test_classify_owner(url, expected):
    assert classify_owner(url, OWNED) == expected


def test_owned_set_build_normalizes_entries():
    owned = OwnedSet.build([" WWW.Example.org "], ["http://Medium.com/@Ali/"], ["http://Example.net/P/"], [])
    assert owned.domains == ("example.org",)
    assert owned.prefixes == ("https://medium.com/@ali",)
    assert owned.urls == frozenset({"https://example.net/P"})
    assert OwnedSet.build([""], [], [], []).domains == ()


# An offsite_prefixes entry claims every page below its path, so one that names a single page must not
# become a prefix: a video URL keeps its id, a key with a query is matched exactly, a fragment is dropped.
PREFIX_ENTRIES = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/9bZkp7q19f0",
    "https://www.facebook.com/profile.php?id=123",
    "https://twitter.com/#!/ali",
    "https://github.com/ali?utm_source=share",
    "https://medium.com/@ali#",
]


def test_owned_set_build_sorts_prefix_entries_by_what_they_name():
    owned = OwnedSet.build([], PREFIX_ENTRIES)
    assert owned.youtube_video_ids == frozenset({"dQw4w9WgXcQ", "9bZkp7q19f0"})
    assert owned.urls == frozenset({"https://facebook.com/profile.php?id=123"})
    assert owned.prefixes == ("https://github.com/ali", "https://medium.com/@ali")


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://youtu.be/dQw4w9WgXcQ", "own_offsite"),
        ("https://www.youtube.com/shorts/9bZkp7q19f0", "own_offsite"),
        ("https://www.youtube.com/watch?v=someoneElse", "other"),
        ("https://www.facebook.com/profile.php?id=123", "own_offsite"),
        ("https://facebook.com/profile.php?id=999", "other"),
        ("https://twitter.com/someone", "other"),
        ("https://twitter.com/#!/someone", "other"),
        ("https://twitter.com/", "other"),
        ("https://github.com/ali/repo", "own_offsite"),
        ("https://github.com/alice", "other"),
        ("https://medium.com/@ali/post", "own_offsite"),
    ],
)
def test_classify_owner_never_widens_a_prefix_entry_that_names_one_page(url, expected):
    assert classify_owner(url, OwnedSet.build([], PREFIX_ENTRIES)) == expected


# Only the &amp; separator is unescaped: html.unescape also decoded legacy entity names that have no
# semicolon, so &section, &region, &copy, &timestamp, &param, &notify and &currency were mangled.
QUERY_ENTITY_ROWS = [
    ("https://example.com/p?a=1&section=2", "https://example.com/p?a=1&section=2"),
    ("https://example.com/p?region=us&q=x", "https://example.com/p?q=x&region=us"),
    ("https://example.com/p?b=2&amp=1", "https://example.com/p?b=2"),
    (
        "https://example.com/p?q=x&copy=1&timestamp=2&param=3&notify=4&currency=5",
        "https://example.com/p?copy=1&currency=5&notify=4&param=3&q=x&timestamp=2",
    ),
    ("https://example.com/p?a=1&amp;section=2", "https://example.com/p?a=1&section=2"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&region=us", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
]


@pytest.mark.parametrize("raw, expected", QUERY_ENTITY_ROWS)
def test_canonicalize_unescapes_only_the_amp_separator(raw, expected):
    assert canonicalize(raw) == expected
    assert canonicalize(expected) == expected


@pytest.mark.parametrize(
    "pairs",
    [("a=1", "section=2"), ("region=us", "q=x"), ("b=2", "amp=1"), ("copy=1", "timestamp=2", "param=3")],
)
def test_canonicalize_query_ignores_pair_order_and_an_escaped_separator(pairs):
    keys = {
        canonicalize("https://example.com/p?" + separator.join(order))
        for order in itertools.permutations(pairs)
        for separator in ("&", "&amp;")
    }
    assert len(keys) == 1, keys
    (key,) = keys
    assert canonicalize(key) == key


def test_youtube_video_id_survives_a_query_name_that_starts_like_an_entity():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&region=us"
    assert youtube_video_id(url) == "dQw4w9WgXcQ"
    assert youtube_video_id("https://www.youtube.com/watch?feature=share&amp;v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert classify_owner(url, OWNED) == "own_offsite"


def test_classify_owner_matches_an_owned_url_whatever_the_query_order():
    owned = OwnedSet.build([], [], ["https://example.net/p?a=1&section=2"])
    assert classify_owner("https://example.net/p?section=2&a=1", owned) == "own_offsite"
    assert classify_owner("https://example.net/p?a=1&section=2", owned) == "own_offsite"


# The key's scheme is https whatever came in, so it drops both web default ports.
PORT_ROWS = [
    ("http://example.com:80/x", "https://example.com/x"),
    ("http://example.com:443/x", "https://example.com/x"),
    ("https://example.com:80/x", "https://example.com/x"),
    ("http://[::1]:80/x", "https://[::1]/x"),
    ("http://example.com:8080/x", "https://example.com:8080/x"),
]


@pytest.mark.parametrize("raw, expected", PORT_ROWS)
def test_canonicalize_drops_both_default_ports_from_an_https_key(raw, expected):
    assert canonicalize(raw) == expected
    assert canonicalize(expected) == expected


def test_canonicalize_is_idempotent_across_schemes_ports_and_queries():
    urls = [
        f"{scheme}://{host}{port}/p{query}"
        for scheme in ("http", "https", "HTTP")
        for host in ("example.com", "WWW.Example.com.", "[::1]")
        for port in ("", ":80", ":443", ":8080")
        for query in ("", "?region=us&q=x", "?b=2&amp=1", "?a=1&amp;section=2", "?amp=1&copy=2&utm_source=x")
    ]
    unstable = [url for url in urls if canonicalize(canonicalize(url)) != canonicalize(url)]
    assert unstable == []
