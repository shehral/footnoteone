import pytest

from footnoteone.pricing import PriceTable


def test_price_table_loads_version_and_tool_fees():
    table = PriceTable.load()
    assert table.version == "2026-10-05"
    assert table.tool_call_usd("openai") == pytest.approx(0.010)
    assert table.tool_call_usd("perplexity") == pytest.approx(0.0025)


def test_perplexity_fast_row_is_the_higher_listed_tier():
    table = PriceTable.load()
    usd = table.token_usd("perplexity", "fast", input_tokens=1_000_000, output_tokens=1_000_000)
    assert usd == pytest.approx(0.40 + 1.80)


def test_token_usd_uses_model_prices_and_default_for_unknown_model():
    table = PriceTable.load()
    known = table.token_usd("openai", "gpt-5-mini", input_tokens=1_000_000, output_tokens=0)
    assert known == pytest.approx(0.25)
    unknown = table.token_usd("openai", "gpt-999", input_tokens=1_000_000, output_tokens=0)
    assert unknown == known  # falls back to the provider default model
    assert table.is_known("openai", "gpt-999") is False


def test_dated_snapshot_prices_at_its_own_row_and_unrelated_names_fall_back():
    table = PriceTable(
        version="test",
        providers={
            "openai": {
                "tool_call_usd_per_1k": 10.0,
                "default_model": "gpt-5-mini",
                "models": {
                    "gpt-5-mini": {"input_per_1m": 0.25, "output_per_1m": 2.00},
                    "gpt-5": {"input_per_1m": 1.25, "output_per_1m": 10.00},
                },
            }
        },
    )
    for dated in ("gpt-5-2025-08-07", "gpt-5-20250807"):
        usd = table.token_usd("openai", dated, input_tokens=1_000_000, output_tokens=0)
        assert usd == pytest.approx(1.25)
        assert table.is_known("openai", dated) is True
    assert table.token_usd("openai", "gpt-5", input_tokens=0, output_tokens=1_000_000) == pytest.approx(10.00)
    unrelated = table.token_usd("openai", "o9-preview", input_tokens=1_000_000, output_tokens=0)
    assert unrelated == pytest.approx(0.25)  # falls back to the default row
    assert table.is_known("openai", "o9-preview") is False


def test_a_missing_model_resolves_to_nothing_and_is_not_known():
    table = PriceTable.load()
    assert table._resolve("openai", None) is None
    assert table.is_known("openai", None) is False
