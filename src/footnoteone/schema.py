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
PageSource = Literal["sitemap", "rss", "youtube", "manual"]


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


def run_key(label: str, intent_id: str, prompt_id: str, engine_config_id: str, rep_idx: int) -> str:
    """Deterministic id of one planned call within a burst label; the runner uses it as Run.id and for
    resume."""
    return sha256_of([label, intent_id, prompt_id, engine_config_id, rep_idx])[:32]


ALL_SEARCHES_FAILED = "all searches failed"
# Run.error_kind, why a run did not end ok (Ruling B30). An error is one of: "transport" (no answer: the
# connection failed, before or after the request was sent), "http" (an error status), "unreadable" (a 200
# whose body is not a JSON object), "parse"
# (the parser raised; the raw response is stored), "provider_failed" (the response's own status says it
# failed or is one the parser does not know), "all_searches_failed" (METRICS 0.2.0). A timeout is "timeout",
# a budget_skip "budget", and a refused or truncated answer carries its status name, so a run's error_kind is
# None exactly when its status is ok.
ERROR_KINDS = (
    "transport", "http", "unreadable", "parse", "provider_failed", "all_searches_failed", "timeout", "budget",
    "refused", "truncated",
)


def effective_status(
    status: RunStatus, search_calls: int, failed_searches: int
) -> tuple[RunStatus, str | None]:
    """METRICS 0.2.0 scope rule: a run whose every search failed is an error ("all searches failed");
    any other run keeps its status. Returns the effective status and the reason, or None."""
    if search_calls > 0 and failed_searches >= search_calls:
        return "error", ALL_SEARCHES_FAILED
    return status, None


def parsed_error_kind(status: RunStatus, reason: str | None) -> str | None:
    """The error_kind of a parsed answer from its effective status and effective_status's reason: None when
    ok, "all_searches_failed" for that reason, "provider_failed" for any other error the response reports,
    else the status itself (refused, truncated)."""
    if status == "ok":
        return None
    if reason == ALL_SEARCHES_FAILED:
        return "all_searches_failed"
    return "provider_failed" if status == "error" else status


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


def first_difference(this: EngineConfig, other: EngineConfig) -> str | None:
    """The first field that sets two engine configs apart, worded as `this` against `other`: the tool
    version, then the params by name, then the surface ("max_output_tokens 1000 instead of 1200", "no
    force_search instead of force_search true"). None when the two are one config. Values print as JSON."""
    if this.config_sha == other.config_sha:
        return None
    if this.tool_version != other.tool_version:
        mine, theirs = (e.tool_version or "the default" for e in (this, other))
        return f"tool version {mine} instead of {theirs}"
    for key in sorted(set(this.params) | set(other.params)):
        mine, theirs = this.params.get(key), other.params.get(key)
        if key not in this.params:
            return f"no {key} instead of {key} {json.dumps(theirs)}"
        if key not in other.params:
            return f"{key} {json.dumps(mine)} instead of no {key}"
        if canonical_json(mine) != canonical_json(theirs):
            return f"{key} {json.dumps(mine)} instead of {json.dumps(theirs)}"
    if this.surface != other.surface:
        return f"surface {this.surface} instead of {other.surface}"
    return f"{this.provider} {this.model_requested} instead of {other.provider} {other.model_requested}"


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
    HTTP error, or a budget_skip): there is no stored response to address or parse. `error_kind` says why a
    run did not end ok (see ERROR_KINDS) and is None for an ok run; records written before it existed read as
    None.
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
    error_kind: str | None = None


class SourceRecord(Lenient):
    """One consulted or cited source of a run, as parsed from the raw response `raw_sha256` names.

    A call that is tried again (a resume retries a call whose last run is not ok, or one whose sources were
    written before a crash stopped its run) appends a second set of sources under the same `run_id`. Readers
    keep only the sources whose `raw_sha256` equals the `raw_sha256` of the run's last record. Lines written
    before this field existed read as None.
    """

    run_id: str
    role: SourceRole
    url: str
    canonical_url: str
    rank: int = Field(ge=1)
    provider_field: str
    title: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    raw_sha256: str | None = None


class Page(Lenient):
    """One owned page or off-site post in the library (`.footnote/pages.jsonl`)."""

    id: str
    url: str
    canonical_url: str
    title: str | None = None
    source: PageSource
    lastmod: str | None = None
    discovered_at: AwareDatetime = Field(default_factory=utcnow)

    @staticmethod
    def id_for(canonical_url: str) -> str:
        return sha256_of(canonical_url)[:16]


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
    label: str = ""
    engines: list[EngineConfig] = Field(default_factory=list)
    intents: list[Intent] = Field(default_factory=list)
    canon_version: int = 0
    planning_version: str = ""
    paraphrases: int = 0
    reps: int = 0
    finished_at: AwareDatetime | None = None
    note: str = ""
