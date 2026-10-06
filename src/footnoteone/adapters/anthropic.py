"""Anthropic Messages API with the web search server tool."""

from __future__ import annotations

import copy
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
)
from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig, Observation, RunStatus, SourceRef

ENDPOINT = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_TOOL = "web_search_20250305"
CONSULTED_FIELD = "web_search_tool_result.content.web_search_result"
CITED_FIELD = "text.citations.web_search_result_location"
# Optional tool fields taken from engine params, per
# https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool, read 2026-10-05.
TOOL_PARAMS = ("max_uses", "allowed_domains", "blocked_domains", "user_location")
STATUS_BY_STOP_REASON: dict[str, RunStatus] = {
    "end_turn": "ok",
    "stop_sequence": "ok",
    "max_tokens": "truncated",
    "model_context_window_exceeded": "truncated",  # the answer filled the context window and was cut off
    "pause_turn": "truncated",  # the server-side search loop paused; this adapter never resumes a turn
    "refusal": "refused",
}


class AnthropicAdapter:
    provider = "anthropic"
    version = "anthropic@0.1.0"

    def build_request(self, prompt_text: str, engine: EngineConfig) -> dict[str, Any]:
        tool: dict[str, Any] = {"type": engine.tool_version or DEFAULT_TOOL, "name": "web_search"}
        for key in TOOL_PARAMS:
            if key in engine.params:
                # A copy, so editing the request can never change the engine's config_sha.
                tool[key] = copy.deepcopy(engine.params[key])
        if "allowed_domains" in tool and "blocked_domains" in tool:
            raise ValueError(
                "web search takes allowed_domains or blocked_domains, not both (the API answers 400)"
            )
        return {
            "model": engine.model_requested,
            "max_tokens": int(engine.params.get("max_tokens", 1024)),
            "messages": [{"role": "user", "content": prompt_text}],
            "tools": [tool],
        }

    async def call(
        self, client: httpx.AsyncClient, prompt_text: str, engine: EngineConfig, api_key: str
    ) -> RawResponse:
        request = self.build_request(prompt_text, engine)
        headers = {"x-api-key": api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        resp = await client.post(ENDPOINT, json=request, headers=headers, timeout=120.0)
        resp.raise_for_status()
        return RawResponse(
            provider="anthropic",
            model_requested=engine.model_requested,
            request=request,
            response=resp.json(),
        )

    def parse(self, raw: RawResponse) -> Observation:
        """Pure parse of one stored response; malformed entries are skipped, never raised on.

        Consulted: the web_search_result items of every web_search_tool_result block, in content order,
        deduplicated by exact URL keeping the first rank, as the OpenAI adapter does. A result block whose
        content is an error object (a failed search) adds no URL; its error code joins the evidence, and
        `failed_searches` counts those blocks.
        Cited: web_search_result_location citations in text order, without character offsets, which this
        API does not give. `answer_text` joins every text block in content order.
        `search_calls` is usage.server_tool_use.web_search_requests when it is a non-negative int (failed
        searches are not billed), else the number of web_search server_tool_use blocks.
        Status comes from stop_reason: end_turn and stop_sequence are ok; max_tokens, pause_turn and
        model_context_window_exceeded truncated; refusal refused; anything else or a missing value an error.
        Activation is "yes" with any web_search server_tool_use block, any web_search_tool_result block or
        a positive usage count; without one it is "unknown" when the status is error or truncated (the turn
        may have ended before a search), else "no". `model_requested` is carried over for pricing.
        """
        body = raw.response
        consulted: list[SourceRef] = []
        seen: set[str] = set()  # consulted URLs so far; a repeat keeps its first rank
        cited: list[SourceRef] = []
        pieces: list[str] = []
        error_codes: list[str] = []
        tool_uses = 0
        result_blocks = 0
        for block in as_dicts(body.get("content")):
            kind = block.get("type")
            if kind == "server_tool_use" and block.get("name") == "web_search":
                tool_uses += 1
            elif kind == "web_search_tool_result":
                result_blocks += 1
                content = block.get("content")
                if isinstance(content, dict):  # a failed search: one error object instead of a result list
                    error_codes.append(str(content.get("error_code")))
                    continue
                for item in as_dicts(content):
                    url = as_str(item.get("url"))
                    if item.get("type") == "web_search_result" and url and url not in seen:
                        seen.add(url)
                        consulted.append(
                            SourceRef(
                                url=url,
                                rank=len(consulted) + 1,
                                provider_field=CONSULTED_FIELD,
                                title=as_str(item.get("title")),
                            )
                        )
            elif kind == "text":
                pieces.append(as_str(block.get("text")) or "")
                for cit in as_dicts(block.get("citations")):
                    url = as_str(cit.get("url"))
                    if cit.get("type") == "web_search_result_location" and url:
                        cited.append(
                            SourceRef(
                                url=url,
                                rank=len(cited) + 1,
                                provider_field=CITED_FIELD,
                                title=as_str(cit.get("title")),
                            )
                        )
        usage = as_dict(body.get("usage"))
        reported = as_count(as_dict(usage.get("server_tool_use")).get("web_search_requests"))
        search_calls = tool_uses if reported is None else reported
        stop_reason = body.get("stop_reason")
        status = STATUS_BY_STOP_REASON.get(as_str(stop_reason), "error")
        evidence = (
            f"{tool_uses} web_search server_tool_use block(s), "
            f"{result_blocks} web_search_tool_result block(s), "
            f"usage web_search_requests {'absent' if reported is None else reported}"
        )
        if error_codes:
            evidence += f"; failed search error code(s): {', '.join(error_codes)}"
        if tool_uses or result_blocks or search_calls > 0:
            activated = "yes"
        elif status in ("error", "truncated"):
            activated = "unknown"
            evidence += (
                f"; stop_reason {stop_reason!r} maps to {status}, so the turn may have ended before a search"
            )
        else:
            activated = "no"
        return Observation(
            activated=activated,
            activation_evidence=evidence,
            answer_text="".join(pieces),
            consulted=consulted,
            cited=cited,
            model_requested=raw.model_requested,
            model_returned=as_str(body.get("model")),
            input_tokens=as_count(usage.get("input_tokens")),
            output_tokens=as_count(usage.get("output_tokens")),
            search_calls=search_calls,
            failed_searches=len(error_codes),
            status=status,
        )

    def price(self, obs: Observation, table: PriceTable) -> float:
        """Token rows by the returned model first, then the requested model, then the default row."""
        model = priced_model("anthropic", table, obs.model_returned, obs.model_requested)
        return default_price("anthropic", model, obs, table)
