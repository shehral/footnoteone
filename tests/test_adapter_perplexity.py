import inspect
import json
from pathlib import Path

import httpx
import pytest

from footnoteone.adapters.base import Adapter, RawResponse
from footnoteone.adapters.perplexity import PerplexityAdapter
from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig, Observation

FIX = Path(__file__).parent / "fixtures"
# POST /v1/agent on https://api.perplexity.ai, per https://docs.perplexity.ai/api-reference/agent-post,
# read 2026-10-05.
ENDPOINT = "https://api.perplexity.ai/v1/agent"
SEARCHED = "perplexity_agent_searched.json"
NO_SEARCH = "perplexity_agent_no_search.json"
LESSWRONG = "https://www.lesswrong.com/posts/abc/attribution-patching"
NOTES = "https://example.org/notes/attribution-patching"
ARXIV = "https://arxiv.org/abs/2310.10348"
BLOG = "https://example.net/mechanistic-interpretability/attribution-patching"
# The fixture's 400 input and 60 output tokens and one search at the table's fast row and web_search fee.
FAST_PRICE = 1 * 0.0025 + 400 / 1e6 * 0.40 + 60 / 1e6 * 1.80


def load(name: str) -> RawResponse:
    data = json.loads((FIX / name).read_text())
    data.pop("_doc_note", None)
    return RawResponse.model_validate(data)


def recorded_body(name: str) -> dict:
    return json.loads((FIX / name).read_text())["response"]


def parse_marked(text: str) -> Observation:
    """Parse the searched fixture with its answer replaced by `text` and no annotations, so markers count."""
    raw = load(SEARCHED)
    raw.response["output"][-1]["content"][0].update(text=text, annotations=[])
    return PerplexityAdapter().parse(raw)


def test_protocol():
    assert isinstance(PerplexityAdapter(), Adapter)
    assert inspect.iscoroutinefunction(PerplexityAdapter.call)


def test_parse_searched_separates_search_results_from_citations():
    obs = PerplexityAdapter().parse(load(SEARCHED))
    assert obs.activated == "yes" and obs.search_calls == 1
    assert obs.activation_evidence == (
        "usage.tool_calls_details.search_web.invocation = 1; "
        "1 search_results item(s) holding results or queries"
    )
    assert len(obs.consulted) == 3 and len(obs.cited) == 2
    assert [s.url for s in obs.consulted] == [LESSWRONG, NOTES, ARXIV]
    assert [s.rank for s in obs.consulted] == [1, 2, 3]
    assert [s.url for s in obs.cited] == [LESSWRONG, NOTES]
    assert [s.rank for s in obs.cited] == [1, 2]
    assert obs.consulted[0].provider_field == "search_results.results"
    assert obs.cited[0].provider_field == "message.content.annotations.url_citation"
    assert obs.consulted[0].title == "Attribution Patching" and obs.cited[1].title == "Notes"
    cited, span = obs.cited[1], "One backward pass scores every component at once."
    assert obs.answer_text[cited.char_start : cited.char_end] == span
    assert obs.answer_text.startswith("Attribution patching")
    assert obs.model_requested == "fast" and obs.model_returned == "openai/gpt-5.6-luna"
    assert (obs.input_tokens, obs.output_tokens, obs.status) == (400, 60, "ok")
    assert obs.failed_searches == 0  # the Agent API docs show no failed-search object


def test_parse_never_copies_search_results_into_cited():
    obs = parse_marked("Attribution patching approximates activation patching with gradients.")
    assert obs.cited == [] and len(obs.consulted) == 3 and obs.activated == "yes"


