"""Perplexity Agent API with the web_search tool (the Sonar chat completions API ended 2026-09-27).

Shapes per the Agent API docs, read 2026-10-05:
https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar,
https://docs.perplexity.ai/api-reference/agent-post and
https://docs.perplexity.ai/docs/agent-api/tools/web-search.
"""

from __future__ import annotations

import copy
import re
from typing import Any

import httpx

from footnoteone.adapters.base import (
    RawResponse,
    as_count,
    as_dict,
    as_dicts,
    as_str,
    default_price,
    priced_model,
    shift_index,
)
from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig, Observation, SourceRef

ENDPOINT = "https://api.perplexity.ai/v1/agent"
CONSULTED_FIELD = "search_results.results"
FETCHED_FIELD = "fetch_url_results.contents.url"
CITED_FIELD = "message.content.annotations.url_citation"
MARKER_FIELD = "message.text.inline_marker"
# "[n]" (the fast preset's prompt) or "[web:n]" (the low to high presets' prompts); n is a result id,
# an int64, so at most 19 ASCII digits (which also keeps int() clear of its 4300-digit limit).
MARKER = re.compile(r"\[(?:web:)?(\d{1,19})\]", re.ASCII)
# Blanked out before markers are matched, approximating CommonMark: fenced code, opened by ``` or ~~~ at
# a line start (up to three spaces in) and closed by the same fence at a line start or the end of the text;
# code spans of one or two backticks within a line; link reference definitions ("[n]:" at a line start);
# and URL runs up to whitespace, except for up to 16 markers glued to the very end of a run (the bound keeps
# a long marker run linear). Not modelled: a longer fence holding a shorter one, a code span of three or
# more backticks, and a line that opens with an inline ```code``` span (it is read as a fence).
FENCED_CODE = re.compile(r"^ {0,3}(```|~~~).*?(?:^ {0,3}\1|\Z)", re.DOTALL | re.MULTILINE)
INLINE_CODE = re.compile(r"(?<!`)(`{1,2})(?!`)[^\n]+?(?<!`)\1(?!`)")
LINK_DEFINITION = re.compile(r"^ {0,3}\[(?:web:)?\d{1,19}\]:", re.MULTILINE | re.ASCII)
URL_RUN = re.compile(r"https?://\S+")
GLUED_MARKERS = re.compile(r"(?:\[(?:web:)?\d{1,19}\]){1,16}[.,;:!?)]*\Z", re.ASCII)
# The usage.tool_calls_details key the web search guide shows for the web_search tool.
SEARCH_USAGE_KEY = "search_web"
# Sonar's top-level search controls, which the Agent API nests in the web_search tool.
FILTER_PARAMS = ("search_domain_filter", "search_recency_filter")
STATUS_MAP = {"completed": "ok", "incomplete": "truncated"}

# url, title, provider_field, char_start, char_end
_Candidate = tuple[str, str | None, str, int | None, int | None]


def _refs(candidates: list[_Candidate]) -> list[SourceRef]:
    """SourceRefs ranked in candidate order."""
    return [
        SourceRef(url=url, rank=rank, provider_field=field, title=title, char_start=start, char_end=end)
        for rank, (url, title, field, start, end) in enumerate(candidates, start=1)
    ]


def _distinct(candidates: list[_Candidate]) -> list[_Candidate]:
    """The candidates without repeated URLs, each first occurrence kept."""
    seen: set[str] = set()
    kept: list[_Candidate] = []
    for candidate in candidates:
        if candidate[0] not in seen:
            seen.add(candidate[0])
            kept.append(candidate)
    return kept


def _blank(match: re.Match[str]) -> str:
    return " " * len(match.group())


def _blank_url(match: re.Match[str]) -> str:
    run = match.group()
    glued = GLUED_MARKERS.search(run)
    keep = glued.start() if glued else len(run)
    return " " * keep + run[keep:]


def _mask(text: str) -> str:
    """`text` with code, link reference definitions and URLs blanked to spaces, its length unchanged, so
    spans still index `text`."""
    text = INLINE_CODE.sub(_blank, FENCED_CODE.sub(_blank, text))
    return URL_RUN.sub(_blank_url, LINK_DEFINITION.sub(_blank, text))


