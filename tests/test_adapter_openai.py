import inspect
import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from footnoteone.adapters.base import Adapter, RawResponse
from footnoteone.adapters.openai import OpenAIAdapter
from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig

FIX = Path(__file__).parent / "fixtures"
ENDPOINT = "https://api.openai.com/v1/responses"


def load(name: str) -> RawResponse:
    data = json.loads((FIX / name).read_text())
    data.pop("_doc_note", None)
    return RawResponse.model_validate(data)


def recorded_body(name: str) -> dict:
    return json.loads((FIX / name).read_text())["response"]


def test_adapter_satisfies_protocol():
    assert isinstance(OpenAIAdapter(), Adapter)
    assert inspect.iscoroutinefunction(OpenAIAdapter.call)


def test_raw_response_rejects_naive_fetched_at():
    fields = dict(provider="openai", model_requested="gpt-5-mini", request={}, response={})
    assert RawResponse(**fields).fetched_at.tzinfo is not None
    with pytest.raises(ValidationError):
        RawResponse(**fields, fetched_at=datetime(2026, 10, 5, 12, 0))


def test_parse_searched_response_separates_consulted_from_cited():
    obs = OpenAIAdapter().parse(load("openai_web_search_searched.json"))
    assert obs.activated == "yes" and "web_search_call" in obs.activation_evidence
    assert [s.url for s in obs.consulted] == [
        "https://example.org/notes/attribution-patching",
        "https://www.lesswrong.com/posts/abc/attribution-patching",
        "https://arxiv.org/abs/2310.10348",
        "https://example.net/mechanistic-interpretability/attribution-patching",
    ]
    assert [s.rank for s in obs.consulted] == [1, 2, 3, 4]
    assert [s.url for s in obs.cited] == [
        "https://www.lesswrong.com/posts/abc/attribution-patching",
        "https://example.org/notes/attribution-patching?utm_source=chatgpt.com",
    ]
    assert obs.cited[0].char_start == 0 and obs.cited[0].char_end == 64
    assert obs.cited[0].provider_field == "message.content.annotations.url_citation"
    assert obs.consulted[0].provider_field == "web_search_call.action.sources"
    assert obs.consulted[-1].provider_field == "web_search_call.action.open_page.url"
    assert obs.model_returned == "gpt-5-mini-2026-01-01"
    # Two web_search_call items (a search, then an open_page): each counts as a call.
    assert (obs.input_tokens, obs.output_tokens, obs.search_calls) == (812, 96, 2)
    assert obs.answer_text.startswith("Attribution patching")


def test_parse_dedupes_consulted_urls_and_ranks_opened_pages_after_sources():
    raw = load("openai_web_search_searched.json")
    output = raw.response["output"]
    opened = next(item for item in output if (item.get("action") or {}).get("type") == "open_page")
    opened["action"]["url"] = "https://arxiv.org/abs/2310.10348"  # repeats a sources url
    found = {
        "type": "web_search_call", "id": "ws_0", "status": "completed",
        "action": {
            "type": "find_in_page", "pattern": "gradient", "url": "https://example.net/attribution-faq"
        },
    }
    output.insert(0, found)  # before the search item, yet still ranked after every sources url
    obs = OpenAIAdapter().parse(raw)
    assert [s.url for s in obs.consulted] == [
        "https://example.org/notes/attribution-patching",
        "https://www.lesswrong.com/posts/abc/attribution-patching",
        "https://arxiv.org/abs/2310.10348",
        "https://example.net/attribution-faq",
    ]
    assert [s.rank for s in obs.consulted] == [1, 2, 3, 4]
    assert obs.consulted[2].provider_field == "web_search_call.action.sources"  # the first rank is kept
    assert obs.consulted[3].provider_field == "web_search_call.action.find_in_page.url"
    assert obs.search_calls == 3


def test_parse_no_search_response_is_activated_no_with_empty_sets():
    obs = OpenAIAdapter().parse(load("openai_web_search_no_search.json"))
    assert obs.activated == "no"
    assert obs.consulted == [] and obs.cited == []
    assert obs.search_calls == 0 and obs.status == "ok"


def test_parse_refusal_part_is_refused_with_the_refusal_text():
    obs = OpenAIAdapter().parse(load("openai_web_search_refusal.json"))
    assert obs.status == "refused"
    assert obs.answer_text == "I can't help with writing fake reviews."
    assert obs.cited == []
    assert obs.activated == "yes" and obs.search_calls == 1
    assert [s.url for s in obs.consulted] == ["https://example.org/course"]


