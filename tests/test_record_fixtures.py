"""scripts/record_fixtures.py (G4), loaded by path: it is not part of the package. Its network calls go to a
mock transport here; nothing leaves the machine."""

import importlib.util
import json
from pathlib import Path

import httpx

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "record_fixtures.py"
FIXTURES = Path(__file__).parent / "fixtures"
KEY = "sk-proj-RECORDCHECK-0123456789"


def load_script():
    spec = importlib.util.spec_from_file_location("record_fixtures", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def searched_body():
    return json.loads((FIXTURES / "openai_web_search_searched.json").read_text())["response"]


async def record(tmp_path, answer, environ=None):
    script = load_script()
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        return await script.record_all(environ or {"OPENAI_API_KEY": KEY}, tmp_path, client), script


async def test_a_failure_prints_the_body_with_the_key_masked_and_a_transport_error_by_type(tmp_path, capsys):
    def answer(request):
        body = json.loads(request.content)
        if body.get("tool_choice"):  # the template engine's searched prompt
            raise httpx.RemoteProtocolError(f"closed after Authorization: Bearer {KEY}")
        if "Search the web" in body["input"]:
            return httpx.Response(401, json={"error": {"message": f"Incorrect API key provided: {KEY}."}})
        return httpx.Response(200, json=searched_body())

    code, _ = await record(tmp_path, answer)
    printed = capsys.readouterr().out
    assert code == 1 and KEY not in printed
    masked = '{"error":{"message":"Incorrect API key provided: ***."}}'
    http = f"openai searched: failed with HTTP 401; nothing written. Response body (key masked): {masked}"
    assert http in printed
    assert "openai searched with the template engine: failed with RemoteProtocolError; nothing written" in (
        printed
    )


async def test_an_activation_the_prompt_did_not_ask_for_is_flagged(tmp_path, capsys):
    code, _ = await record(tmp_path, lambda request: httpx.Response(200, json=searched_body()))
    printed = capsys.readouterr().out.splitlines()
    no_search = printed.index(f"Wrote {tmp_path / 'openai_no_search.json'}")
    assert "unexpected for this prompt: activated yes where no search was asked for" in printed[no_search + 2]
    assert not any("unexpected" in line for line in printed[: no_search])  # the searched prompt did search
    assert code == 0


async def test_the_searched_prompt_is_recorded_with_the_template_engine_too(tmp_path, capsys):
    requests = []

    def answer(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=searched_body())

    await record(tmp_path, answer)
    template = json.loads((tmp_path / "openai_searched_template.json").read_text())
    assert template["request"]["tool_choice"] == "required"  # force_search on, as footnote init writes it
    assert template["request"]["max_output_tokens"] == 1200 and template["request"]["max_tool_calls"] == 3
    assert [("tool_choice" in r) for r in requests] == [False, False, True]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "openai_no_search.json", "openai_searched.json", "openai_searched_template.json"
    ]