class PerplexityAdapter:
    provider = "perplexity"
    version = "perplexity@0.1.0"
    # The request field that caps generated tokens, per https://docs.perplexity.ai/api-reference/agent-post
    # (read 2026-10-06). It is named like the engine param, so the param is sent under its own name.
    OUTPUT_LIMIT_FIELD = "max_output_tokens"

    def build_request(self, prompt_text: str, engine: EngineConfig) -> dict[str, Any]:
        tool: dict[str, Any] = {"type": "web_search"}
        # Copies, so editing the request can never change the engine's config_sha.
        filters = {key: copy.deepcopy(engine.params[key]) for key in FILTER_PARAMS if key in engine.params}
        if filters:
            tool["filters"] = filters
        if "user_location" in engine.params:
            tool["user_location"] = copy.deepcopy(engine.params["user_location"])
        req: dict[str, Any] = {"preset": engine.model_requested, "input": prompt_text, "tools": [tool]}
        if "max_output_tokens" in engine.params:
            req[self.OUTPUT_LIMIT_FIELD] = int(engine.params["max_output_tokens"])
        return req

    async def call(
        self, client: httpx.AsyncClient, prompt_text: str, engine: EngineConfig, api_key: str
    ) -> RawResponse:
        request = self.build_request(prompt_text, engine)
        resp = await client.post(
            ENDPOINT, json=request, headers={"Authorization": f"Bearer {api_key}"}, timeout=120.0
        )
        resp.raise_for_status()
        return RawResponse(
            provider="perplexity",
            model_requested=engine.model_requested,
            request=request,
            response=resp.json(),
        )

    def parse(self, raw: RawResponse) -> Observation:
        """Pure parse of one stored response; malformed entries are skipped, never raised on.

        Consulted: the `results` of every `search_results` item in output order, then each page `url` in a
        `fetch_url_results` item (a page the agent read) in output order, deduplicated by exact URL keeping
        the first rank. Cited, one entry per occurrence: the url_citation annotations, in array order per
        output_text part, offsets shifted to index the joined `answer_text`; when there are none, the inline
        markers "[n]" and "[web:n]" in `answer_text` in text order, each resolved by search result `id`
        compared as text (the web search guide says "Each number then refers to a result's `id`",
        https://docs.perplexity.ai/docs/agent-api/tools/web-search, read 2026-10-05), with the marker's
        span as the offsets. Fenced code (fences at line starts), code spans of one or two backticks, link
        reference definitions (a line-start "[n]:") and URLs are blanked out before matching (up to 16
        markers glued to the end of a URL survive), and a marker followed by "(" is a link label, not a
        citation; a bare unformatted `x[1]` stays indistinguishable from a citation. A marker naming no
        result is skipped and counted in `activation_evidence`. Search results are never copied into cited
        wholesale.

        Activation is "yes" with a positive `search_web` invocation count, a `search_results` item holding
        results or queries, or any `fetch_url_results` item (the agent used a web tool); without those it is
        "unknown" when the status maps to "error" or "truncated", "no" with an explicit zero count or only
        empty `search_results` items, else "unknown". `search_calls` is the invocation count when reported
        as a non-negative int, else the number of `search_results` items holding results or queries, at
        least 1 once activated. Status completed maps to "ok", incomplete to "truncated" and anything
        else, missing included, to "error"; the docs define no refusal content part, so "refused" is never
        produced. `failed_searches` stays 0: the docs show no failed-search object to count. `model_requested`
        (the preset) is carried over for pricing. The stored request carries `max_output_tokens` (sent as
        `OUTPUT_LIMIT_FIELD`) when the engine params set it; parsing does not depend on it, so a response
        held to that limit is read by its reported status like any other.
        """
        body = raw.response
        found: list[_Candidate] = []
        fetched: list[_Candidate] = []
        annotated: list[_Candidate] = []
        by_id: dict[str, tuple[str, str | None]] = {}  # result id as text -> (url, title), the first wins
        pieces: list[str] = []
        offset = 0  # where the next piece starts in the joined answer text
        search_items = searched_items = fetch_items = 0
        for item in as_dicts(body.get("output")):
            kind = item.get("type")
            if kind == "search_results":
                search_items += 1
                results = as_dicts(item.get("results"))
                queries = item.get("queries")
                if results or (isinstance(queries, list) and queries):
                    searched_items += 1
                for result in results:
                    url, title = as_str(result.get("url")), as_str(result.get("title"))
                    if url:
                        found.append((url, title, CONSULTED_FIELD, None, None))
                        result_id = result.get("id")
                        if isinstance(result_id, int | str) and not isinstance(result_id, bool):
                            by_id.setdefault(str(result_id), (url, title))
            elif kind == "fetch_url_results":
                fetch_items += 1
                for page in as_dicts(item.get("contents")):
                    if url := as_str(page.get("url")):
                        fetched.append((url, as_str(page.get("title")), FETCHED_FIELD, None, None))
            elif kind == "message":
                for part in as_dicts(item.get("content")):
                    if part.get("type") != "output_text":
                        continue
                    text = as_str(part.get("text")) or ""
                    for ann in as_dicts(part.get("annotations")):
                        if ann.get("type") == "url_citation" and (url := as_str(ann.get("url"))):
                            start = shift_index(ann.get("start_index"), offset)
                            end = shift_index(ann.get("end_index"), offset)
                            annotated.append((url, as_str(ann.get("title")), CITED_FIELD, start, end))
                    pieces.append(text)
                    offset += len(text) + 1  # the "\n" that joins the pieces
        answer_text = "\n".join(pieces)
        marked: list[_Candidate] = []
        unresolved = 0
        if not annotated:
            masked = _mask(answer_text)
            for marker in MARKER.finditer(masked):
                if masked[marker.end() : marker.end() + 1] == "(":
                    continue  # a markdown link label: the link target is not a citation
                source = by_id.get(str(int(marker.group(1))))
                if source is None:
                    unresolved += 1
                else:
                    marked.append((*source, MARKER_FIELD, marker.start(), marker.end()))
        usage = as_dict(body.get("usage"))
        details = as_dict(as_dict(usage.get("tool_calls_details")).get(SEARCH_USAGE_KEY))
        calls = as_count(details.get("invocation"))
        status = STATUS_MAP.get(as_str(body.get("status")), "error")
        count = f"usage.tool_calls_details.{SEARCH_USAGE_KEY}.invocation = {calls}"
        facts = []
        if calls:
            facts.append(count)
        if searched_items:
            facts.append(f"{searched_items} search_results item(s) holding results or queries")
        if fetch_items:
            facts.append(f"{len(fetched)} page(s) fetched in {fetch_items} fetch_url_results item(s)")
        if facts:
            activated, evidence = "yes", "; ".join(facts)
        elif status in ("error", "truncated"):
            activated = "unknown"
            evidence = f"no search shown in a response with status {body.get('status')!r}"
        elif calls == 0 or search_items:
            activated = "no"
            evidence = count if calls == 0 else f"{search_items} search_results item(s), all empty"
        else:
            activated = "unknown"
            evidence = f"no {SEARCH_USAGE_KEY} invocation count in usage and no search_results item in output"
        if unresolved:
            evidence += f"; {unresolved} unresolved inline marker(s)"
        if calls is not None:
            search_calls = calls
        else:
            search_calls = max(searched_items, 1) if activated == "yes" else 0
        return Observation(
            activated=activated,
            activation_evidence=evidence,
            answer_text=answer_text,
            consulted=_refs(_distinct(found + fetched)),
            cited=_refs(annotated or marked),
            model_requested=raw.model_requested,
            model_returned=as_str(body.get("model")),
            input_tokens=as_count(usage.get("input_tokens")),
            output_tokens=as_count(usage.get("output_tokens")),
            search_calls=search_calls,
            status=status,
        )

    def price(self, obs: Observation, table: PriceTable) -> float:
        """Token rows by the requested preset first, then the returned model, then the default row."""
        model = priced_model("perplexity", table, obs.model_requested, obs.model_returned)
        return default_price("perplexity", model, obs, table)