def test_parse_mixed_refusal_then_cited_text_keeps_order_and_offsets():
    raw = load("openai_web_search_refusal.json")
    refusal = "I can't help with writing fake reviews."
    text = "The course page shows its real reviews, which you can quote."
    span = "its real reviews"
    start = text.index(span)
    citation = {
        "type": "url_citation", "url": "https://example.org/course", "title": "Course",
        "start_index": start, "end_index": start + len(span),
    }
    raw.response["output"][-1]["content"].append(
        {"type": "output_text", "text": text, "annotations": [citation]}
    )
    obs = OpenAIAdapter().parse(raw)
    assert obs.status == "refused"
    assert obs.answer_text == refusal + "\n" + text
    cited = obs.cited[0]
    assert obs.answer_text[cited.char_start : cited.char_end] == span


def test_parse_counts_failed_web_search_calls_without_changing_activation():
    failed = {"type": "web_search_call", "id": "ws_9", "status": "failed", "action": {"type": "search"}}
    raw = load("openai_web_search_searched.json")
    raw.response["output"].insert(1, failed)
    obs = OpenAIAdapter().parse(raw)
    assert obs.failed_searches == 1 and obs.search_calls == 3
    assert OpenAIAdapter().parse(load("openai_web_search_searched.json")).failed_searches == 0
    raw = load("openai_web_search_no_search.json")
    raw.response["output"].insert(0, failed)
    obs = OpenAIAdapter().parse(raw)
    assert (obs.failed_searches, obs.activated) == (1, "yes")  # activation unchanged pending a METRICS ruling


def test_parse_marks_incomplete_as_truncated():
    raw = load("openai_web_search_searched.json")
    raw.response["status"] = "incomplete"
    assert OpenAIAdapter().parse(raw).status == "truncated"


@pytest.mark.parametrize("status", ["cancelled", "failed", None])
def test_parse_maps_failed_unknown_and_missing_statuses_to_error(status):
    raw = load("openai_web_search_searched.json")
    if status is None:
        del raw.response["status"]
    else:
        raw.response["status"] = status
    obs = OpenAIAdapter().parse(raw)
    assert obs.status == "error"
    assert obs.activated == "yes"  # the web_search_call items still show a search ran


def test_parse_error_response_without_search_items_is_activated_unknown():
    raw = load("openai_web_search_no_search.json")
    raw.response["status"] = "cancelled"
    obs = OpenAIAdapter().parse(raw)
    assert obs.status == "error" and obs.activated == "unknown"


def test_parse_truncated_response_without_search_items_is_activated_unknown():
    raw = load("openai_web_search_no_search.json")
    raw.response["status"] = "incomplete"
    obs = OpenAIAdapter().parse(raw)
    assert obs.status == "truncated" and obs.activated == "unknown"  # it may have stopped before searching
    assert "'incomplete'" in obs.activation_evidence


def test_parse_tolerates_null_output_and_null_content():
    raw = load("openai_web_search_searched.json")
    raw.response["output"] = None
    raw.response["status"] = "failed"
    obs = OpenAIAdapter().parse(raw)
    assert obs.status == "error" and obs.activated == "unknown"
    assert obs.consulted == [] and obs.cited == [] and obs.answer_text == ""
    raw = load("openai_web_search_no_search.json")
    raw.response["output"][0]["content"] = None
    obs = OpenAIAdapter().parse(raw)
    assert obs.status == "ok" and obs.answer_text == "" and obs.cited == []


def test_parse_skips_output_items_parts_and_entries_that_are_not_dicts():
    raw = load("openai_web_search_searched.json")
    search, opened, message = raw.response["output"]
    search["action"]["sources"].insert(0, "junk")
    message["content"].insert(0, 7)
    message["content"][1]["annotations"].insert(0, None)
    raw.response["output"] = ["junk", None, 3, search, opened, message]
    obs = OpenAIAdapter().parse(raw)
    clean = OpenAIAdapter().parse(load("openai_web_search_searched.json"))
    assert obs.consulted == clean.consulted and obs.cited == clean.cited
    assert obs.answer_text == clean.answer_text and obs.search_calls == 2


