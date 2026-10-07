"""Run one burst: reserve the worst case before each call, store raw first, then sources, then the run."""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
from pydantic import ValidationError

from footnoteone import __version__
from footnoteone.adapters.anthropic import AnthropicAdapter
from footnoteone.adapters.base import Adapter
from footnoteone.adapters.openai import OpenAIAdapter
from footnoteone.adapters.perplexity import PerplexityAdapter
from footnoteone.canon import CANON_VERSION, canonicalize
from footnoteone.config import ConfigError, ProjectConfig, config_sha_of
from footnoteone.design import Call, enumerate_calls
from footnoteone.planning import PlanningAssumptions, worst_case_usd
from footnoteone.pricing import PriceTable
from footnoteone.schema import (
    EngineConfig,
    Intent,
    Manifest,
    Observation,
    Run,
    RunStatus,
    SourceRecord,
    effective_status,
    parsed_error_kind,
    utcnow,
)
from footnoteone.store import JsonlStore, RawStore

RETRY_DELAYS = (2.0, 8.0)
TRANSIENT = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
PROVIDERS = ("openai", "anthropic", "perplexity")
# Transport failures raised before the request is sent: no connection, no free pooled connection, a refused
# proxy tunnel, a header h11 refuses to send, an unsupported scheme. The provider cannot bill them, so they
# are retried at no cost. Any other transport failure (a read timeout, a dropped connection) can come
# after the provider received the request and may be billed, so it is charged the worst case, not retried.
NOT_SENT = (
    httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ProxyError, httpx.LocalProtocolError,
    httpx.UnsupportedProtocol,
)
# A success answer whose body is not a JSON object: not JSON, not UTF-8, a JSON value RawResponse rejects,
# a content encoding that does not decode, or JSON the reader cannot hold (an integer past int()'s 4300-digit
# limit is a plain ValueError, nesting past the recursion limit a RecursionError). The request was answered,
# so the provider may have billed it.
UNREADABLE = (
    json.JSONDecodeError, UnicodeDecodeError, ValidationError, httpx.DecodingError, ValueError, RecursionError
)


class RunnerError(ConfigError):
    """The run cannot start: an unpriced engine, a missing key or one a header cannot carry. Nothing is
    written."""


def _header_safe(key: str) -> bool:
    """Whether an HTTP header can carry the key as it is: printable ASCII only (httpx encodes header values as
    ASCII), with no space at either end (h11 refuses a header value that starts or ends with one)."""
    return key == key.strip() and all(" " <= char <= "~" for char in key)


# Ruling B23 as amended: on resume these last records are final, so their call is not asked again: an ok
# run, a refused or truncated answer (complete and billed; asking again would pay for the same answer), and
# a parse failure (its raw response is stored, so a newer parser can replay it). Every other run that is not
# ok (a transport, HTTP or unreadable-body error, a provider failure, all searches failed, a timeout or a
# budget skip) is asked again.
FINAL_STATUSES = frozenset({"ok", "refused", "truncated"})


def is_final(run: Run) -> bool:
    """Whether a resume leaves this run's call alone: see FINAL_STATUSES."""
    return run.status in FINAL_STATUSES or (run.status == "error" and run.error_kind == "parse")


def default_adapters() -> dict[str, Adapter]:
    return {"openai": OpenAIAdapter(), "anthropic": AnthropicAdapter(), "perplexity": PerplexityAdapter()}


def keys_from_env(config: ProjectConfig, environ: Mapping[str, str] | None = None) -> dict[str, str | None]:
    """Each provider's key from the environment variable footnote.toml names for it (os.environ unless
    `environ` is given, read when called); None when the variable is unset or blank after strip."""
    environ = os.environ if environ is None else environ
    keys: dict[str, str | None] = {}
    for provider in PROVIDERS:
        value = environ.get(config.keys.env_name(provider))
        keys[provider] = value if value and value.strip() else None
    return keys


@dataclass
class EngineTally:
    """One engine config's share of a burst: the calls this invocation made or skipped for budget, by status;
    the calls skipped because their last record is final (see is_final); and the worst case reserved per
    call."""

    engine: EngineConfig
    worst_case_usd: float
    already_done: int = 0
    counts: dict[str, int] = field(default_factory=dict)


