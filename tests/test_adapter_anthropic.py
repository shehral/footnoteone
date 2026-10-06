import inspect
import json
from pathlib import Path

import httpx
import pytest

from footnoteone.adapters.anthropic import AnthropicAdapter
from footnoteone.adapters.base import Adapter, RawResponse
from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig

FIX = Path(__file__).parent / "fixtures"
ENDPOINT = "https://api.anthropic.com/v1/messages"
SEARCHED_URLS = [
    "https://www.lesswrong.com/posts/abc/attribution-patching",
    "https://example.org/notes/attribution-patching",
]
SEARCHED_ANSWER = (
    "Attribution patching uses gradients to approximate patching. It is faster than activation patching."
)


def load(name: str) -> RawResponse:
    data = json.loads((FIX / name).read_text())
    data.pop("_doc_note", None)
    return RawResponse.model_validate(data)


def recorded_body(name: str) -> dict:
    return json.loads((FIX / name).read_text())["response"]


def test_protocol():
    assert isinstance(AnthropicAdapter(), Adapter)
    assert inspect.iscoroutinefunction(AnthropicAdapter.call)


def test_parse_searched_keeps_consulted_and_cited_apart():
    obs = AnthropicAdapter().parse(load("anthropic_web_search_searched.json"))
    assert obs.activated == "yes" and obs.search_calls == 1 and obs.status == "ok"
    assert "1 web_search server_tool_use block" in obs.activation_evidence
    assert [s.url for s in obs.consulted] == SEARCHED_URLS
    assert [(s.rank, s.title) for s in obs.consulted] == [(1, "Attribution Patching"), (2, "Notes")]
    assert [s.url for s in obs.cited] == ["https://example.org/notes/attribution-patching"]
    assert (obs.cited[0].rank, obs.cited[0].title) == (1, "Notes")
    assert obs.cited[0].char_start is None and obs.cited[0].char_end is None
    assert obs.consulted[0].provider_field == "web_search_tool_result.content.web_search_result"
    assert obs.cited[0].provider_field == "text.citations.web_search_result_location"
    assert obs.answer_text == SEARCHED_ANSWER
    assert obs.model_returned == "claude-sonnet-4-5-20260101"
    assert (obs.input_tokens, obs.output_tokens) == (1200, 80)


def test_parse_ranks_citations_across_text_blocks_contiguously_in_text_order():
    raw = load("anthropic_web_search_searched.json")
    paper = "https://arxiv.org/abs/2310.10348"

    def citation(url: str, title: str) -> dict:
        return {"type": "web_search_result_location", "url": url, "title": title, "cited_text": "..."}

    first, second = raw.response["content"][2], raw.response["content"][3]
    first["citations"].append(citation(SEARCHED_URLS[0], "Attribution Patching"))
    second["citations"] = [citation(paper, "Paper")]
    obs = AnthropicAdapter().parse(raw)
    assert [s.url for s in obs.cited] == [SEARCHED_URLS[1], SEARCHED_URLS[0], paper]
    assert [s.rank for s in obs.cited] == [1, 2, 3]


def test_parse_no_search():
    obs = AnthropicAdapter().parse(load("anthropic_web_search_no_search.json"))
    assert obs.activated == "no" and obs.consulted == [] and obs.cited == [] and obs.search_calls == 0
    assert obs.status == "ok" and obs.answer_text == "4."


def test_parse_failed_search_is_an_attempted_search_with_no_sources():
    obs = AnthropicAdapter().parse(load("anthropic_web_search_error.json"))
    assert obs.activated == "yes" and obs.status == "ok"
    assert obs.consulted == [] and obs.cited == []
    assert "too_many_requests" in obs.activation_evidence
    # Usage is present and says 0 (a failed search is not billed): it must not fall back to the block count.
    assert obs.search_calls == 0
    assert obs.answer_text == (
        "I'll search for that."
        " The search failed, so from memory: it approximates activation patching with gradients."
    )


def test_parse_error_result_without_its_server_tool_use_still_shows_an_attempted_search():
    raw = load("anthropic_web_search_error.json")
    raw.response["content"] = [b for b in raw.response["content"] if b["type"] != "server_tool_use"]
    del raw.response["usage"]["server_tool_use"]
    obs = AnthropicAdapter().parse(raw)
    assert obs.activated == "yes" and obs.search_calls == 0
    assert "too_many_requests" in obs.activation_evidence


