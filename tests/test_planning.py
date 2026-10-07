import pytest

from footnoteone.planning import (
    PlanningAssumptions,
    max_output_for,
    max_searches_for,
    typical_usd,
    worst_case_usd,
)
from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig


def test_assumptions_load_with_limits_per_provider():
    a = PlanningAssumptions.load()
    assert a.version == "2026-10-05"
    assert a.providers["openai"].limits == {"max_output_tokens": 1200, "max_tool_calls": 3}
    assert a.providers["anthropic"].limits["max_uses"] == 3


def test_limits_can_be_overridden_by_engine_params():
    a = PlanningAssumptions.load()
    e = EngineConfig(
        provider="anthropic", model_requested="claude-sonnet-4-5", params={"max_uses": 1, "max_tokens": 400}
    )
    assert max_searches_for(e, a) == 1 and max_output_for(e, a) == 400
    d = EngineConfig(provider="anthropic", model_requested="claude-sonnet-4-5")
    assert max_searches_for(d, a) == 3 and max_output_for(d, a) == 1200


def test_worst_case_exceeds_typical_and_matches_formula():
    a, t = PlanningAssumptions.load(), PriceTable.load()
    e = EngineConfig(provider="openai", model_requested="gpt-5-mini")
    worst = worst_case_usd(e, t, a)
    expected = 3 * 0.010 + t.token_usd("openai", "gpt-5-mini", 8000, 1200)
    assert worst == pytest.approx(expected)
    assert typical_usd(e, t, a) == pytest.approx(1.5 * 0.010 + t.token_usd("openai", "gpt-5-mini", 2500, 600))
    assert worst > typical_usd(e, t, a)
