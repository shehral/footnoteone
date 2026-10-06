"""URL canonicalization and owner classification. Pure functions."""

from __future__ import annotations

from typing import Literal
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

OwnerClass = Literal["own_site", "own_offsite", "other"]

TRACKING_PREFIXES = ("utm_",)
TRACKING_KEYS = {
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
}
DEFAULT_PORTS = {"http": "80", "https": "443"}


def _clean_path(path: str) -> str:
    path = quote(unquote(path), safe="/~:@!$&'()*+,;=-._").rstrip("/")
    while path.endswith("/amp"):
        path = path[: -len("/amp")].rstrip("/")
    return path or "/"


def canonicalize(url: str) -> str:
    """Canonical form of url. Never raises: an unparseable URL comes back stripped but unchanged."""
    url = url.strip()
    try:
        return _canonicalize_strict(url)
    except ValueError:
        return url


def _canonicalize_strict(url: str) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if ":" in host:
        # .hostname drops the brackets around an IPv6 literal; restore them so the key re-parses
        host = f"[{host}]"
    try:
        number = parts.port
    except ValueError:
        number = None
    port = f":{number}" if number and str(number) != DEFAULT_PORTS.get(scheme) else ""
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(TRACKING_PREFIXES) and k.lower() not in TRACKING_KEYS
    ]
    query.sort()
    return urlunsplit((scheme, host + port, _clean_path(parts.path), urlencode(query), ""))


def host_of(url: str) -> str:
    """Lowercase host without a leading www. Never raises: an unparseable URL has host ""."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def classify_owner(url: str, own_domains: list[str], offsite_prefixes: list[str]) -> OwnerClass:
    canon = canonicalize(url)
    host = host_of(canon)
    for domain in own_domains:
        d = domain.lower().removeprefix("www.")
        if host == d or host.endswith("." + d):
            return "own_site"
    base = canon.split("?", 1)[0]
    for prefix in offsite_prefixes:
        p = canonicalize(prefix).rstrip("/")
        if base == p or base.startswith(p + "/"):
            return "own_offsite"
    return "other"
