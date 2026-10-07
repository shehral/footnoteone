import httpx
import pytest

from footnoteone import __version__
from footnoteone.audit.probe import access_matrix, fetch_robots, probe_url
from footnoteone.audit.robots import load_bots, parse_robots

ROBOTS_UA = f"FootnoteOne/{__version__} (+https://github.com/shehral/footnoteone)"
CONTROL_TOKENS = ("Google-Extended", "Applebot-Extended")  # bots.yaml `crawls: false`: never probed


@pytest.mark.asyncio
async def test_fetch_robots_returns_text_or_none(httpx_mock):
    httpx_mock.add_response(
        url="https://site.example/robots.txt",
        text="User-agent: *\nDisallow: /x\n",
        match_headers={"User-Agent": ROBOTS_UA},
    )
    httpx_mock.add_response(
        url="https://empty.example/robots.txt", status_code=404, match_headers={"User-Agent": ROBOTS_UA}
    )
    async with httpx.AsyncClient() as client:
        found = await fetch_robots(client, "https://site.example")
        missing = await fetch_robots(client, "https://empty.example")
    assert found.status == 200 and "Disallow" in found.text and found.error is None
    assert found.access == "success"
    assert missing.status == 404 and missing.text is None and missing.error is None
    assert missing.access == "unavailable"


@pytest.mark.asyncio
async def test_fetch_robots_ignores_content_past_500_kib(httpx_mock):
    head = "User-agent: *\nDisallow: /a\n"
    last = "Disallow: /bcd\n"  # the 500 KiB cap falls inside this line, right after "Disallow: /b"
    body = head + "#" * (500 * 1024 - len(head) - 1 - 12) + "\n" + last
    httpx_mock.add_response(url="https://site.example/robots.txt", text=body)
    async with httpx.AsyncClient() as client:
        fetched = await fetch_robots(client, "https://site.example")
    policy = parse_robots(fetched.text)
    assert policy.allowed("x", "/a")[0] is False
    assert policy.allowed("x", "/bcd") == (True, None)
    assert policy.allowed("x", "/bxyz") == (True, None)


@pytest.mark.asyncio
async def test_probe_url_sends_user_agent_and_returns_status(httpx_mock):
    httpx_mock.add_response(
        url="https://site.example/p", status_code=200, match_headers={"User-Agent": "GPTBot"}
    )
    async with httpx.AsyncClient() as client:
        result = await probe_url(client, "https://site.example/p", "GPTBot")
    assert result.status == 200 and result.final_url == "https://site.example/p" and result.error is None


@pytest.mark.asyncio
async def test_probe_url_reports_the_final_url_after_a_redirect(httpx_mock):
    httpx_mock.add_response(
        url="https://site.example/p", status_code=302, headers={"Location": "https://site.example/challenge"}
    )
    httpx_mock.add_response(url="https://site.example/challenge", text="Checking your browser")
    async with httpx.AsyncClient() as client:
        result = await probe_url(client, "https://site.example/p", "GPTBot")
    assert result.status == 200 and result.final_url == "https://site.example/challenge"


@pytest.mark.asyncio
async def test_probe_url_reports_a_failed_request(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"), url="https://site.example/p")
    async with httpx.AsyncClient() as client:
        result = await probe_url(client, "https://site.example/p", "GPTBot")
    assert result.status is None and result.final_url is None and result.error == "ConnectError: refused"


@pytest.mark.asyncio
async def test_access_matrix_combines_rules_and_probes(httpx_mock):
    httpx_mock.add_response(url="https://site.example/robots.txt", text="User-agent: GPTBot\nDisallow: /\n")
    async with httpx.AsyncClient() as client:
        report = await access_matrix(client, "https://site.example", ["/p"], probe=False)
    assert report.robots_status == 200 and report.robots_error is None
    by_bot = {r.bot: r for r in report.rows}
    assert (
        by_bot["GPTBot"].robots_allowed is False and by_bot["GPTBot"].matched_rule == "Disallow: / (line 2)"
    )
    assert by_bot["OAI-SearchBot"].robots_allowed is True and by_bot["OAI-SearchBot"].matched_rule is None
    assert all(r.http_status is None and r.final_url is None and r.probe_error is None for r in report.rows)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "access"), [(200, "success"), (404, "unavailable"), (503, "unreachable")]
)
async def test_access_matrix_reports_the_robots_access_result(httpx_mock, status, access):
    httpx_mock.add_response(url="https://site.example/robots.txt", status_code=status, text="")
    async with httpx.AsyncClient() as client:
        report = await access_matrix(client, "https://site.example", ["/p"], probe=False)
    assert report.robots_access == access


