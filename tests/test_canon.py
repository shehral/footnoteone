import pytest

from footnoteone.canon import canonicalize, classify_owner, host_of


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
        ("http://Example.com/a%7Eb", "http://example.com/a~b"),
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
    assert canonicalize("http://[::1]:8080/x") == "http://[::1]:8080/x"


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
    assert classify_owner(raw, ["example.com"], ["https://medium.com/@ali"]) == expected


def test_classify_owner_two_own_domains_and_subdomains():
    own = ["example.org", "notes.example.org"]
    assert classify_owner("https://example.org/p", own, []) == "own_site"
    assert classify_owner("https://www.example.org/p", own, []) == "own_site"
    assert classify_owner("https://notes.example.org/p", own, []) == "own_site"
    assert classify_owner("https://example.net/p", own, []) == "other"


def test_classify_owner_offsite_profiles_by_prefix():
    prefixes = [
        "https://medium.com/@ali",
        "https://www.youtube.com/@alichannel",
        "https://alishehral.substack.com",
    ]
    assert classify_owner("https://medium.com/@ali/why-geo-fails-1234", [], prefixes) == "own_offsite"
    assert classify_owner("https://medium.com/@someone-else/post", [], prefixes) == "other"
    assert classify_owner("https://medium.com/@alice/post", [], prefixes) == "other"
    assert classify_owner("https://alishehral.substack.com/p/first-post", [], prefixes) == "own_offsite"
    assert classify_owner("https://medium.com/@ali?source=x", [], prefixes) == "own_offsite"
    assert classify_owner("https://medium.com/@alice?source=x", [], prefixes) == "other"