def test_parse_resolves_inline_markers_by_result_id_when_no_annotations_exist():
    # The migration guide's example shape: empty annotations, markers in the text that name a result id.
    raw = load(SEARCHED)
    results = raw.response["output"][0]["results"]
    results[0]["id"], results[1]["id"] = 2, 1  # markers name ids, not positions in the list
    first = "Attribution patching approximates activation patching with gradients.[web:2]"
    second = "It needs one backward pass.[1][web:3] Results hold on larger models.[2][7]"
    raw.response["output"][-1]["content"] = [
        {"type": "output_text", "text": first, "annotations": []},
        {"type": "output_text", "text": second, "annotations": []},
    ]
    obs = PerplexityAdapter().parse(raw)
    assert obs.answer_text == first + "\n" + second
    # Every marker that resolves is its own occurrence, the repeated id 2 included; [7] names no result.
    assert [s.url for s in obs.cited] == [LESSWRONG, NOTES, ARXIV, LESSWRONG]
    assert [s.rank for s in obs.cited] == [1, 2, 3, 4]
    assert [s.title for s in obs.cited] == ["Attribution Patching", "Notes", "arXiv", "Attribution Patching"]
    assert {s.provider_field for s in obs.cited} == {"message.text.inline_marker"}
    spans = [obs.answer_text[s.char_start : s.char_end] for s in obs.cited]
    assert spans == ["[web:2]", "[1]", "[web:3]", "[2]"]
    assert obs.activation_evidence.endswith("; 1 unresolved inline marker(s)")
    assert len(obs.consulted) == 3 and obs.activated == "yes"


def test_parse_records_cited_annotations_per_occurrence():
    raw = load(SEARCHED)
    part = raw.response["output"][-1]["content"][0]
    part["annotations"].insert(1, {**part["annotations"][0], "start_index": 73, "end_index": 122})
    obs = PerplexityAdapter().parse(raw)
    assert [s.url for s in obs.cited] == [LESSWRONG, LESSWRONG, NOTES]
    assert [s.rank for s in obs.cited] == [1, 2, 3]
    assert [(s.char_start, s.char_end) for s in obs.cited] == [(0, 69), (73, 122), (73, 122)]


def test_parse_ignores_inline_markers_when_annotations_exist():
    raw = load(SEARCHED)
    raw.response["output"][-1]["content"][0]["text"] += " It beats automated circuit discovery.[3][9]"
    obs = PerplexityAdapter().parse(raw)
    assert [s.url for s in obs.cited] == [LESSWRONG, NOTES]  # the annotations; [3] and [9] are not read
    assert {s.provider_field for s in obs.cited} == {"message.content.annotations.url_citation"}
    assert "unresolved" not in obs.activation_evidence


def test_parse_never_raises_on_an_oversized_bracketed_number():
    obs = parse_marked("Big.[" + "9" * 5000 + "]")  # int() refuses strings over 4300 digits; ids are int64
    assert obs.cited == [] and "unresolved" not in obs.activation_evidence


@pytest.mark.parametrize(
    "text",
    [
        "Read it with `cache['resid'][1]` after a forward pass.[2]",
        "Run this:\n```python\nprint(cache[1])\n```\nThen compare the two runs.[2]",
        "The page https://example.net/search?q=[1]&page=3 is unrelated.[2]",
        "Compare [1](https://example.net/other) with the notes.[2]",
        "Index with ``cache[1]`` then.[2]",
    ],
    ids=["inline-code", "fenced-block", "url-query", "link-label", "double-backtick-code"],
)
def test_parse_does_not_cite_brackets_in_code_urls_or_link_labels(text):
    obs = parse_marked(text)
    assert text.index("[1]") not in [s.char_start for s in obs.cited]  # that span is not a citation
    assert [s.url for s in obs.cited] == [NOTES]  # only the real closing [2]
    assert obs.cited[0].char_start == text.rindex("[2]")
    assert "unresolved" not in obs.activation_evidence


def test_parse_reads_a_fence_mid_line_as_literal_text():
    obs = parse_marked("Wrap code in ``` fences.[1] Then run it.[2]")
    assert [s.url for s in obs.cited] == [LESSWRONG, NOTES]
    obs = parse_marked("Use `~~~` to fence.[2]")
    assert [s.url for s in obs.cited] == [NOTES]


def test_parse_skips_a_link_reference_definition():
    text = "Claim.[1]\n\n[1]: https://example.net/a\n[2] Notes"
    obs = parse_marked(text)
    assert [s.url for s in obs.cited] == [LESSWRONG, NOTES]  # [1] once and [2] once
    assert [s.char_start for s in obs.cited] == [text.index("[1]"), text.rindex("[2]")]


def test_parse_masks_a_long_run_of_glued_markers_quickly():
    obs = parse_marked("See https://example.net/a" + "[1]" * 5000 + "x")  # the run ends in "x": all URL
    assert obs.cited == [] and "unresolved" not in obs.activation_evidence