def test_parse_reads_an_output_that_is_a_string_as_no_output():
    raw = load("openai_web_search_searched.json")
    raw.response["output"] = "Attribution patching is fast."
    obs = OpenAIAdapter().parse(raw)
    assert obs.consulted == [] and obs.cited == [] and obs.answer_text == "" and obs.search_calls == 0


def test_parse_maps_a_status_that_is_an_object_to_error():
    raw = load("openai_web_search_searched.json")
    raw.response["status"] = {"state": "completed"}
    obs = OpenAIAdapter().parse(raw)
    assert obs.status == "error" and obs.activated == "yes"


def test_parse_treats_string_token_counts_and_a_numeric_model_as_unreported():
    raw = load("openai_web_search_searched.json")
    raw.response["usage"] = {"input_tokens": "812", "output_tokens": "many"}
    raw.response["model"] = 5
    obs = OpenAIAdapter().parse(raw)
    assert (obs.input_tokens, obs.output_tokens, obs.model_returned) == (None, None, None)


def test_parse_drops_a_citation_offset_that_is_a_string():
    raw = load("openai_web_search_searched.json")
    raw.response["output"][-1]["content"][0]["annotations"][0]["start_index"] = "0"
    obs = OpenAIAdapter().parse(raw)
    assert (obs.cited[0].char_start, obs.cited[0].char_end) == (None, 64)


def test_parse_skips_urls_titles_and_texts_of_the_wrong_type():
    raw = load("openai_web_search_searched.json")
    search, opened, message = raw.response["output"]
    search["action"]["sources"].append({"type": "url", "url": 5})
    opened["action"]["url"] = ["https://example.net/listed"]
    part = message["content"][0]
    part["annotations"][0]["title"] = 5
    linked = {"type": "url_citation", "url": {"href": "x"}, "start_index": 0, "end_index": 1}
    part["annotations"].append(linked)
    message["content"].append({"type": "output_text", "text": 12, "annotations": []})
    obs = OpenAIAdapter().parse(raw)
    clean = OpenAIAdapter().parse(load("openai_web_search_searched.json"))
    assert obs.consulted == clean.consulted[:3]  # the opened page's url is not a string
    assert [s.url for s in obs.cited] == [s.url for s in clean.cited] and obs.cited[0].title is None
    assert obs.answer_text == clean.answer_text + "\n"  # a text that is not a string reads as empty


def test_parse_shifts_citation_offsets_to_index_the_joined_answer_text():
    raw = load("openai_web_search_searched.json")
    first = "Activation patching swaps activations between two runs."
    second = "Attribution patching estimates it by gradients."
    citation = {
        "type": "url_citation", "url": "https://example.org/notes/attribution-patching", "title": "Notes",
        "start_index": 0, "end_index": len(second),
    }
    raw.response["output"][-1]["content"] = [
        {"type": "output_text", "text": first, "annotations": []},
        {"type": "output_text", "text": second, "annotations": [citation]},
    ]
    obs = OpenAIAdapter().parse(raw)
    assert obs.answer_text == first + "\n" + second
    cited = obs.cited[0]
    assert (cited.char_start, cited.char_end) == (len(first) + 1, len(first) + 1 + len(second))
    assert obs.answer_text[cited.char_start : cited.char_end] == second


def test_price_is_tool_fee_plus_tokens():
    obs = OpenAIAdapter().parse(load("openai_web_search_searched.json"))
    table = PriceTable.load()
    assert table.is_known("openai", obs.model_returned)  # the dated snapshot resolves to the gpt-5-mini row
    expected = 2 * 0.010 + 812 / 1e6 * 0.25 + 96 / 1e6 * 2.00  # two web_search_call items
    assert OpenAIAdapter().price(obs, table) == pytest.approx(expected)


def test_parse_carries_the_requested_model():
    assert OpenAIAdapter().parse(load("openai_web_search_searched.json")).model_requested == "gpt-5-mini"


