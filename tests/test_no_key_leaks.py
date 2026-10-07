"""No key leaks: one burst through `footnote run` with the three real adapters on a mock transport, then every
file the project holds and everything `footnote run`, `doctor`, `plan` and `report` print is searched for the
keys. The transport answers from the recorded-shape fixtures, except one 401 whose body echoes the key and one
transport error whose message quotes the key's header, the two places a provider or a library hands a key
back."""

import json
from pathlib import Path

import httpx
from typer.testing import CliRunner

from footnoteone.cli import app
from footnoteone.config import write_templates
from footnoteone.schema import Run
from footnoteone.store import JsonlStore

FIXTURES = Path(__file__).parent / "fixtures"
KEYS = {
    "OPENAI_API_KEY": "sk-proj-LEAKCHECK-openai-0123456789",
    "ANTHROPIC_API_KEY": "sk-ant-LEAKCHECK-anthropic-0123456789",
    "PERPLEXITY_API_KEY": "pplx-LEAKCHECK-perplexity-0123456789",
}
BODIES = {
    "api.openai.com": "openai_web_search_searched.json",
    "api.anthropic.com": "anthropic_web_search_searched.json",
    "api.perplexity.ai": "perplexity_agent_searched.json",
}


class Provider:
    """The mock transport's handler: each host's second request fails in its own way, the rest answer 200
    with the host's fixture body. `sent` keeps the key header of every request, to show the keys travelled."""

    def __init__(self):
        self.bodies = {
            host: json.loads((FIXTURES / name).read_text())["response"] for host, name in BODIES.items()
        }
        self.seen = {host: 0 for host in BODIES}
        self.sent: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        header = request.headers.get("authorization") or request.headers.get("x-api-key") or ""
        self.sent.append(header)
        self.seen[host] += 1
        if host == "api.openai.com" and self.seen[host] == 2:
            echo = {"error": {"message": f"Incorrect API key provided: {header.removeprefix('Bearer ')}"}}
            return httpx.Response(401, json=echo)
        if host == "api.anthropic.com" and self.seen[host] == 2:
            raise httpx.RemoteProtocolError(f"peer closed the connection after x-api-key: {header}")
        return httpx.Response(200, json=self.bodies[host])


def test_no_key_reaches_a_file_a_raw_response_or_any_printed_line(tmp_path, monkeypatch):
    write_templates(tmp_path, "https://example.org")
    for name, value in KEYS.items():
        monkeypatch.setenv(name, value)
    provider = Provider()
    real_client = httpx.AsyncClient

    class MockedClient(real_client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **{**kwargs, "transport": httpx.MockTransport(provider)})

    monkeypatch.setattr(httpx, "AsyncClient", MockedClient)  # the client run_design builds for itself
    root = ["--root", str(tmp_path)]
    printed = {
        "run --dry-run": CliRunner().invoke(app, ["run", *root, "--label", "leak", "--dry-run"]),
        "run": CliRunner().invoke(app, ["run", *root, "--label", "leak"]),
        "doctor": CliRunner().invoke(app, ["doctor", *root]),
        "plan": CliRunner().invoke(app, ["plan", *root]),
        "report": CliRunner().invoke(app, ["report", *root, "--label", "leak"]),
    }
    assert printed["run"].exit_code == 0, printed["run"].output
    assert printed["report"].exit_code == 0, printed["report"].output

    # The keys did travel, in headers, and both failures happened.
    assert set(KEYS.values()) <= {header.removeprefix("Bearer ") for header in provider.sent}
    runs = {run.id: run for run in JsonlStore(tmp_path / ".footnote").iter("runs", Run)}.values()
    evidence = [run.activation_evidence for run in runs]
    assert any("HTTP 401" in text for text in evidence)
    assert any("RemoteProtocolError" in text for text in evidence)

    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    blobs = list((tmp_path / ".footnote" / "raw").glob("*.json"))
    assert blobs and (tmp_path / "reports" / "leak" / "report.html") in files
    for key in KEYS.values():
        for path in files:  # the store, every raw response blob, the reports and the project files
            assert key.encode() not in path.read_bytes(), path
        for command, result in printed.items():
            assert key not in result.stdout + result.stderr, command