def test_parse_resolves_a_marker_glued_to_the_end_of_a_url():
    text = "See https://example.net/paper[1] and https://example.net/notes[2]."
    obs = parse_marked(text)
    assert [s.url for s in obs.cited] == [LESSWRONG, NOTES]
    assert [text[s.char_start : s.char_end] for s in obs.cited] == ["[1]", "[2]"]


def test_parse_marker_spans_index_the_answer_across_message_items():
    raw = load(SEARCHED)
    message = raw.response["output"][-1]
    first, second = "Attribution patching is fast.[1]", "Circuit work uses it.[web:3]"
    message["content"] = [{"type": "output_text", "text": first, "annotations": []}]
    later = {**message, "id": "msg_pplx_1b", "content": [{"type": "output_text", "text": second}]}
    raw.response["output"].append(later)
    obs = PerplexityAdapter().parse(raw)
    assert obs.answer_text == first + "\n" + second
    assert [s.url for s in obs.cited] == [LESSWRONG, ARXIV]
    assert [obs.answer_text[s.char_start : s.char_end] for s in obs.cited] == ["[1]", "[web:3]"]
    assert obs.cited[1].char_start == len(first) + 1 + second.index("[web:3]")


def test_parse_reads_every_search_results_item_in_order_and_dedupes_urls():
    raw = load(SEARCHED)
    later = {
        "type": "search_results",
        "queries": ["attribution patching gradients"],
        "results": [
            {"id": 4, "url": ARXIV, "title": "arXiv", "snippet": "...", "source": "web"},
            {"id": 5, "url": BLOG, "title": "Attribution Patching", "snippet": "...", "source": "web"},
        ],
    }
    raw.response["output"].insert(1, later)
    obs = PerplexityAdapter().parse(raw)
    assert [s.url for s in obs.consulted] == [LESSWRONG, NOTES, ARXIV, BLOG]
    assert [s.rank for s in obs.consulted] == [1, 2, 3, 4]
    assert [s.url for s in obs.cited] == [LESSWRONG, NOTES]


def test_parse_appends_fetched_pages_to_consulted_after_search_results():
    raw = load(SEARCHED)
    fetched = {
        "type": "fetch_url_results",
        "contents": [
            {"url": BLOG, "title": "Attribution Patching", "snippet": "..."},
            {"url": ARXIV, "title": "arXiv", "snippet": "..."},  # also a search result
        ],
    }
    raw.response["output"].insert(0, fetched)  # before the search item, yet ranked after every search result
    obs = PerplexityAdapter().parse(raw)
    assert [s.url for s in obs.consulted] == [LESSWRONG, NOTES, ARXIV, BLOG]
    assert [s.rank for s in obs.consulted] == [1, 2, 3, 4]
    assert obs.consulted[2].provider_field == "search_results.results"  # the first rank is kept
    assert obs.consulted[3].provider_field == "fetch_url_results.contents.url"
    assert obs.consulted[3].title == "Attribution Patching"
    assert obs.activation_evidence.endswith("; 2 page(s) fetched in 1 fetch_url_results item(s)")
    assert obs.activated == "yes" and obs.search_calls == 1  # the reported search count


def test_parse_a_fetch_url_results_item_is_activation_yes_even_with_a_zero_search_count():
    raw = load(NO_SEARCH)  # its usage reports zero search_web invocations
    page = {"url": BLOG, "title": "Attribution Patching", "snippet": "..."}
    raw.response["output"].insert(0, {"type": "fetch_url_results", "contents": [page]})
    obs = PerplexityAdapter().parse(raw)
    assert obs.activated == "yes"  # the agent used a web tool
    assert obs.activation_evidence == "1 page(s) fetched in 1 fetch_url_results item(s)"
    assert obs.search_calls == 0 and [s.url for s in obs.consulted] == [BLOG]


def test_parse_no_search_is_no_with_empty_sets():
    obs = PerplexityAdapter().parse(load(NO_SEARCH))
    assert obs.activated == "no" and obs.consulted == [] and obs.cited == []
    assert obs.search_calls == 0 and obs.status == "ok" and obs.answer_text == "4."


def test_parse_missing_search_results_field_is_unknown():
    raw = load(SEARCHED)
    raw.response["output"] = [item for item in raw.response["output"] if item["type"] != "search_results"]
    del raw.response["usage"]["tool_calls_details"]
    obs = PerplexityAdapter().parse(raw)
    assert obs.activated == "unknown" and obs.search_calls == 0 and obs.consulted == []