@dataclass
class RunnerResult:
    """`spent_usd` is what this invocation spent, as its manifest records it; `prior_spent_usd` is what
    earlier runs of the same burst label spent, which the budget check counts first. `manifest` is the
    manifest's final record (None for a dry run). `skipped_existing` counts the calls left alone because
    their last record is final; `to_call` lists the others, in call order, and `engines` tallies each
    engine config."""

    manifest: Manifest | None
    counts: dict[str, int]
    spent_usd: float
    skipped_existing: int
    planned_calls: int
    dry_run: bool
    prior_spent_usd: float = 0.0
    engines: list[EngineTally] = field(default_factory=list)
    to_call: list[Call] = field(default_factory=list)
    changed: list[tuple[EngineConfig, EngineConfig]] = field(default_factory=list)  # see BurstStart


@dataclass(frozen=True)
class BurstStart:
    """What run_design reports once its checks pass and the manifest is written, before the first call.
    `changed` pairs each engine config an earlier run of the label used and footnote.toml no longer gives
    with today's config of the same provider and model, (earlier, now): the burst starts afresh for today's
    config, since run keys and the report go by config sha."""

    label: str
    planned_calls: int
    already_done: int
    prior_spent_usd: float
    budget_usd: float
    changed: list[tuple[EngineConfig, EngineConfig]] = field(default_factory=list)


@dataclass(frozen=True)
class CallDone:
    """What run_design reports after each call it made or skipped for budget: its 1-based place among the
    planned calls, the engine, the outcome and what it was charged."""

    index: int
    total: int
    engine: EngineConfig
    status: RunStatus
    error_kind: str | None
    cost_usd: float


@dataclass
class _BurstState:
    """What the store already holds for a burst label: each run's last record, what the label spent and the
    engine configs its manifests ran (by config_sha, first seen first)."""

    last: dict[str, Run]
    prior_spent: float
    label_engines: list[EngineConfig] = field(default_factory=list)


def _burst_state(root: Path, label: str, calls: list[Call]) -> _BurstState:
    """Read the store without creating it. The budget is per burst, so a rerun that resumes the burst starts
    from what the burst already spent: the cost of every run recorded for it, superseded attempts included,
    since each one was charged. A run belongs to the burst when its id is one of this design's run keys or
    its manifest carries the label (the design may have changed between reruns). A damaged record file
    raises ValueError naming its file and line."""
    directory = root / ".footnote"
    if not directory.is_dir():
        return _BurstState({}, 0.0)
    store = JsonlStore(directory)  # the folder exists, so this creates nothing
    burst_keys = {call.key(label) for call in calls}
    label_of: dict[str, str] = {}
    label_engines: dict[str, EngineConfig] = {}
    for m in store.iter("manifests", Manifest):
        label_of[m.id] = m.label
        if m.label == label:
            for engine in m.engines:
                label_engines.setdefault(engine.config_sha, engine)
    last: dict[str, Run] = {}
    prior = 0.0
    for r in store.iter("runs", Run):
        last[r.id] = r  # the last record per id wins
        if r.id in burst_keys or label_of.get(r.manifest_id) == label:
            prior += r.cost_usd
    return _BurstState(last, prior, list(label_engines.values()))


def _changed_configs(
    label_engines: list[EngineConfig], engines: list[EngineConfig]
) -> list[tuple[EngineConfig, EngineConfig]]:
    """(earlier, now) for each engine config the label ran that footnote.toml no longer gives and each
    engine footnote.toml gives now of the same provider and model."""
    current = {e.config_sha for e in engines}
    return [
        (earlier, now)
        for now in engines
        for earlier in label_engines
        if earlier.config_sha not in current
        and (earlier.provider, earlier.model_requested) == (now.provider, now.model_requested)
    ]


@dataclass
class _Outcome:
    status: RunStatus
    obs: Observation | None
    raw_sha: str | None
    parser_version: str | None
    cost: float
    evidence: str
    kind: str | None  # Run.error_kind: None exactly when the status is ok (see schema.ERROR_KINDS)