def test_price_resolves_the_returned_model_then_the_requested_one_then_the_default_row():
    rows = {
        "gpt-5-mini": {"input_per_1m": 1.0, "output_per_1m": 0.0},
        "gpt-5": {"input_per_1m": 2.0, "output_per_1m": 0.0},
        "base": {"input_per_1m": 3.0, "output_per_1m": 0.0},
    }
    provider = {"tool_call_usd_per_1k": 0.0, "default_model": "base", "models": rows}
    table = PriceTable(version="test", providers={"openai": provider})
    raw = load("openai_web_search_searched.json").model_copy(update={"model_requested": "gpt-5"})
    del raw.response["model"]
    obs = OpenAIAdapter().parse(raw)  # 812 input tokens
    assert (obs.model_returned, obs.model_requested) == (None, "gpt-5")
    assert OpenAIAdapter().price(obs, table) == pytest.approx(812 / 1e6 * 2.0)  # the requested model's row
    obs = obs.model_copy(update={"model_returned": "gpt-5-mini-2026-01-01"})
    assert OpenAIAdapter().price(obs, table) == pytest.approx(812 / 1e6 * 1.0)  # the returned model first
    obs = obs.model_copy(update={"model_returned": "o9-preview"})
    assert OpenAIAdapter().price(obs, table) == pytest.approx(812 / 1e6 * 2.0)  # unknown: the requested one
    obs = obs.model_copy(update={"model_requested": "o9"})
    assert OpenAIAdapter().price(obs, table) == pytest.approx(812 / 1e6 * 3.0)  # neither known: the default


def test_build_request_includes_sources_and_forced_search_params():
    engine = EngineConfig(provider="openai", model_requested="gpt-5-mini", params={"force_search": True})
    req = OpenAIAdapter().build_request("q", engine)
    assert req["model"] == "gpt-5-mini" and req["input"] == "q"
    assert {"type": "web_search"} in req["tools"] or req["tools"][0]["type"] == "web_search"
    assert "web_search_call.action.sources" in req["include"]
    # The web search guide (read 2026-10-05) forces search with tool_choice "required"; the SDK's
    # hosted tool_choice types list web_search_preview but not web_search.
    assert req["tool_choice"] == "required"


def test_build_request_puts_user_location_inside_the_tool_without_aliasing_params():
    location = {"type": "approximate", "country": "GB", "city": "London", "region": "London"}
    engine = EngineConfig(provider="openai", model_requested="gpt-5-mini", params={"user_location": location})
    sha = engine.config_sha
    req = OpenAIAdapter().build_request("q", engine)
    assert req["tools"] == [{"type": "web_search", "user_location": location}]
    assert "tool_choice" not in req  # search stays optional unless force_search is set
    req["tools"][0]["user_location"]["city"] = "Paris"
    assert engine.config_sha == sha


@pytest.mark.asyncio
async def test_call_posts_to_responses_endpoint_with_bearer(httpx_mock):
    request_body = {
        "model": "gpt-5-mini",
        "input": "q",
        "tools": [{"type": "web_search"}],
        "include": ["web_search_call.action.sources"],
    }
    httpx_mock.add_response(
        method="POST", url=ENDPOINT, json=recorded_body("openai_web_search_searched.json"),
        match_headers={"Authorization": "Bearer sk-test"}, match_json=request_body,
    )
    engine = EngineConfig(provider="openai", model_requested="gpt-5-mini")
    async with httpx.AsyncClient() as client:
        raw = await OpenAIAdapter().call(client, "q", engine, api_key="sk-test")
    assert raw.response["id"] == "resp_1" and raw.request == request_body


@pytest.mark.asyncio
async def test_call_raises_on_a_server_error(httpx_mock):
    httpx_mock.add_response(method="POST", url=ENDPOINT, status_code=500, json={"error": {"message": "boom"}})
    engine = EngineConfig(provider="openai", model_requested="gpt-5-mini")
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await OpenAIAdapter().call(client, "q", engine, api_key="sk-test")


@pytest.mark.asyncio
async def test_as_blob_round_trips_and_never_holds_the_api_key(httpx_mock):
    body = recorded_body("openai_web_search_searched.json")
    httpx_mock.add_response(method="POST", url=ENDPOINT, json=body)
    engine = EngineConfig(provider="openai", model_requested="gpt-5-mini")
    async with httpx.AsyncClient() as client:
        raw = await OpenAIAdapter().call(client, "q", engine, api_key="sk-test")
    blob = raw.as_blob()
    assert "sk-test" not in json.dumps(blob)
    again = RawResponse.model_validate(json.loads(json.dumps(blob)))
    assert again == raw
    assert OpenAIAdapter().parse(again) == OpenAIAdapter().parse(raw)