@pytest.mark.asyncio
async def test_access_matrix_has_one_row_per_bot_and_path(httpx_mock):
    httpx_mock.add_response(url="https://site.example/robots.txt", text="")
    async with httpx.AsyncClient() as client:
        report = await access_matrix(client, "https://site.example", ["/p", "/q"], probe=False)
    assert len(report.rows) == len(load_bots()) * 2 == 26


@pytest.mark.asyncio
async def test_access_matrix_probes_with_each_bots_user_agent(httpx_mock):
    httpx_mock.add_response(url="https://site.example/robots.txt", text="")
    gptbot = "Mozilla/5.0 (compatible; GPTBot; +https://developers.openai.com/api/docs/bots)"
    httpx_mock.add_response(
        url="https://site.example/p", status_code=403, match_headers={"User-Agent": gptbot}
    )
    httpx_mock.add_response(url="https://site.example/p", status_code=200, is_reusable=True)
    async with httpx.AsyncClient() as client:
        report = await access_matrix(client, "https://site.example", ["/p"])
    statuses = {r.bot: r.http_status for r in report.rows}
    assert statuses.pop("GPTBot") == 403
    assert {statuses.pop(token) for token in CONTROL_TOKENS} == {None}  # not probed
    assert set(statuses.values()) == {200}


@pytest.mark.asyncio
async def test_access_matrix_does_not_probe_control_tokens_but_keeps_their_rows(httpx_mock):
    robots = "User-agent: Google-Extended\nDisallow: /\n"
    httpx_mock.add_response(url="https://site.example/robots.txt", text=robots)
    httpx_mock.add_response(url="https://site.example/p", status_code=200, is_reusable=True)
    async with httpx.AsyncClient() as client:
        report = await access_matrix(client, "https://site.example", ["/p"])
    assert len(report.rows) == len(load_bots())  # one row per bot, as before
    by_bot = {r.bot: r for r in report.rows}
    for token in CONTROL_TOKENS:
        row = by_bot[token]
        assert (row.http_status, row.final_url, row.probe_error) == (None, None, "not probed: control token")
    google = by_bot["Google-Extended"]
    assert google.robots_allowed is False and google.matched_rule == "Disallow: / (line 2)"  # verdict kept
    agents = [r.headers["User-Agent"] for r in httpx_mock.get_requests() if r.url.path == "/p"]
    assert len(agents) == len(load_bots()) - len(CONTROL_TOKENS)
    assert not any(f"; {token};" in agent for agent in agents for token in CONTROL_TOKENS)


@pytest.mark.asyncio
async def test_access_matrix_judges_and_probes_an_empty_path_as_slash(httpx_mock):
    httpx_mock.add_response(url="https://site.example/robots.txt", text="User-agent: *\nDisallow: /\n")
    httpx_mock.add_response(url="https://site.example/", status_code=200, is_reusable=True)
    async with httpx.AsyncClient() as client:
        report = await access_matrix(client, "https://site.example", [""])
    assert report.rows and all(r.path == "/" and r.robots_allowed is False for r in report.rows)
    assert all(r.matched_rule == "Disallow: / (line 2)" for r in report.rows)
    probed = [r for r in report.rows if r.bot not in CONTROL_TOKENS]
    assert all(r.http_status == 200 and r.final_url == "https://site.example/" for r in probed)