async def _execute(
    client, adapter: Adapter, raw_store: RawStore, call: Call, api_key: str, table: PriceTable,
    timeout: float, worst: float, sleep,
) -> _Outcome:
    attempt = 0
    while True:
        try:
            raw = await asyncio.wait_for(
                adapter.call(client, call.prompt.text, call.engine, api_key), timeout
            )
            break
        except TimeoutError:
            return _Outcome(
                "timeout", None, None, None, worst,
                f"no response within {timeout:g}s; charged the worst case", "timeout",
            )
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in TRANSIENT and attempt < len(RETRY_DELAYS):
                await sleep(RETRY_DELAYS[attempt])
                attempt += 1
                continue
            return _Outcome(
                "error", None, None, None, 0.0,
                f"HTTP {code} from the provider after {attempt + 1} attempt(s)", "http",
            )
        # Transport evidence names only the exception type: h11's text quotes an illegal header, key included.
        except NOT_SENT as exc:
            if attempt < len(RETRY_DELAYS):
                await sleep(RETRY_DELAYS[attempt])
                attempt += 1
                continue
            return _Outcome(
                "error", None, None, None, 0.0,
                f"transport error: {type(exc).__name__} after {attempt + 1} attempt(s)", "transport",
            )
        except httpx.TransportError as exc:
            return _Outcome(
                "error", None, None, None, worst,
                f"transport error after the request may have reached the provider: {type(exc).__name__} "
                f"on attempt {attempt + 1}; charged the worst case", "transport",
            )
        except UNREADABLE as exc:
            return _Outcome(
                "error", None, None, None, worst,
                f"unreadable response body: {type(exc).__name__}; the request was answered, so charged the "
                "worst case", "unreadable",
            )
    raw_sha = raw_store.put(raw.as_blob())
    # The parser is pure and should never raise; if it does, the blob is kept for replay.
    try:
        obs = adapter.parse(raw)
    except Exception as exc:
        return _Outcome(
            "error", None, raw_sha, adapter.version, worst,
            f"parse failed: {type(exc).__name__}: {exc}; charged the worst case", "parse",
        )
    note = ""
    try:
        cost = adapter.price(obs, table)
    except Exception as exc:
        cost, note = worst, f"; price failed ({type(exc).__name__}), charged the worst case"
    else:
        # Ruling B26: a price is tokens x rates, so an answer that reports no token count would be charged for
        # its searches alone and the cap would leak; it is charged at least the worst case instead.
        if obs.input_tokens is None or obs.output_tokens is None:
            cost, note = max(cost, worst), "; token usage missing; charged the worst case"
    status, reason = effective_status(obs.status, obs.search_calls, obs.failed_searches)
    evidence = obs.activation_evidence + (f"; {reason}" if reason else "") + note
    return _Outcome(
        status, obs, raw_sha, adapter.version, cost, evidence, parsed_error_kind(status, reason)
    )


def _record(
    store: JsonlStore, manifest_id: str, key: str, call: Call, o: _Outcome, started: datetime,
    finished: datetime,
) -> None:
    obs = o.obs
    if obs is not None:
        for role, refs in (("consulted", obs.consulted), ("cited", obs.cited)):
            for ref in refs:
                store.append(
                    "sources",
                    SourceRecord(
                        run_id=key, role=role, url=ref.url, canonical_url=canonicalize(ref.url),
                        rank=ref.rank, provider_field=ref.provider_field, title=ref.title,
                        char_start=ref.char_start, char_end=ref.char_end, raw_sha256=o.raw_sha,
                    ),
                )
    store.append(
        "runs",
        Run(
            id=key, manifest_id=manifest_id, intent_id=call.intent.id, prompt_id=call.prompt.id,
            engine_config_id=call.engine.config_sha, rep_idx=call.rep_idx, status=o.status,
            model_requested=call.engine.model_requested,
            model_returned=obs.model_returned if obs else None,
            tool_version=call.engine.tool_version, activated=obs.activated if obs else "unknown",
            activation_evidence=o.evidence, raw_sha256=o.raw_sha, parser_version=o.parser_version,
            input_tokens=obs.input_tokens if obs else None,
            output_tokens=obs.output_tokens if obs else None,
            search_calls=obs.search_calls if obs else 0, cost_usd=o.cost,
            started_at=started, finished_at=finished, error_kind=o.kind,
        ),
    )