def test_parse_empty_search_results_item_without_a_count_is_no():
    raw = load(NO_SEARCH)
    del raw.response["usage"]["tool_calls_details"]
    raw.response["output"].insert(0, {"type": "search_results", "queries": [], "results": []})
    obs = PerplexityAdapter().parse(raw)
    assert obs.activated == "no" and obs.search_calls == 0


def test_parse_search_results_item_with_queries_but_no_results_is_yes():
    raw = load(NO_SEARCH)
    del raw.response["usage"]["tool_calls_details"]
    raw.response["output"].insert(0, {"type": "search_results", "queries": ["2 + 2"], "results": []})
    obs = PerplexityAdapter().parse(raw)
    assert obs.activated == "yes" and obs.consulted == []  # a query was issued, so a search ran
    assert obs.search_calls == 1


def test_parse_search_calls_is_the_invocation_count_else_the_searched_items():
    raw = load(SEARCHED)
    raw.response["usage"]["tool_calls_details"]["search_web"]["invocation"] = 5
    assert PerplexityAdapter().parse(raw).search_calls == 5
    del raw.response["usage"]["tool_calls_details"]
    first_result = raw.response["output"][0]["results"][:1]
    raw.response["output"][1:1] = [
        {"type": "search_results", "queries": ["attribution patching gradients"], "results": first_result},
        {"type": "search_results", "queries": ["attribution patching circuits"], "results": []},
        {"type": "search_results", "queries": [], "results": []},  # empty: not a search
    ]
    obs = PerplexityAdapter().parse(raw)
    assert obs.activated == "yes" and obs.search_calls == 3  # three items hold results or queries
    raw = load(NO_SEARCH)
    del raw.response["usage"]["tool_calls_details"]
    raw.response["output"].insert(0, {"type": "fetch_url_results", "contents": []})
    obs = PerplexityAdapter().parse(raw)
    assert obs.activated == "yes" and obs.search_calls == 1  # at least one once activated


def test_parse_marks_incomplete_as_truncated():
    raw = load(SEARCHED)
    raw.response["status"] = "incomplete"
    obs = PerplexityAdapter().parse(raw)
    assert obs.status == "truncated" and obs.activated == "yes"


@pytest.mark.parametrize("status", ["failed", "cancelled", "in_progress", "expired", None])
def test_parse_maps_failed_unknown_and_missing_statuses_to_error(status):
    raw = load(SEARCHED)
    if status is None:
        del raw.response["status"]
    else:
        raw.response["status"] = status
    obs = PerplexityAdapter().parse(raw)
    assert obs.status == "error"
    assert obs.activated == "yes"  # the search_results item still shows a search ran


@pytest.mark.parametrize(("status", "mapped"), [("failed", "error"), ("incomplete", "truncated")])
def test_parse_error_or_truncated_response_without_search_evidence_is_unknown(status, mapped):
    raw = load(NO_SEARCH)
    raw.response["status"] = status
    obs = PerplexityAdapter().parse(raw)
    assert obs.status == mapped
    assert obs.activated == "unknown"  # even with the zero count: the answer stopped before it finished


def test_parse_tolerates_explicit_nulls():
    raw = load(SEARCHED)
    raw.response.update(output=None, usage=None, status="failed")
    obs = PerplexityAdapter().parse(raw)
    assert obs.status == "error" and obs.activated == "unknown"
    assert obs.consulted == [] and obs.cited == [] and obs.answer_text == ""
    assert obs.input_tokens is None and obs.search_calls == 0
    raw = load(SEARCHED)
    search_item, message = raw.response["output"]
    search_item["results"] = None
    message["content"][0]["annotations"] = None
    raw.response["usage"]["tool_calls_details"] = None
    obs = PerplexityAdapter().parse(raw)
    assert obs.consulted == [] and obs.cited == [] and obs.answer_text.startswith("Attribution patching")
    assert obs.activated == "yes" and obs.search_calls == 1  # the item's queries still show a search
    raw = load(NO_SEARCH)
    raw.response["output"][0]["content"] = None
    raw.response["usage"]["tool_calls_details"]["search_web"] = None
    obs = PerplexityAdapter().parse(raw)
    assert obs.status == "ok" and obs.answer_text == "" and obs.activated == "unknown"
    raw.response["output"].append({"type": "fetch_url_results", "contents": None})
    obs = PerplexityAdapter().parse(raw)
    assert obs.activated == "yes" and obs.consulted == [] and obs.cited == []  # the fetch tool ran


