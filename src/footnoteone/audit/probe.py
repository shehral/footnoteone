"""Network side of the audit: fetch robots.txt and probe URLs with each bot's user agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import httpx

from footnoteone import __version__
from footnoteone.audit.robots import Purpose, load_bots, parse_robots

ROBOTS_USER_AGENT = f"FootnoteOne/{__version__} (+https://github.com/footnoteone/footnoteone)"
ROBOTS_MAX_BYTES = 500 * 1024  # RFC 9309 2.5: parse at least 500 KiB; content past the cap is ignored
RobotsAccess = Literal["success", "unavailable", "unreachable"]  # RFC 9309 2.3.1 access results


@dataclass(frozen=True)
class RobotsFetch:
    status: int | None  # final HTTP status after redirects; None when the request itself failed
    text: str | None  # the body, capped at 500 KiB, only on success
    error: str | None  # exception class name and message when the request failed
    access: RobotsAccess


@dataclass(frozen=True)
class ProbeResult:
    status: int | None  # final HTTP status after redirects; None when the request failed
    final_url: str | None  # where the redirects ended
    error: str | None  # exception class name and message when the request failed


@dataclass(frozen=True)
class AccessRow:
    bot: str
    purpose: Purpose
    path: str
    robots_allowed: bool
    matched_rule: str | None
    http_status: int | None
    final_url: str | None
    probe_error: str | None  # why the probe failed, or why the path was not probed


@dataclass(frozen=True)
class AccessReport:
    robots_status: int | None
    robots_error: str | None
    robots_access: RobotsAccess  # RobotsFetch.access: success, unavailable or unreachable
    rows: list[AccessRow]


_NOT_PROBED = ProbeResult(status=None, final_url=None, error=None)
_CONTROL_TOKEN = ProbeResult(status=None, final_url=None, error="not probed: control token")


def _describe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


def _access_for_status(status: int) -> RobotsAccess:
    """RFC 9309 2.3.1 for a final status: 2xx success; a 3xx with nothing left to follow, or a 4xx other
    than 429, unavailable; 429 (as Google treats it), 5xx and 1xx unreachable."""
    if 200 <= status < 300:
        return "success"
    if 300 <= status < 500 and status != 429:
        return "unavailable"
    return "unreachable"


def _capped_text(body: bytes, encoding: str) -> str:
    """The first 500 KiB of the body (RFC 9309 2.5); a line cut by the cap is dropped, not shortened."""
    if len(body) > ROBOTS_MAX_BYTES:
        body = body[:ROBOTS_MAX_BYTES]
        body = body[: max(body.rfind(b"\n"), body.rfind(b"\r")) + 1]
    return body.decode(encoding, errors="replace")


def _origin(url: str) -> str:
    """`scheme://netloc` of an http or https URL; ValueError for any other scheme or a missing host."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"site URL needs an http or https scheme and a host, got {url!r}")
    return f"{parts.scheme}://{parts.netloc}"


async def fetch_robots(client: httpx.AsyncClient, origin: str) -> RobotsFetch:
    """GET `<origin>/robots.txt` and classify the result per RFC 9309 2.3.1 (see RobotsFetch.access).

    Raises ValueError, before any request, when `origin` lacks an http or https scheme or a host. Callers
    should create the client with `max_redirects=5` to match RFC 9309's five-hop rule (2.3.1.2: after five
    consecutive redirects robots.txt may be treated as unavailable); httpx defaults to 20.
    """
    url = _origin(origin) + "/robots.txt"
    headers = {"User-Agent": ROBOTS_USER_AGENT}
    try:
        resp = await client.get(url, headers=headers, timeout=10.0, follow_redirects=True)
    except httpx.TooManyRedirects as exc:  # RFC 9309 2.3.1.2: past the redirect limit it is unavailable
        return RobotsFetch(status=None, text=None, error=_describe(exc), access="unavailable")
    except httpx.HTTPError as exc:
        return RobotsFetch(status=None, text=None, error=_describe(exc), access="unreachable")
    access = _access_for_status(resp.status_code)
    text = _capped_text(resp.content, resp.encoding or "utf-8") if access == "success" else None
    return RobotsFetch(status=resp.status_code, text=text, error=None, access=access)


async def probe_url(client: httpx.AsyncClient, url: str, user_agent: str) -> ProbeResult:
    try:
        resp = await client.get(url, headers={"User-Agent": user_agent}, timeout=10.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        return ProbeResult(status=None, final_url=None, error=_describe(exc))
    return ProbeResult(status=resp.status_code, final_url=str(resp.url), error=None)


def _unreachable_reason(fetched: RobotsFetch) -> str | None:
    """The matched-rule text for every row when robots.txt is unreachable (RFC 9309 2.3.1.4), else None."""
    if fetched.access != "unreachable":
        return None
    cause = f"HTTP {fetched.status}" if fetched.status is not None else fetched.error
    return f"robots.txt unreachable ({cause}); RFC 9309 2.3.1.4 assumes complete disallow"


def _request_path(path: str) -> str:
    """The path as judged and probed: empty means `/`; anything else must start with `/`."""
    if path == "":
        return "/"
    if not path.startswith("/"):
        raise ValueError(f"path must start with '/': {path!r}")
    return path


async def access_matrix(
    client: httpx.AsyncClient, site_url: str, paths: list[str], probe: bool = True
) -> AccessReport:
    """One row per (bot, path): the robots verdict with its matched rule, and the live status if probed.

    An unreachable robots.txt disallows every path (RFC 9309 2.3.1.4); probes still run when asked. A
    control token (`crawls: false` in bots.yaml, such as Google-Extended) never fetches pages, so it is not
    probed: its rows keep the robots verdict, with probe_error "not probed: control token". Raises
    ValueError, before any request, for a site URL without an http or https scheme or without a host, and
    for a path that does not start with `/`.
    """
    request_paths = [_request_path(path) for path in paths]  # validated before any request is sent
    origin = _origin(site_url)  # likewise
    fetched = await fetch_robots(client, origin)
    unreachable = _unreachable_reason(fetched)
    policy = parse_robots(fetched.text or "")
    rows: list[AccessRow] = []
    for bot in load_bots():
        for path in request_paths:
            if unreachable is not None:
                allowed, matched = False, unreachable
            else:
                allowed, rule = policy.allowed(bot.token, path)
                matched = None
                if rule is not None:
                    matched = f"{'Allow' if rule.allow else 'Disallow'}: {rule.pattern} (line {rule.line_no})"
            result = _NOT_PROBED
            if probe and not bot.crawls:
                result = _CONTROL_TOKEN
            elif probe:
                ua = f"Mozilla/5.0 (compatible; {bot.token}; +{bot.doc_url})"
                result = await probe_url(client, origin + path, ua)
            rows.append(
                AccessRow(
                    bot=bot.token,
                    purpose=bot.purpose,
                    path=path,
                    robots_allowed=allowed,
                    matched_rule=matched,
                    http_status=result.status,
                    final_url=result.final_url,
                    probe_error=result.error,
                )
            )
    return AccessReport(
        robots_status=fetched.status, robots_error=fetched.error, robots_access=fetched.access, rows=rows
    )
