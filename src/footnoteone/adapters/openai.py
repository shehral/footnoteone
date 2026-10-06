"""OpenAI Responses API with the web_search tool."""

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
    shift_index,
)
from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig, Observation, SourceRef

ENDPOINT = "https://api.openai.com/v1/responses"
CONSULTED_FIELD = "web_search_call.action.sources"
CITED_FIELD = "message.content.annotations.url_citation"
OPENED_ACTIONS = ("open_page", "find_in_page")


class OpenAIAdapter:
    provider = "openai"
    version = "openai@0.1.0"

    def build_request(self, prompt_text: str, engine: EngineConfig) -> dict[str, Any]:
        req: dict[str, Any] = {
            "model": engine.model_requested,
            "input": prompt_text,
            "tools": [{"type": "web_search"}],
            "include": ["web_search_call.action.sources"],
        }
        if engine.params.get("force_search"):
            # Forcing per https://developers.openai.com/api/docs/guides/tools-web-search, read 2026-10-05.
            req["tool_choice"] = "required"
        if "user_location" in engine.params:
            # A copy, so editing the request can never change the engine's config_sha.
            req["tools"][0]["user_location"] = copy.deepcopy(engine.params["user_location"])
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
            provider="openai", model_requested=engine.model_requested, request=request, response=resp.json()
        )

    def parse(self, raw: RawResponse) -> Observation:
        """Pure parse of one stored response; malformed entries are skipped, never raised on.

        Consulted: the `sources` of search actions in order, then the `url` of each open_page or find_in_page
        action (a page the model read) in output order, deduplicated by exact URL keeping the first rank.
        Cited: url_citation annotations in text order, offsets shifted to index the joined `answer_text`.
        `search_calls` counts every web_search_call item, whatever its action; that is the billing assumption
        until a recorded live response settles it. `failed_searches` counts the web_search_call items whose
        status is "failed". A refusal content part makes the status "refused" and its text the answer; a
        response status other than completed, incomplete or failed maps to "error".
        Activation is "yes" with any web_search_call item; without one it is "unknown" when the response
        status maps to "error" or "truncated" (the answer may have stopped before a search), else "no".
        `model_requested` is carried over for pricing.
        """
        body = raw.response
        searched: list[str | None] = []
        opened: list[tuple[str | None, str]] = []
        cited: list[SourceRef] = []
        pieces: list[str] = []
        offset = 0  # where the next piece starts in the joined answer text
        refused = False
        search_calls = failed_searches = 0
        for item in as_dicts(body.get("output")):
            if item.get("type") == "web_search_call":
                search_calls += 1
                if item.get("status") == "failed":
                    failed_searches += 1
                action = as_dict(item.get("action"))
                kind = as_str(action.get("type"))
                if kind in OPENED_ACTIONS:
                    opened.append((as_str(action.get("url")), f"web_search_call.action.{kind}.url"))
                else:
                    searched.extend(as_str(src.get("url")) for src in as_dicts(action.get("sources")))
            elif item.get("type") == "message":
                for part in as_dicts(item.get("content")):
                    if part.get("type") == "output_text":
                        text = as_str(part.get("text")) or ""
                        for ann in as_dicts(part.get("annotations")):
                            url = as_str(ann.get("url"))
                            if ann.get("type") == "url_citation" and url:
                                cited.append(
                                    SourceRef(
                                        url=url,
                                        rank=len(cited) + 1,
                                        provider_field=CITED_FIELD,
                                        title=as_str(ann.get("title")),
                                        char_start=shift_index(ann.get("start_index"), offset),
                                        char_end=shift_index(ann.get("end_index"), offset),
                                    )
                                )
                    elif part.get("type") == "refusal":
                        text = as_str(part.get("refusal")) or ""
                        refused = True
                    else:
                        continue
                    pieces.append(text)
                    offset += len(text) + 1  # the "\n" that joins the pieces
        consulted: list[SourceRef] = []
        seen: set[str] = set()
        for url, field in [(url, CONSULTED_FIELD) for url in searched] + opened:
            if url and url not in seen:
                seen.add(url)
                consulted.append(SourceRef(url=url, rank=len(consulted) + 1, provider_field=field))
        usage = as_dict(body.get("usage"))
        status_map = {"completed": "ok", "incomplete": "truncated", "failed": "error"}
        response_status = status_map.get(as_str(body.get("status")), "error")
        if search_calls:
            activated, evidence = "yes", f"{search_calls} web_search_call item(s) in output"
        elif response_status in ("error", "truncated"):
            activated = "unknown"
            evidence = f"no web_search_call item in a response with status {body.get('status')!r}"
        else:
            activated, evidence = "no", "no web_search_call item in output"
        return Observation(
            activated=activated,
            activation_evidence=evidence,
            answer_text="\n".join(pieces),
            consulted=consulted,
            cited=cited,
            model_requested=raw.model_requested,
            model_returned=as_str(body.get("model")),
            input_tokens=as_count(usage.get("input_tokens")),
            output_tokens=as_count(usage.get("output_tokens")),
            search_calls=search_calls,
            failed_searches=failed_searches,
            status="refused" if refused else response_status,
        )

    def price(self, obs: Observation, table: PriceTable) -> float:
        """Token rows by the returned model first, then the requested model, then the default row."""
        model = priced_model("openai", table, obs.model_returned, obs.model_requested)
        return default_price("openai", model, obs, table)
