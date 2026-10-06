"""Typed records for FootnoteOne. Pure data; no I/O lives here."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Activation = Literal["yes", "no", "unknown"]
RunStatus = Literal["ok", "error", "refused", "truncated", "timeout", "budget_skip"]
SourceRole = Literal["consulted", "cited"]
IntentKind = Literal["unbranded", "branded", "placebo"]
Provider = Literal["openai", "anthropic", "perplexity"]
RunnerKind = Literal["local", "gha"]
ManifestStatus = Literal["running", "done", "aborted"]


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


def canonical_json(obj: Any) -> str:
    """Canonical JSON: sorted keys, no spaces, non-JSON values through str().

    Content addresses and config_sha are hashes of this form, so changing it changes every stored id.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256_of(obj: Any) -> str:
    """sha256 hex digest of `canonical_json(obj)`."""
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()


class Lenient(BaseModel):
    """Base: unknown fields are ignored so older JSONL files keep loading after a schema addition.

    Non-finite floats (inf, nan) are rejected: JSON cannot hold them, so they would be written as null and
    the record could not be read back.
    """

    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class Prompt(Lenient):
    id: str = Field(default_factory=new_id)
    text: str
    paraphrase_idx: int = 0
    lang: str = "en"


class Intent(Lenient):
    id: str = Field(default_factory=new_id)
    label: str
    kind: IntentKind = "unbranded"
    target_page_ids: list[str] = Field(default_factory=list)
    prompts: list[Prompt] = Field(default_factory=list)


class EngineConfig(Lenient):
    """Identity is `config_sha`, so callers must never mutate `params` after construction."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    provider: Provider
    model_requested: str
    tool_version: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    surface: str = ""

    @model_validator(mode="before")
    @classmethod
    def _default_surface(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("surface"):
            data = {**data, "surface": f"api:{data.get('provider', '')}"}
        return data

    @property
    def config_sha(self) -> str:
        return sha256_of(
            {
                "provider": self.provider,
                "model_requested": self.model_requested,
                "tool_version": self.tool_version,
                "params": self.params,
                "surface": self.surface,
            }
        )


class SourceRef(Lenient):
    """A source as a parser saw it, before storage."""

    url: str
    rank: int = Field(ge=1)
    provider_field: str
    title: str | None = None
    char_start: int | None = None
    char_end: int | None = None


class Observation(Lenient):
    """The pure parse of one raw response.

    `failed_searches` counts the searches the response shows as failed; each adapter's parse says what counts
    as one. It is recorded beside `search_calls` and does not change `activated`.
    """

    activated: Activation
    activation_evidence: str
    answer_text: str
    consulted: list[SourceRef] = Field(default_factory=list)
    cited: list[SourceRef] = Field(default_factory=list)
    model_requested: str | None = None
    model_returned: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    search_calls: int = 0
    failed_searches: int = 0
    status: RunStatus = "ok"


class Run(Lenient):
    """One call to one engine for one prompt and replicate.

    `raw_sha256` and `parser_version` are None when no response body was received (a timeout, a transport or
    HTTP error, or a budget_skip): there is no stored response to address or parse.
    """

    id: str = Field(default_factory=new_id)
    manifest_id: str
    intent_id: str
    prompt_id: str
    engine_config_id: str
    rep_idx: int = 0
    status: RunStatus
    model_requested: str
    model_returned: str | None = None
    tool_version: str | None = None
    activated: Activation = "unknown"
    activation_evidence: str = ""
    raw_sha256: str | None = None
    parser_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    search_calls: int = 0
    cost_usd: float = 0.0
    started_at: AwareDatetime
    finished_at: AwareDatetime


class SourceRecord(Lenient):
    run_id: str
    role: SourceRole
    url: str
    canonical_url: str
    rank: int = Field(ge=1)
    provider_field: str
    title: str | None = None
    char_start: int | None = None
    char_end: int | None = None


class Manifest(Lenient):
    id: str = Field(default_factory=new_id)
    code_version: str
    adapter_versions: dict[str, str] = Field(default_factory=dict)
    config_sha: str
    price_table_version: str
    budget_usd: float
    spent_usd: float = 0.0
    runner: RunnerKind = "local"
    started_at: AwareDatetime = Field(default_factory=utcnow)
    status: ManifestStatus = "running"