async def run_design(
    root: Path, config: ProjectConfig, intents: list[Intent], engines: list[EngineConfig], label: str,
    budget_usd: float, dry_run: bool, adapters: Mapping[str, Adapter], keys: Mapping[str, str | None],
    table: PriceTable, assumptions: PlanningAssumptions, client: httpx.AsyncClient | None = None,
    clock: Callable[[], datetime] = utcnow, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    timeout_s: float | None = None, on_start: Callable[[BurstStart], None] | None = None,
    on_call: Callable[[CallDone], None] | None = None,
) -> RunnerResult:
    """Run one burst of the design under `label`, or with `dry_run` say what it would do.

    Before anything is written: an engine without a price row, or a provider without an adapter, raises
    RunnerError; the store is read (a damaged record file raises ValueError), so a dry run also reports the
    calls already final, the label's prior spend and the calls left to make. A real run then needs every
    provider's key (RunnerError for a missing one or one a header cannot carry), writes the manifest, calls
    `on_start`, makes or budget-skips each call that is not final, calling `on_call` after each, and appends
    the manifest's final record even when the burst is interrupted.
    """
    calls = enumerate_calls(intents, engines, config.design.reps)
    unpriced = [e for e in engines if not table.is_known(e.provider, e.model_requested)]
    if unpriced:
        names = ", ".join(f"{e.provider}/{e.model_requested}" for e in unpriced)
        raise RunnerError(
            f"engines not priced in pricing.yaml {table.version}: {names}; add a row before running"
        )
    providers = sorted({e.provider for e in engines})
    no_adapter = [p for p in providers if p not in adapters]
    if no_adapter:
        raise RunnerError(f"no adapter for provider(s) {', '.join(no_adapter)}")
    state = _burst_state(root, label, calls)
    tallies = {e.config_sha: EngineTally(e, worst_case_usd(e, table, assumptions)) for e in engines}
    pending: list[tuple[int, Call]] = []  # (1-based place among the planned calls, call) of each call to make
    for position, call in enumerate(calls, start=1):
        prior_run = state.last.get(call.key(label))
        if prior_run is not None and is_final(prior_run):
            tallies[call.engine.config_sha].already_done += 1
        else:
            pending.append((position, call))
    to_call = [call for _, call in pending]
    skipped = len(calls) - len(to_call)
    changed = _changed_configs(state.label_engines, engines)
    if dry_run:
        return RunnerResult(
            None, {}, 0.0, skipped, len(calls), True, state.prior_spent, list(tallies.values()), to_call,
            changed,
        )
    missing = [config.keys.env_name(p) for p in providers if not (keys.get(p) or "").strip()]
    if missing:
        raise RunnerError(f"no API key found in environment variable(s) {', '.join(missing)}")
    # Messages name the variable, never its value.
    unsendable = [config.keys.env_name(p) for p in providers if not _header_safe(keys[p] or "")]
    if unsendable:
        names = ", ".join(unsendable)
        raise RunnerError(
            f"the API key in environment variable(s) {names} cannot be sent in an HTTP header: it holds a "
            "character outside printable ASCII (such as a pasted curly quote, ellipsis or line break) or "
            f"spaces around it; set {names} to the key alone"
        )
    store, raw_store = JsonlStore(root / ".footnote"), RawStore(root / ".footnote")
    prior = state.prior_spent
    manifest = Manifest(
        code_version=__version__, adapter_versions={p: adapters[p].version for p in providers},
        config_sha=config_sha_of(config, intents, engines), price_table_version=table.version,
        budget_usd=budget_usd, label=label, engines=list(engines), intents=list(intents),
        canon_version=CANON_VERSION, planning_version=assumptions.version,
        paraphrases=config.design.paraphrases, reps=config.design.reps, started_at=clock(),
    )
    store.append("manifests", manifest)
    if on_start is not None:
        on_start(BurstStart(label, len(calls), skipped, prior, budget_usd, changed))
    counts: dict[str, int] = defaultdict(int)
    spent = 0.0
    timeout = timeout_s or config.design.call_timeout_s
    own_client = client is None
    client = client or httpx.AsyncClient(max_redirects=5)
    final_status = "aborted"
    try:
        for position, call in pending:
            key = call.key(label)
            started = clock()
            worst = tallies[call.engine.config_sha].worst_case_usd
            if prior + spent + worst > budget_usd:
                earlier = f" (of which {prior:.4f} by earlier runs of label {label})" if prior else ""
                outcome = _Outcome(
                    "budget_skip", None, None, None, 0.0,
                    f"budget: spent {prior + spent:.4f}{earlier} + worst case {worst:.4f} for "
                    f"{call.engine.provider}/{call.engine.model_requested} > budget {budget_usd:.4f} USD",
                    "budget",
                )
            else:
                adapter, api_key = adapters[call.engine.provider], keys[call.engine.provider] or ""
                outcome = await _execute(
                    client, adapter, raw_store, call, api_key, table, timeout, worst, sleep
                )
            spent += outcome.cost
            _record(store, manifest.id, key, call, outcome, started, clock())
            counts[outcome.status] += 1
            tally = tallies[call.engine.config_sha].counts
            tally[outcome.status] = tally.get(outcome.status, 0) + 1
            if on_call is not None:
                done = CallDone(position, len(calls), call.engine, outcome.status, outcome.kind, outcome.cost)
                on_call(done)
            if config.design.politeness_s and outcome.status != "budget_skip":
                await sleep(config.design.politeness_s)
        final_status = "done"
    finally:
        ending = {"spent_usd": spent, "status": final_status, "finished_at": clock()}
        final = manifest.model_copy(update=ending)
        store.append("manifests", final)
        if own_client:
            await client.aclose()
    return RunnerResult(
        final, dict(counts), spent, skipped, len(calls), False, prior, list(tallies.values()), to_call,
        changed,
    )
