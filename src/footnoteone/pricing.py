"""Versioned list prices. Every cost figure in the project points back to this table's version."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

import yaml

# A dated snapshot suffix such as "-2026-01-01" or "-20250929".
_SNAPSHOT_SUFFIX = re.compile(r"-(?:\d{4}-\d{2}-\d{2}|\d{8})$")


@dataclass(frozen=True)
class PriceTable:
    version: str
    providers: dict[str, Any]

    @classmethod
    def load(cls) -> PriceTable:
        text = resources.files("footnoteone").joinpath("pricing.yaml").read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        return cls(version=str(data["version"]), providers=data["providers"])

    def tool_call_usd(self, provider: str) -> float:
        return float(self.providers[provider]["tool_call_usd_per_1k"]) / 1000.0

    def _resolve(self, provider: str, model: str | None) -> str | None:
        """Exact key, else the key left after stripping one dated snapshot suffix, else None.

        A missing model (None) resolves to None.
        """
        if model is None:
            return None
        models = self.providers[provider]["models"]
        if model in models:
            return model
        base = _SNAPSHOT_SUFFIX.sub("", model, count=1)
        return base if base in models else None

    def is_known(self, provider: str, model: str | None) -> bool:
        return self._resolve(provider, model) is not None

    def token_usd(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
        p = self.providers[provider]
        prices = p["models"][self._resolve(provider, model) or p["default_model"]]
        return (
            input_tokens / 1e6 * float(prices["input_per_1m"])
            + output_tokens / 1e6 * float(prices["output_per_1m"])
        )
