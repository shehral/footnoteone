"""URL canonicalization and owner classification. Pure functions; never raise.

CANON_VERSION labels the rule set. Stored canonical_url values are a cache: metrics recompute them with the
current version (METRICS.md 0.2.0).
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

CANON_VERSION = 2
OwnerClass = Literal["own_site", "own_offsite", "other"]

TRACKING_PREFIXES = ("utm_",)
TRACKING_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "ref_src",
        "srsltid",
        "amp",
        "gbraid",
        "wbraid",
        "yclid",
        "twclid",
        "ttclid",
        "_hsenc",
        "_hsmi",
        "mkt_tok",
        "si",
        "feature",  # YouTube appends feature=share
    }
)
HOST_TRACKING_KEYS = {"medium.com": frozenset({"source"})}
# Keyed by the key's scheme. http folds to https, so an https key drops both web default ports:
# http://h:80/x, http://h:443/x, https://h:80/x and https://h:443/x all key as https://h/x.
DEFAULT_PORTS = {"https": frozenset({80, 443})}
INDEX_FILES = ("/index.html", "/index.htm")
YOUTUBE_HOSTS = frozenset({"youtube.com", "m.youtube.com", "youtu.be", "youtube-nocookie.com"})
_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _canonical_escape(match: re.Match[str]) -> str:
    char = chr(int(match.group(1), 16))
    return char if char in _UNRESERVED else "%" + match.group(1).upper()


def _clean_path(path: str) -> str:
    path = quote(path, safe="/~:@!$&'()*+,;=-._%")  # keep existing escapes; encode raw non-ASCII and spaces
    path = re.sub(r"%([0-9A-Fa-f]{2})", _canonical_escape, path)  # decode unreserved escapes only
    for index in INDEX_FILES:
        if path.endswith(index):
            path = path[: -len(index)]
    path = path.rstrip("/")
    while path.endswith("/amp"):
        path = path[: -len("/amp")].rstrip("/")
    return path or "/"


def _unescape_query(query: str) -> str:
    """Undo an HTML-escaped pair separator (&amp; becomes &) and nothing else. html.unescape also decodes
    legacy entity names that have no semicolon: ?a=1&section=2 lost its section key to a section sign, and
    ?b=2&amp=1 kept amp's value under an empty key that the tracking list cannot remove. A numeric reference
    such as &#038; never reaches the query, since its # starts the fragment."""
    return query.replace("&amp;", "&")


def _clean_host(hostname: str | None) -> str:
    host = (hostname or "").lower().rstrip(".")
    while host.startswith("www."):
        host = host[4:]
    if ":" in host:
        host = f"[{host}]"  # .hostname drops the brackets around an IPv6 literal
    return host


def _video_id(host: str, path: str, query: list[tuple[str, str]]) -> str | None:
    if host not in YOUTUBE_HOSTS:
        return None
    candidate = None
    if host == "youtu.be":
        candidate = path.strip("/").split("/")[0]
    elif path == "/watch":
        candidate = next((v for k, v in query if k == "v"), None)
    else:
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
            candidate = parts[1]
    return candidate if candidate and _VIDEO_ID.match(candidate) else None


def youtube_video_id(url: str) -> str | None:
    """The 11-character video id of a YouTube video URL in any of its forms, else None. Never raises."""
    try:
        parts = urlsplit(url.strip())
        return _video_id(_clean_host(parts.hostname), parts.path, parse_qsl(_unescape_query(parts.query)))
    except ValueError:
        return None


def _canonicalize_strict(url: str) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme == "http":
        scheme = "https"  # one key per page whatever the scheme the engine returned
    host = _clean_host(parts.hostname)
    query_pairs = parse_qsl(_unescape_query(parts.query), keep_blank_values=True)
    video = _video_id(host, parts.path, query_pairs)
    if video:
        return f"https://youtube.com/watch?v={video}"
    try:
        number = parts.port
    except ValueError:
        number = None
    port = f":{number}" if number and number not in DEFAULT_PORTS.get(scheme, frozenset()) else ""
    drop = TRACKING_KEYS | HOST_TRACKING_KEYS.get(host, frozenset())
    query = sorted(
        (k, v)
        for k, v in query_pairs
        if not k.lower().startswith(TRACKING_PREFIXES) and k.lower() not in drop
    )
    return urlunsplit((scheme, host + port, _clean_path(parts.path), urlencode(query), ""))