def test_parse_skips_entries_that_are_not_dicts():
    raw = load(SEARCHED)
    search_item, message = raw.response["output"]
    search_item["results"].insert(0, "junk")
    message["content"].insert(0, 7)
    message["content"][1]["annotations"].insert(0, None)
    raw.response["output"] = ["junk", None, 3, search_item, message]
    obs = PerplexityAdapter().parse(raw)
    assert [s.url for s in obs.consulted] == [LESSWRONG, NOTES, ARXIV]
    assert [s.url for s in obs.cited] == [LESSWRONG, NOTES]
    assert obs.answer_text.startswith("Attribution patching") and obs.status == "ok"


@pytest.mark.parametrize("bad", ["one", -1, True, 1.5])
def test_parse_treats_a_malformed_count_as_unreported(bad):
    raw = load(SEARCHED)
    raw.response["usage"]["tool_calls_details"]["search_web"]["invocation"] = bad
    raw.response["usage"]["input_tokens"] = bad
    obs = PerplexityAdapter().parse(raw)
    assert obs.search_calls == 1  # from the one search_results item, as when no count is reported
    assert "invocation" not in obs.activation_evidence and obs.input_tokens is None


def test_parse_resolves_markers_against_ids_sent_as_strings():
    raw = load(SEARCHED)
    for result in raw.response["output"][0]["results"]:
        result["id"] = str(result["id"])
    raw.response["output"][-1]["content"][0]["annotations"] = []
    obs = PerplexityAdapter().parse(raw)
    assert [s.url for s in obs.cited] == [LESSWRONG, NOTES]  # the fixture text's [1] and [2]


def test_parse_does_not_read_non_ascii_digits_as_markers():
    obs = parse_marked("Attribution patching approximates activation patching.[１]")  # a fullwidth 1
    assert obs.cited == [] and "unresolved" not in obs.activation_evidence


def test_parse_tolerates_a_result_without_id_or_url():
    raw = load(SEARCHED)
    results = raw.response["output"][0]["results"]
    results.append({"id": 4, "title": "No url", "snippet": "...", "source": "web"})
    results.append({"url": BLOG, "title": "No id", "snippet": "...", "source": "web"})
    raw.response["output"][-1]["content"][0].update(text="Claim one.[4] Claim two.[1]", annotations=[])
    obs = PerplexityAdapter().parse(raw)
    assert [s.url for s in obs.consulted] == [LESSWRONG, NOTES, ARXIV, BLOG]
    assert [s.url for s in obs.cited] == [LESSWRONG]  # [4] names a result without a url
    assert obs.activation_evidence.endswith("; 1 unresolved inline marker(s)")


def test_parse_shifts_citation_offsets_to_index_the_joined_answer_text():
    raw = load(SEARCHED)
    first = "Activation patching swaps activations between two runs."
    second = "Attribution patching estimates it by gradients."
    citation = {
        "type": "url_citation", "url": NOTES, "title": "Notes", "start_index": 0, "end_index": len(second),
    }
    raw.response["output"][-1]["content"] = [
        {"type": "output_text", "text": first, "annotations": []},
        {"type": "output_text", "text": second, "annotations": [citation]},
    ]
    obs = PerplexityAdapter().parse(raw)
    assert obs.answer_text == first + "\n" + second
    cited = obs.cited[0]
    assert (cited.char_start, cited.char_end) == (len(first) + 1, len(first) + 1 + len(second))
    assert obs.answer_text[cited.char_start : cited.char_end] == second


def test_price():
    obs = PerplexityAdapter().parse(load(SEARCHED))
    table = PriceTable.load()
    assert table.is_known("perplexity", obs.model_requested)  # the fast preset has its own row
    assert not table.is_known("perplexity", obs.model_returned)  # the third-party model behind it does not
    assert PerplexityAdapter().price(obs, table) == pytest.approx(FAST_PRICE)


def test_price_without_a_returned_model_falls_back_to_the_preset_row():
    raw = load(SEARCHED)
    del raw.response["model"]
    obs = PerplexityAdapter().parse(raw)
    assert obs.model_returned is None
    assert PerplexityAdapter().price(obs, PriceTable.load()) == pytest.approx(FAST_PRICE)