def test_parse_max_uses_error_after_a_search_keeps_its_sources_and_records_the_code():
    raw = load("anthropic_web_search_searched.json")
    raw.request["tools"][0]["max_uses"] = 1  # so a second search is refused with max_uses_exceeded
    search = {"type": "server_tool_use", "id": "srvtoolu_2", "name": "web_search", "input": {"query": "q2"}}
    error = {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}
    raw.response["content"][2:2] = [
        search,
        {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_2", "content": error},
    ]
    obs = AnthropicAdapter().parse(raw)
    assert [s.url for s in obs.consulted] == SEARCHED_URLS
    assert [s.rank for s in obs.consulted] == [1, 2]
    assert "max_uses_exceeded" in obs.activation_evidence
    assert obs.search_calls == 1  # usage still reports the one billed search


def test_parse_counts_failed_searches_without_changing_activation():
    obs = AnthropicAdapter().parse(load("anthropic_web_search_error.json"))
    assert (obs.failed_searches, obs.activated) == (1, "yes")  # activation unchanged pending a METRICS ruling
    raw = load("anthropic_web_search_searched.json")
    assert AnthropicAdapter().parse(raw).failed_searches == 0
    error = {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}
    raw.response["content"][2:2] = [
        {"type": "server_tool_use", "id": "srvtoolu_2", "name": "web_search", "input": {"query": "q2"}},
        {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_2", "content": error},
    ]
    obs = AnthropicAdapter().parse(raw)
    assert obs.failed_searches == 1 and [s.url for s in obs.consulted] == SEARCHED_URLS


def test_parse_dedupes_consulted_urls_across_result_blocks_keeping_the_first_rank():
    raw = load("anthropic_web_search_searched.json")
    paper = "https://arxiv.org/abs/2310.10348"
    results = [
        {"type": "web_search_result", "url": SEARCHED_URLS[1], "title": "Notes again"},  # a repeat
        {"type": "web_search_result", "url": paper, "title": "Paper"},
    ]
    raw.response["content"][2:2] = [
        {"type": "server_tool_use", "id": "srvtoolu_2", "name": "web_search", "input": {"query": "q2"}},
        {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_2", "content": results},
    ]
    raw.response["usage"]["server_tool_use"]["web_search_requests"] = 2
    obs = AnthropicAdapter().parse(raw)
    assert [s.url for s in obs.consulted] == [*SEARCHED_URLS, paper]
    assert [s.rank for s in obs.consulted] == [1, 2, 3]
    assert obs.consulted[1].title == "Notes"  # the first occurrence keeps its rank and title


def test_parse_counts_only_server_tool_use_blocks_named_web_search():
    raw = load("anthropic_web_search_no_search.json")
    # Dynamic filtering (web_search_20260209 and later) adds server_tool_use blocks named code_execution.
    code_run = {"type": "server_tool_use", "id": "srvtoolu_9", "name": "code_execution", "input": {}}
    raw.response["content"].insert(0, code_run)
    del raw.response["usage"]["server_tool_use"]
    obs = AnthropicAdapter().parse(raw)
    assert obs.activated == "no" and obs.search_calls == 0


def test_parse_max_tokens_stop_is_truncated():
    raw = load("anthropic_web_search_searched.json")
    raw.response["stop_reason"] = "max_tokens"
    assert AnthropicAdapter().parse(raw).status == "truncated"


@pytest.mark.parametrize(
    ("stop_reason", "status"),
    [
        ("end_turn", "ok"),
        ("stop_sequence", "ok"),
        ("pause_turn", "truncated"),
        ("refusal", "refused"),
        ("tool_use", "error"),
        ("model_context_window_exceeded", "truncated"),
        (None, "error"),
    ],
)
def test_parse_maps_stop_reason_to_status(stop_reason, status):
    raw = load("anthropic_web_search_searched.json")
    if stop_reason is None:
        del raw.response["stop_reason"]
    else:
        raw.response["stop_reason"] = stop_reason
    obs = AnthropicAdapter().parse(raw)
    assert obs.status == status
    assert obs.activated == "yes"  # the server_tool_use block still shows a search ran


@pytest.mark.parametrize(
    ("stop_reason", "status"),
    [("max_tokens", "truncated"), ("pause_turn", "truncated"), ("tool_use", "error"), (None, "error")],
)
def test_parse_cut_off_or_error_response_without_a_search_is_activated_unknown(stop_reason, status):
    raw = load("anthropic_web_search_no_search.json")
    raw.response["stop_reason"] = stop_reason
    obs = AnthropicAdapter().parse(raw)
    assert (obs.status, obs.activated) == (status, "unknown")
    assert repr(stop_reason) in obs.activation_evidence


def test_parse_refusal_answer_is_the_text_blocks_in_content_order():
    raw = load("anthropic_web_search_searched.json")
    raw.response["stop_reason"] = "refusal"
    obs = AnthropicAdapter().parse(raw)
    assert obs.status == "refused" and obs.answer_text == SEARCHED_ANSWER
    assert obs.activated == "yes" and [s.url for s in obs.consulted] == SEARCHED_URLS
    raw = load("anthropic_web_search_no_search.json")
    raw.response["stop_reason"] = "refusal"
    raw.response["content"] = [{"type": "text", "text": "I can't help with that."}]
    obs = AnthropicAdapter().parse(raw)
    assert (obs.status, obs.activated, obs.answer_text) == ("refused", "no", "I can't help with that.")


def test_parse_tolerates_null_content_usage_text_and_citations():
    raw = load("anthropic_web_search_searched.json")
    raw.response.update(content=None, usage=None, stop_reason=None)
    obs = AnthropicAdapter().parse(raw)
    assert (obs.status, obs.activated) == ("error", "unknown")
    assert obs.consulted == [] and obs.cited == [] and obs.answer_text == ""
    assert obs.input_tokens is None and obs.output_tokens is None and obs.search_calls == 0
    raw = load("anthropic_web_search_searched.json")
    content = raw.response["content"]
    content[1]["content"] = None
    content[2].update(text=None, citations=None)
    raw.response["usage"]["server_tool_use"] = None
    obs = AnthropicAdapter().parse(raw)
    assert (obs.status, obs.activated) == ("ok", "yes")
    assert obs.consulted == [] and obs.cited == []
    assert obs.answer_text == " It is faster than activation patching."
    assert obs.search_calls == 1  # no usage count, so the web_search server_tool_use blocks are counted


def test_parse_skips_blocks_results_and_citations_that_are_not_dicts():
    raw = load("anthropic_web_search_searched.json")
    content = raw.response["content"]
    content[1]["content"].insert(0, "junk")
    content[2]["citations"].insert(0, 7)
    raw.response["content"] = ["junk", None, 3, *content]
    obs = AnthropicAdapter().parse(raw)
    assert [s.url for s in obs.consulted] == SEARCHED_URLS
    assert [s.url for s in obs.cited] == [SEARCHED_URLS[1]]
    assert obs.answer_text == SEARCHED_ANSWER and obs.activated == "yes"


def test_parse_reads_content_that_is_a_string_as_no_content():
    raw = load("anthropic_web_search_searched.json")
    raw.response["content"][1]["content"] = "no results"
    obs = AnthropicAdapter().parse(raw)
    assert obs.consulted == [] and obs.answer_text == SEARCHED_ANSWER
    raw.response["content"] = "Attribution patching is fast."
    obs = AnthropicAdapter().parse(raw)
    assert obs.consulted == [] and obs.cited == [] and obs.answer_text == ""
    assert obs.search_calls == 1  # usage still reports the billed search


def test_parse_maps_a_stop_reason_that_is_an_object_to_error():
    raw = load("anthropic_web_search_searched.json")
    raw.response["stop_reason"] = {"type": "end_turn"}
    obs = AnthropicAdapter().parse(raw)
    assert obs.status == "error" and obs.activated == "yes"


def test_parse_treats_string_token_counts_and_a_numeric_model_as_unreported():
    raw = load("anthropic_web_search_searched.json")
    raw.response["usage"].update(input_tokens="1200", output_tokens="many")
    raw.response["model"] = 7
    obs = AnthropicAdapter().parse(raw)
    assert (obs.input_tokens, obs.output_tokens, obs.model_returned) == (None, None, None)


@pytest.mark.parametrize("bad", ["one", -2])
def test_parse_treats_a_malformed_web_search_count_as_unreported(bad):
    raw = load("anthropic_web_search_no_search.json")
    raw.response["usage"]["server_tool_use"]["web_search_requests"] = bad
    obs = AnthropicAdapter().parse(raw)
    assert obs.search_calls == 0 and obs.activated == "no"
    price = AnthropicAdapter().price(obs, PriceTable.load())
    assert price == pytest.approx(30 / 1e6 * 3.00 + 2 / 1e6 * 15.00)  # tokens only, never negative
    raw = load("anthropic_web_search_searched.json")
    raw.response["usage"]["server_tool_use"]["web_search_requests"] = bad
    assert AnthropicAdapter().parse(raw).search_calls == 1  # unreported: the server_tool_use blocks count


def test_parse_skips_urls_titles_and_texts_of_the_wrong_type():
    raw = load("anthropic_web_search_searched.json")
    content = raw.response["content"]
    content[1]["content"][0]["title"] = 5
    listed = {"type": "web_search_result", "url": ["https://example.net/x"], "title": "X"}
    content[1]["content"].append(listed)
    content[2]["citations"].append({"type": "web_search_result_location", "url": 5, "title": "Five"})
    content[3]["text"] = 12
    obs = AnthropicAdapter().parse(raw)
    assert [s.url for s in obs.consulted] == SEARCHED_URLS and obs.consulted[0].title is None
    assert [s.url for s in obs.cited] == [SEARCHED_URLS[1]]
    assert obs.answer_text == "Attribution patching uses gradients to approximate patching."


def test_price_counts_searches_from_usage():
    obs = AnthropicAdapter().parse(load("anthropic_web_search_searched.json"))
    table = PriceTable.load()
    assert table.is_known("anthropic", obs.model_returned)  # the dated snapshot resolves to its own row
    expected = 1 * 0.010 + 1200 / 1e6 * 3.00 + 80 / 1e6 * 15.00
    assert AnthropicAdapter().price(obs, table) == pytest.approx(expected)


def test_price_does_not_bill_a_failed_search():
    obs = AnthropicAdapter().parse(load("anthropic_web_search_error.json"))
    expected = 900 / 1e6 * 3.00 + 40 / 1e6 * 15.00
    assert AnthropicAdapter().price(obs, PriceTable.load()) == pytest.approx(expected)


def test_parse_carries_the_requested_model():
    assert AnthropicAdapter().parse(load("anthropic_web_search_searched.json")).model_requested == (
        "claude-sonnet-4-5"
    )


def test_price_resolves_the_returned_model_then_the_requested_one_then_the_default_row():
    rows = {
        "claude-sonnet-4-5": {"input_per_1m": 1.0, "output_per_1m": 0.0},
        "claude-opus-4-1": {"input_per_1m": 2.0, "output_per_1m": 0.0},
        "base": {"input_per_1m": 3.0, "output_per_1m": 0.0},
    }
    provider = {"tool_call_usd_per_1k": 0.0, "default_model": "base", "models": rows}
    table = PriceTable(version="test", providers={"anthropic": provider})
    raw = load("anthropic_web_search_searched.json").model_copy(update={"model_requested": "claude-opus-4-1"})
    del raw.response["model"]
    obs = AnthropicAdapter().parse(raw)  # 1200 input tokens
    assert (obs.model_returned, obs.model_requested) == (None, "claude-opus-4-1")
    assert AnthropicAdapter().price(obs, table) == pytest.approx(1200 / 1e6 * 2.0)  # the requested row
    obs = obs.model_copy(update={"model_returned": "claude-sonnet-4-5-20260101"})
    assert AnthropicAdapter().price(obs, table) == pytest.approx(1200 / 1e6 * 1.0)  # the returned first
    obs = obs.model_copy(update={"model_returned": "claude-haiku-9"})
    assert AnthropicAdapter().price(obs, table) == pytest.approx(1200 / 1e6 * 2.0)  # unknown: requested
    obs = obs.model_copy(update={"model_requested": "claude-haiku-9"})
    assert AnthropicAdapter().price(obs, table) == pytest.approx(1200 / 1e6 * 3.0)  # neither: the default


def test_build_request_sets_tool_and_max_uses():
    engine = EngineConfig(
        provider="anthropic",
        model_requested="claude-sonnet-4-5",
        tool_version="web_search_20250305",
        params={"max_uses": 2},
    )
    req = AnthropicAdapter().build_request("q", engine)
    assert req["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}]
    assert req["messages"][0]["content"] == "q"


def test_build_request_uses_the_configured_tool_version_and_max_tokens():
    engine = EngineConfig(
        provider="anthropic",
        model_requested="claude-sonnet-4-5",
        tool_version="web_search_20260318",
        params={"max_tokens": 2048},
    )
    req = AnthropicAdapter().build_request("q", engine)
    assert req["tools"] == [{"type": "web_search_20260318", "name": "web_search"}]
    assert req["max_tokens"] == 2048 and req["model"] == "claude-sonnet-4-5"


def test_build_request_puts_location_and_domains_inside_the_tool_without_aliasing_params():
    location = {"type": "approximate", "city": "London", "region": "England", "country": "GB"}
    params = {"user_location": location, "allowed_domains": ["example.org"]}
    engine = EngineConfig(provider="anthropic", model_requested="claude-sonnet-4-5", params=params)
    sha = engine.config_sha
    req = AnthropicAdapter().build_request("q", engine)
    assert req["tools"] == [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "allowed_domains": ["example.org"],
            "user_location": location,
        }
    ]
    req["tools"][0]["user_location"]["city"] = "Paris"
    req["tools"][0]["allowed_domains"].append("example.com")
    assert engine.config_sha == sha


def test_build_request_takes_blocked_domains_but_not_both_lists():
    blocked = EngineConfig(
        provider="anthropic", model_requested="claude-sonnet-4-5", params={"blocked_domains": ["example.com"]}
    )
    assert AnthropicAdapter().build_request("q", blocked)["tools"][0]["blocked_domains"] == ["example.com"]
    both = EngineConfig(
        provider="anthropic",
        model_requested="claude-sonnet-4-5",
        params={"allowed_domains": ["example.org"], "blocked_domains": ["example.com"]},
    )
    with pytest.raises(ValueError, match="not both"):
        AnthropicAdapter().build_request("q", both)


@pytest.mark.asyncio
async def test_call_posts_with_api_key_and_version_headers(httpx_mock):
    request_body = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "q"}],
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
    }
    httpx_mock.add_response(
        method="POST",
        url=ENDPOINT,
        json=recorded_body("anthropic_web_search_searched.json"),
        match_headers={"x-api-key": "sk-ant-test", "anthropic-version": "2023-06-01"},
        match_json=request_body,
    )
    engine = EngineConfig(provider="anthropic", model_requested="claude-sonnet-4-5")
    async with httpx.AsyncClient() as client:
        raw = await AnthropicAdapter().call(client, "q", engine, api_key="sk-ant-test")
    assert raw.response["id"] == "msg_1" and raw.request == request_body
    assert (raw.provider, raw.model_requested) == ("anthropic", "claude-sonnet-4-5")


@pytest.mark.asyncio
async def test_call_raises_on_a_server_error(httpx_mock):
    error = {"type": "error", "error": {"type": "api_error", "message": "boom"}}
    httpx_mock.add_response(method="POST", url=ENDPOINT, status_code=500, json=error)
    engine = EngineConfig(provider="anthropic", model_requested="claude-sonnet-4-5")
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await AnthropicAdapter().call(client, "q", engine, api_key="sk-ant-test")


@pytest.mark.asyncio
async def test_as_blob_round_trips_and_never_holds_the_api_key(httpx_mock):
    body = recorded_body("anthropic_web_search_searched.json")
    httpx_mock.add_response(method="POST", url=ENDPOINT, json=body)
    engine = EngineConfig(provider="anthropic", model_requested="claude-sonnet-4-5")
    async with httpx.AsyncClient() as client:
        raw = await AnthropicAdapter().call(client, "q", engine, api_key="sk-ant-test")
    blob = raw.as_blob()
    assert "sk-ant-test" not in json.dumps(blob)
    again = RawResponse.model_validate(json.loads(json.dumps(blob)))
    assert again == raw
    assert AnthropicAdapter().parse(again) == AnthropicAdapter().parse(raw)
