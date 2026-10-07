import re

import httpx
import pytest
import typer
from typer.testing import CliRunner

from footnoteone.audit.robots import load_bots
from footnoteone.commands.common import fail, load_project
from footnoteone.commands.crawl_check import register
from footnoteone.config import write_templates
from footnoteone.library import write_pages
from footnoteone.schema import Page

GPTBOT_UA = "Mozilla/5.0 (compatible; GPTBot; +https://developers.openai.com/api/docs/bots)"
FOOTER = (
    "Probe = what a request carrying that user-agent string from this machine receives; bot-verifying "
    "firewalls may answer the real bot differently. Unreachable robots.txt means complete disallow "
    "(RFC 9309)."
)
BOTS = {bot.token for bot in load_bots()}


def app_with_command():
    app = typer.Typer()
    register(app)
    return app


def library(*urls):
    return [Page(id=Page.id_for(url), url=url, canonical_url=url, source="sitemap") for url in urls]


def table_rows(output):
    """The table body as cells: bot, purpose, path, robots verdict, matched rule, probe."""
    return [re.split(r" {2,}", line) for line in output.splitlines() if line.split(" ")[0] in BOTS]


def paths_of(output, bot="GPTBot"):
    return [cells[2] for cells in table_rows(output) if cells[0] == bot]


def test_crawl_check_prints_matrix_without_probes(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    httpx_mock.add_response(url="https://example.org/robots.txt", text="User-agent: GPTBot\nDisallow: /\n")
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--no-probe"])
    assert result.exit_code == 0, result.output
    assert "robots.txt: HTTP 200" in result.output
    assert "GPTBot" in result.output and "blocked" in result.output
    assert "Disallow: / (line 2)" in result.output and "not probed" in result.output
    assert "OAI-SearchBot" in result.output and "allowed" in result.output
    assert "\u2014" not in result.output


def test_crawl_check_probes_given_paths(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    httpx_mock.add_response(url="https://example.org/robots.txt", status_code=404)
    httpx_mock.add_response(
        url="https://example.org/p", status_code=403, match_headers={"User-Agent": GPTBOT_UA}
    )
    # Every other crawling bot gets this one; the GPTBot response above is registered first so it wins.
    httpx_mock.add_response(url="https://example.org/p", status_code=200, is_reusable=True)
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--path", "/p"])
    assert result.exit_code == 0, result.output
    assert "unavailable" in result.output and "403" in result.output and "200" in result.output
    probes = {cells[0]: cells[5] for cells in table_rows(result.output)}
    assert probes["GPTBot"] == "403" and probes["OAI-SearchBot"] == probes["Googlebot"] == "200"


def test_crawl_check_without_config_exits_2(tmp_path):
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path)])
    assert result.exit_code == 2 and "footnote.toml" in (result.output + str(result.exception or ""))