def test_price_resolves_the_preset_first_then_the_returned_model_then_the_default_row():
    rows = {
        "fast": {"input_per_1m": 1.0, "output_per_1m": 0.0},
        "openai/gpt-5.6-luna": {"input_per_1m": 2.0, "output_per_1m": 0.0},
        "base": {"input_per_1m": 3.0, "output_per_1m": 0.0},
    }
    provider = {"tool_call_usd_per_1k": 0.0, "default_model": "base", "models": rows}
    table = PriceTable(version="test", providers={"perplexity": provider})
    obs = PerplexityAdapter().parse(load(SEARCHED))  # 400 input tokens
    assert PerplexityAdapter().price(obs, table) == pytest.approx(400 / 1e6 * 1.0)  # the preset's row
    obs = obs.model_copy(update={"model_requested": "medium"})  # a preset the table lacks
    assert PerplexityAdapter().price(obs, table) == pytest.approx(400 / 1e6 * 2.0)  # the returned model's
    obs = obs.model_copy(update={"model_returned": None})
    assert PerplexityAdapter().price(obs, table) == pytest.approx(400 / 1e6 * 3.0)  # the default row


def test_build_request_declares_web_search_on_the_preset():
    engine = EngineConfig(provider="perplexity", model_requested="fast")
    req = PerplexityAdapter().build_request("q", engine)
    assert req == {"preset": "fast", "input": "q", "tools": [{"type": "web_search"}]}


def test_build_request_nests_search_controls_in_the_tool_without_aliasing_params():
    location = {"country": "GB", "city": "London"}
    params = {
        "search_domain_filter": ["example.org"], "search_recency_filter": "month", "user_location": location,
    }
    engine = EngineConfig(provider="perplexity", model_requested="fast", params=params)
    sha = engine.config_sha
    req = PerplexityAdapter().build_request("q", engine)
    # Where each control goes, per https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/how-to and
    # https://docs.perplexity.ai/api-reference/agent-post, read 2026-10-05.
    assert req["tools"] == [
        {
            "type": "web_search",
            "filters": {"search_domain_filter": ["example.org"], "search_recency_filter": "month"},
            "user_location": location,
        }
    ]
    req["tools"][0]["filters"]["search_domain_filter"].append("example.net")
    req["tools"][0]["user_location"]["city"] = "Paris"
    assert engine.config_sha == sha


@pytest.mark.asyncio
async def test_call_posts_bearer_to_agent_endpoint(httpx_mock):
    request_body = {"preset": "fast", "input": "q", "tools": [{"type": "web_search"}]}
    httpx_mock.add_response(
        method="POST", url=ENDPOINT, json=recorded_body(SEARCHED),
        match_headers={"Authorization": "Bearer pplx-test"}, match_json=request_body,
    )
    engine = EngineConfig(provider="perplexity", model_requested="fast")
    async with httpx.AsyncClient() as client:
        raw = await PerplexityAdapter().call(client, "q", engine, api_key="pplx-test")
    assert raw.response["id"] == "resp_pplx_1" and raw.request == request_body
    assert raw.provider == "perplexity" and raw.model_requested == "fast"


@pytest.mark.asyncio
async def test_call_raises_on_a_server_error(httpx_mock):
    httpx_mock.add_response(method="POST", url=ENDPOINT, status_code=500, json={"error": {"message": "boom"}})
    engine = EngineConfig(provider="perplexity", model_requested="fast")
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await PerplexityAdapter().call(client, "q", engine, api_key="pplx-test")


@pytest.mark.asyncio
async def test_as_blob_round_trips_and_never_holds_the_api_key(httpx_mock):
    httpx_mock.add_response(method="POST", url=ENDPOINT, json=recorded_body(SEARCHED))
    engine = EngineConfig(provider="perplexity", model_requested="fast")
    async with httpx.AsyncClient() as client:
        raw = await PerplexityAdapter().call(client, "q", engine, api_key="pplx-test")
    blob = raw.as_blob()
    assert "pplx-test" not in json.dumps(blob)
    again = RawResponse.model_validate(json.loads(json.dumps(blob)))
    assert again == raw
    assert PerplexityAdapter().parse(again) == PerplexityAdapter().parse(raw)
