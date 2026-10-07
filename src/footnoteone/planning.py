"""Per-call limits and cost assumptions for planning and budget reservation (planning.yaml)."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

import yaml

from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig

SEARCH_LIMIT_KEYS = ("max_tool_calls", "max_uses")  # OpenAI, Anthropic; Perplexity has no documented limit
OUTPUT_LIMIT_KEYS = ("max_output_tokens", "max_tokens")  # OpenAI and Perplexity, Anthropic


@dataclass(frozen=True)
class ProviderAssumptions:
    limits: dict[str, int]
    typical_input_tokens: int
    typical_output_tokens: int
    typical_searches: float
    max_input_tokens: int
    max_searches: int


@dataclass(frozen=True)
class PlanningAssumptions:
    version: str
    providers: dict[str, ProviderAssumptions]

    @classmethod
    def load(cls) -> PlanningAssumptions:
        text = resources.files("footnoteone").joinpath("planning.yaml").read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        providers = {
            name: ProviderAssumptions(
                limits={k: int(v) for k, v in dict(p.get("limits") or {}).items()},
                typical_input_tokens=int(p["typical_input_tokens"]),
                typical_output_tokens=int(p["typical_output_tokens"]),
                typical_searches=float(p["typical_searches"]),
                max_input_tokens=int(p["max_input_tokens"]),
                max_searches=int(p["max_searches"]),
            )
            for name, p in data["providers"].items()
        }
        return cls(version=str(data["version"]), providers=providers)


def _first_int(params: dict, keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = params.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def max_searches_for(engine: EngineConfig, a: PlanningAssumptions) -> int:
    """Searches one call can bill at most: the engine's own limit param, else the provider assumption."""
    p = a.providers[engine.provider]
    return (
        _first_int(engine.params, SEARCH_LIMIT_KEYS)
        or _first_int(p.limits, SEARCH_LIMIT_KEYS)
        or p.max_searches
    )


def max_output_for(engine: EngineConfig, a: PlanningAssumptions) -> int:
    p = a.providers[engine.provider]
    return (
        _first_int(engine.params, OUTPUT_LIMIT_KEYS)
        or _first_int(p.limits, OUTPUT_LIMIT_KEYS)
        or p.typical_output_tokens
    )


def typical_usd(engine: EngineConfig, table: PriceTable, a: PlanningAssumptions) -> float:
    """Expected cost of one call: typical searches x tool fee + typical tokens at the model's rows."""
    p = a.providers[engine.provider]
    tokens = table.token_usd(
        engine.provider, engine.model_requested, p.typical_input_tokens, p.typical_output_tokens
    )
    return p.typical_searches * table.tool_call_usd(engine.provider) + tokens


def worst_case_usd(engine: EngineConfig, table: PriceTable, a: PlanningAssumptions) -> float:
    """Upper bound the runner reserves before a call: max searches x tool fee + max input and output
    tokens."""
    p = a.providers[engine.provider]
    tokens = table.token_usd(
        engine.provider, engine.model_requested, p.max_input_tokens, max_output_for(engine, a)
    )
    return max_searches_for(engine, a) * table.tool_call_usd(engine.provider) + tokens
