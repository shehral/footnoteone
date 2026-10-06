"""Adapter contract: `call` talks to the network, `parse` is pure, `price` reads the table.

The `as_*` readers let every parser take JSON-shaped input of the wrong type (a string where a list was
expected, an object where a string was) without raising: a mistyped value reads as absent.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import AwareDatetime, BaseModel, Field

from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig, Observation, Provider, utcnow


def as_dicts(value: Any) -> list[dict[str, Any]]:
    """The dict entries of a list; null, a non-list or a non-dict entry contributes nothing."""
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def as_dict(value: Any) -> dict[str, Any]:
    """`value` if it is a dict, else an empty dict."""
    return value if isinstance(value, dict) else {}


def as_str(value: Any) -> str | None:
    """`value` if it is a str, else None."""
    return value if isinstance(value, str) else None


def as_count(value: Any) -> int | None:
    """A reported count: a non-negative int, bools excluded; anything else (negatives too) is unreported."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def shift_index(index: Any, offset: int) -> int | None:
    """A character index from a response moved by `offset`; a missing or malformed index is None."""
    value = as_count(index)
    return None if value is None else value + offset


class RawResponse(BaseModel):
    provider: Provider
    model_requested: str
    request: dict[str, Any]
    response: dict[str, Any]
    fetched_at: AwareDatetime = Field(default_factory=utcnow)

    def as_blob(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@runtime_checkable
class Adapter(Protocol):
    provider: Provider
    version: str

    def build_request(self, prompt_text: str, engine: EngineConfig) -> dict[str, Any]: ...

    async def call(
        self, client: httpx.AsyncClient, prompt_text: str, engine: EngineConfig, api_key: str
    ) -> RawResponse: ...

    def parse(self, raw: RawResponse) -> Observation: ...

    def price(self, obs: Observation, table: PriceTable) -> float: ...


def priced_model(provider: Provider, table: PriceTable, *names: str | None) -> str:
    """The first of `names` with its own price row (see PriceTable.is_known), else the provider default.

    Each adapter's `price` passes its model names in the order it trusts them; None entries are skipped.
    """
    known = next((name for name in names if table.is_known(provider, name)), None)
    return known or table.providers[provider]["default_model"]


def default_price(provider: Provider, model: str, obs: Observation, table: PriceTable) -> float:
    """Tool-call fee times searches performed, plus token cost.

    Unknown token counts cost 0 and are flagged upstream.
    """
    tokens = table.token_usd(provider, model, obs.input_tokens or 0, obs.output_tokens or 0)
    return obs.search_calls * table.tool_call_usd(provider) + tokens