def canonicalize(url: str) -> str:
    """Canonical form of url under rule set CANON_VERSION. Never raises: an unparseable URL comes back
    stripped but unchanged."""
    url = url.strip()
    try:
        return _canonicalize_strict(url)
    except ValueError:
        return url


def host_of(url: str) -> str:
    """Lowercase host without leading www or trailing dot. Never raises: an unparseable URL has host ""."""
    try:
        return _clean_host(urlsplit(url.strip()).hostname).strip("[]")
    except ValueError:
        return ""


def _offsite_entry(entry: str) -> tuple[Literal["video", "url", "prefix"], str] | None:
    """What one offsite_prefixes entry names. A prefix claims every page below its path, so an entry that
    names one page must not become one. A YouTube video URL gives its video id: as a prefix it would cut to
    https://youtube.com/watch and claim every video. An entry whose key keeps a query gives that exact URL:
    profile.php?id=123 must not claim profile.php?id=999 (tracking keys go first, so an entry whose only
    query is ?utm_source=x stays a prefix). An entry with a fragment gives None: keys drop the fragment, so
    as a prefix or as an exact URL https://twitter.com/#!/ali would also claim https://twitter.com/#!/anyone.
    Everything else gives a prefix, compared without scheme and path case."""
    entry = entry.strip()
    if not entry:
        return None
    video = youtube_video_id(entry)
    if video:
        return "video", video
    if entry.partition("#")[2]:
        return None
    key = canonicalize(entry)
    if "?" in key:
        return "url", key
    return "prefix", key.rstrip("/").lower()


@dataclass(frozen=True)
class OwnedSet:
    """Everything that counts as the creator's: own domains (with subdomains), off-site profile prefixes
    (compared without scheme and path case), exact off-site page URLs, and owned YouTube video ids. build
    files each offsite_prefixes entry by what it names (see _offsite_entry): a video URL adds its video id, a
    URL whose key keeps a query adds that exact URL, and an entry with a fragment is dropped."""

    domains: tuple[str, ...]
    prefixes: tuple[str, ...]
    urls: frozenset[str]
    youtube_video_ids: frozenset[str]

    @classmethod
    def build(
        cls,
        own_domains: list[str] | tuple[str, ...],
        offsite_prefixes: list[str] | tuple[str, ...],
        owned_urls: list[str] | tuple[str, ...] = (),
        youtube_video_ids: list[str] | tuple[str, ...] = (),
    ) -> OwnedSet:
        hosts: list[str] = []
        for entry in own_domains:
            # " WWW.Example.org " and "https://example.org" both reduce to the bare host example.org
            bare = entry.strip().lower().removeprefix("https://").removeprefix("http://")
            hosts.append(host_of("https://" + bare))
        domains = tuple(dict.fromkeys(host for host in hosts if host))
        prefixes: list[str] = []
        urls = {canonicalize(u) for u in owned_urls if u.strip()}
        video_ids = list(youtube_video_ids)
        for entry in offsite_prefixes:
            filed = _offsite_entry(entry)
            if filed is None:
                continue
            kind, key = filed
            if kind == "video":
                video_ids.append(key)
            elif kind == "url":
                urls.add(key)
            else:
                prefixes.append(key)
        ids = frozenset(v.strip() for v in video_ids if _VIDEO_ID.match(v.strip()))
        return cls(domains, tuple(dict.fromkeys(prefixes)), frozenset(urls), ids)


def classify_owner(url: str, owned: OwnedSet) -> OwnerClass:
    """own_site by domain or subdomain; own_offsite by exact URL, owned video id or profile prefix;
    else other."""
    canon = canonicalize(url)
    host = host_of(canon)
    if host and any(host == d or host.endswith("." + d) for d in owned.domains):
        return "own_site"
    if canon in owned.urls:
        return "own_offsite"
    video = youtube_video_id(canon)
    if video and video in owned.youtube_video_ids:
        return "own_offsite"
    base = canon.split("?", 1)[0].rstrip("/").lower()
    if any(base == p or base.startswith(p + "/") for p in owned.prefixes):
        return "own_offsite"
    return "other"