@pytest.mark.asyncio
async def test_access_matrix_rejects_a_path_without_a_leading_slash(httpx_mock):
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError):
            await access_matrix(client, "https://site.example", ["p"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "site", ["site.example", "ftp://site.example", "https://", "https:///p", "//site.example", "https://:80"]
)
async def test_access_matrix_rejects_a_site_url_without_an_http_scheme_or_a_host(httpx_mock, site):
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="scheme and a host"):
            await access_matrix(client, site, ["/p"])
    assert httpx_mock.get_requests() == []  # rejected before any request


@pytest.mark.asyncio
async def test_fetch_robots_rejects_an_origin_without_an_http_scheme_or_a_host(httpx_mock):
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="scheme and a host"):
            await fetch_robots(client, "site.example")
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_access_matrix_treats_a_5xx_robots_txt_as_complete_disallow_and_still_probes(httpx_mock):
    httpx_mock.add_response(url="https://site.example/robots.txt", status_code=503)
    httpx_mock.add_response(url="https://site.example/p", status_code=200, is_reusable=True)
    async with httpx.AsyncClient() as client:
        report = await access_matrix(client, "https://site.example", ["/p"])
    assert report.robots_status == 503 and report.robots_error is None
    reason = "robots.txt unreachable (HTTP 503); RFC 9309 2.3.1.4 assumes complete disallow"
    assert report.rows and all(r.robots_allowed is False and r.matched_rule == reason for r in report.rows)
    assert all(r.http_status == 200 for r in report.rows if r.bot not in CONTROL_TOKENS)


@pytest.mark.asyncio
async def test_access_matrix_treats_a_4xx_robots_txt_as_no_rules(httpx_mock):
    httpx_mock.add_response(url="https://site.example/robots.txt", status_code=404)
    async with httpx.AsyncClient() as client:
        report = await access_matrix(client, "https://site.example", ["/p"], probe=False)
    assert report.robots_status == 404 and report.robots_error is None
    assert report.rows and all(r.robots_allowed is True and r.matched_rule is None for r in report.rows)


@pytest.mark.asyncio
async def test_access_matrix_treats_429_as_complete_disallow(httpx_mock):
    httpx_mock.add_response(
        url="https://site.example/robots.txt", status_code=429, text="User-agent: *\nDisallow:\n"
    )
    async with httpx.AsyncClient() as client:
        report = await access_matrix(client, "https://site.example", ["/p"], probe=False)
    assert report.robots_status == 429
    reason = "robots.txt unreachable (HTTP 429); RFC 9309 2.3.1.4 assumes complete disallow"
    assert report.rows and all(r.robots_allowed is False and r.matched_rule == reason for r in report.rows)


@pytest.mark.asyncio
async def test_access_matrix_treats_too_many_redirects_as_unavailable(httpx_mock):
    httpx_mock.add_exception(httpx.TooManyRedirects("loop"), url="https://site.example/robots.txt")
    async with httpx.AsyncClient() as client:
        report = await access_matrix(client, "https://site.example", ["/p"], probe=False)
    assert report.robots_status is None and report.robots_error == "TooManyRedirects: loop"
    assert report.robots_access == "unavailable"
    assert report.rows and all(r.robots_allowed is True and r.matched_rule is None for r in report.rows)


@pytest.mark.asyncio
async def test_access_matrix_treats_a_final_3xx_without_location_as_unavailable(httpx_mock):
    httpx_mock.add_response(url="https://site.example/robots.txt", status_code=302)
    async with httpx.AsyncClient() as client:
        report = await access_matrix(client, "https://site.example", ["/p"], probe=False)
    assert report.robots_status == 302 and report.robots_error is None
    assert report.rows and all(r.robots_allowed is True and r.matched_rule is None for r in report.rows)


@pytest.mark.asyncio
async def test_access_matrix_treats_a_failed_robots_fetch_as_complete_disallow(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"), url="https://site.example/robots.txt")
    async with httpx.AsyncClient() as client:
        report = await access_matrix(client, "https://site.example", ["/p"], probe=False)
    assert report.robots_status is None and report.robots_error == "ConnectError: boom"
    assert report.robots_access == "unreachable"
    reason = "robots.txt unreachable (ConnectError: boom); RFC 9309 2.3.1.4 assumes complete disallow"
    assert report.rows and all(r.robots_allowed is False and r.matched_rule == reason for r in report.rows)