def test_crawl_check_prints_header_table_and_the_probe_caveat_last(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    httpx_mock.add_response(
        url="https://example.org/robots.txt", text="User-agent: GPTBot\nDisallow: /private\n"
    )
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--no-probe"])
    lines = result.output.splitlines()
    assert lines[0] == "robots.txt: HTTP 200, success"
    assert re.split(r" {2,}", lines[2]) == ["bot", "purpose", "path", "robots", "matched rule", "probe"]
    assert lines[-1] == FOOTER
    assert len(table_rows(result.output)) == len(BOTS)  # one row per bot for the default path "/"
    assert {cells[3] for cells in table_rows(result.output)} == {"allowed"}
    assert {cells[4] for cells in table_rows(result.output)} == {"no matching rule"}


def test_crawl_check_reads_the_project_from_the_current_folder_by_default(tmp_path, monkeypatch, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    monkeypatch.chdir(tmp_path)
    httpx_mock.add_response(url="https://example.org/robots.txt", text="User-agent: *\nAllow: /\n")
    result = CliRunner().invoke(app_with_command(), ["--no-probe"])
    assert result.exit_code == 0, result.output
    assert "robots.txt: HTTP 200, success" in result.output


def test_crawl_check_defaults_to_home_and_ten_library_pages_on_the_site_host(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    urls = ["https://other.example/x", "https://blog.example.org/y"]
    urls += [f"https://example.org/p{i}" for i in range(12)]
    write_pages(tmp_path, library(*urls))
    httpx_mock.add_response(url="https://example.org/robots.txt", text="User-agent: *\nAllow: /\n")
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--no-probe"])
    assert result.exit_code == 0, result.output
    assert paths_of(result.output) == ["/"] + [f"/p{i}" for i in range(10)]


def test_crawl_check_lists_the_home_page_once_and_keeps_a_page_query(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    write_pages(tmp_path, library("https://example.org/", "https://example.org/post?id=3#top"))
    httpx_mock.add_response(url="https://example.org/robots.txt", text="User-agent: *\nAllow: /\n")
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--no-probe"])
    assert paths_of(result.output) == ["/", "/post?id=3"]


def test_crawl_check_given_paths_replace_the_library_pages(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    write_pages(tmp_path, library("https://example.org/p0"))
    httpx_mock.add_response(url="https://example.org/robots.txt", text="User-agent: *\nAllow: /\n")
    args = ["--root", str(tmp_path), "--no-probe", "--path", "/a", "--path", "/b"]
    result = CliRunner().invoke(app_with_command(), args)
    assert paths_of(result.output) == ["/a", "/b"]


def test_crawl_check_rejects_a_path_without_a_leading_slash_before_any_request(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--path", "posts/a"])
    assert result.exit_code == 2
    assert "path must start with '/'" in result.stderr and "posts/a" in result.stderr
    assert httpx_mock.get_requests() == []


def test_crawl_check_exits_2_on_a_path_that_cannot_be_requested(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    httpx_mock.add_response(url="https://example.org/robots.txt", text="User-agent: *\nAllow: /\n")
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--path", "/a\nb"])
    assert result.exit_code == 2 and "non-printable" in result.stderr


def test_crawl_check_exits_2_on_a_damaged_library(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    (tmp_path / ".footnote").mkdir()
    (tmp_path / ".footnote" / "pages.jsonl").write_text('{"id": 1}\n{"id": 2}\n', encoding="utf-8")
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path)])
    assert result.exit_code == 2 and "pages.jsonl" in result.stderr


def test_crawl_check_exits_0_when_everything_is_blocked(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    httpx_mock.add_response(url="https://example.org/robots.txt", text="User-agent: *\nDisallow: /\n")
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--no-probe"])
    assert result.exit_code == 0, result.output
    assert {cells[3] for cells in table_rows(result.output)} == {"blocked"}
    assert {cells[4] for cells in table_rows(result.output)} == {"Disallow: / (line 2)"}


def test_crawl_check_says_an_unreachable_robots_txt_blocks_everything(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    httpx_mock.add_response(url="https://example.org/robots.txt", status_code=503)
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--no-probe"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == "robots.txt: HTTP 503, unreachable"
    assert {cells[3] for cells in table_rows(result.output)} == {"blocked"}
    rules = [cells[4] for cells in table_rows(result.output)]
    assert all(rule.startswith("robots.txt unreachable (HTTP 503)") for rule in rules)


def test_crawl_check_header_names_the_error_when_robots_txt_cannot_be_fetched(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    httpx_mock.add_exception(httpx.ConnectError("no route"), url="https://example.org/robots.txt")
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--no-probe"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == "robots.txt: ConnectError: no route, unreachable"


def test_crawl_check_probe_column_shows_the_error_and_marks_control_tokens(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    httpx_mock.add_response(url="https://example.org/robots.txt", text="User-agent: *\nAllow: /\n")
    httpx_mock.add_exception(httpx.ConnectError("boom"), url="https://example.org/p", is_reusable=True)
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--path", "/p"])
    assert result.exit_code == 0, result.output
    probes = {cells[0]: cells[5] for cells in table_rows(result.output)}
    assert probes["GPTBot"] == "ConnectError: boom"
    assert probes["Google-Extended"] == probes["Applebot-Extended"] == "not probed: control token"
    assert len(httpx_mock.get_requests(url="https://example.org/p")) == len(BOTS) - 2


def test_crawl_check_truncates_a_long_matched_rule_at_60_characters(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    rule = "/a" + "b" * 100
    httpx_mock.add_response(
        url="https://example.org/robots.txt", text=f"User-agent: GPTBot\nDisallow: {rule}\n"
    )
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--no-probe", "--path", rule])
    cells = next(cells for cells in table_rows(result.output) if cells[0] == "GPTBot")
    assert cells[3] == "blocked" and len(cells[4]) == 60 and cells[4].endswith("...")
    assert cells[4].startswith("Disallow: /ab")


def test_fail_prints_the_message_on_stderr_and_exits_2():
    app = typer.Typer()

    @app.command()
    def broken() -> None:
        fail("it broke")

    result = CliRunner().invoke(app, [])
    assert result.exit_code == 2 and result.stderr == "it broke\n" and result.stdout == ""


def test_load_project_returns_the_config_and_the_intents(tmp_path):
    write_templates(tmp_path, "https://example.org")
    config, intents = load_project(tmp_path)
    assert config.site.url == "https://example.org"
    assert [intent.id for intent in intents] == ["example-topic", "placebo-water"]


def test_load_project_turns_a_config_error_into_exit_2(tmp_path, capsys):
    write_templates(tmp_path, "https://example.org")
    (tmp_path / "intents.yaml").write_text("intents: []\n", encoding="utf-8")
    with pytest.raises(typer.Exit) as raised:
        load_project(tmp_path)
    assert raised.value.exit_code == 2
    assert "intents.yaml" in capsys.readouterr().err
