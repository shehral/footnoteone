# FootnoteOne instrument core, implementation plan (plan A of two)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the measurement core of FootnoteOne: typed run records and an append-only store, URL canonicalization and owner classification, the statistics module, the robots.txt audit, and the three sanctioned-API adapters with recorded fixtures, each independently tested.

**Architecture:** One Python 3.12 package `footnoteone` (src layout, uv, hatchling). Pure modules with no network except `audit/probe.py` and the adapters' `call()`; every parser is a pure function of a stored raw response so history can be replayed. Records are Pydantic v2 models written as JSONL under `.footnote/`, raw responses content-addressed by sha256.

**Tech Stack:** Python 3.12, pydantic>=2.9, httpx>=0.27, typer>=0.12, pyyaml, jinja2; tests with pytest and pytest-httpx; ruff.

**Spec:** `docs/superpowers/specs/2026-10-05-footnoteone-design.md` (sections 5 to 8 govern this plan) and `spec/METRICS.md` (normative metric definitions).

Plan B (planner, runner, metrics, report, CLI commands `init`, `plan`, `run`, `report`, `diff`, `crawl-check`, `doctor`) follows once these modules exist; its tasks consume the interfaces defined here.

## As built (2026-10-05)

Rulings made during implementation changed some interfaces shown below. Where this plan and the code differ, plan B consumes the code and its tests.

- **Audit (R18, R30):** `fetch_robots` returns `RobotsFetch(status, text, error, access)`, `probe_url` returns `ProbeResult(status, final_url, error)` and `access_matrix` returns `AccessReport(robots_status, robots_error, robots_access, rows)`, whose `AccessRow` adds `final_url` and `probe_error`. An unreachable robots.txt (a 1xx, 429 or 5xx status, or a failed request) means complete disallow; an unavailable one (another 4xx, a final 3xx or too many redirects) means no rules.
- **Perplexity (R4, R33 to R42):** the adapter follows the live Agent API, not the flat stand-in shape in Task 7: `POST https://api.perplexity.ai/v1/agent` with a `preset` and a typed `output` array. Consulted comes from `search_results` items, then `fetch_url_results` pages; cited comes from `url_citation` annotations or, when there are none, from inline `[n]` and `[web:n]` markers resolved by result id. The field constants and fixtures follow that shape.
- **Observation (R41, R44):** gains `model_requested` (every adapter prices by it) and `failed_searches`.
- **Run (R43):** `raw_sha256` and `parser_version` are optional; None means no response body was received (a timeout, a transport or HTTP error, or a budget_skip).
- **Statistics (R14):** `t_quantile`, `t_interval` and `cluster_t_interval` (a Student t interval over per-intent means) sit beside the cluster bootstrap, which under-covers at creator sample sizes.
- **Pricing (R34, R39):** the Perplexity rows follow its Agent API pricing page: web_search at $2.50 per 1k calls, and the fast preset at the higher listed tier, $0.40 in and $1.80 out per 1M tokens.

`spec/METRICS.md` and the design spec still describe the bootstrap interval and the pre-R18 audit; both are queued for an RFC.

## Global Constraints

- Python `>=3.12`; runtime dependencies only `pydantic`, `httpx`, `typer`, `pyyaml`, `jinja2` and the standard library; dev dependencies `pytest`, `pytest-httpx`, `ruff` (spec section 8).
- `ruff check .` must pass with the configured rules (E, F, I, B, UP; line length 110).
- No network in tests: adapters are tested against fixtures in `tests/fixtures/`; HTTP in `audit` is tested with `pytest-httpx`.
- Parsers are pure: `parse(raw)` must not perform I/O and must be deterministic.
- Consulted and cited sources are stored as separate records with `role`; never merge them (spec section 7).
- Every statistic function documents numerator and denominator; `n == 0` returns `None` intervals, never 0.
- No em dashes anywhere in code comments, docstrings or user-facing strings.
- Commits are signed off (`git commit -s`); author identity is the repo's configured one; no assistant attribution lines.
- Run tests with `uv run pytest -q` from the repo root; install with `uv sync --all-extras`.
- Code blocks in this plan may exceed 110 characters; wrap them when transcribing. `ruff check` must pass on every file you touch, and tests are linted too.
- Work only in your own task's worktree and branch; never commit on `main`. `pytest-asyncio` (asyncio_mode auto) is pre-installed, so no task edits `pyproject.toml`.

## Review Focus

Inputs the spec implies but no happy-path test exercises; each line names the owning task, and that task carries the test.

1. A robots.txt with a `User-agent: *` group after a specific group, or two user-agent lines sharing one group, must still resolve the specific agent to its own rules (RFC 9309 group matching). Task 4.
2. A provider response where the engine answered without searching must yield `activated = "no"` with empty consulted and cited sets, not an exception and not `activated = "unknown"`. Tasks 5, 6, 7.
3. A URL with uppercase host, `utm_*` parameters, a fragment, `/amp` suffix and a trailing slash must canonicalize to the same string as its clean form, and two different owned domains must both classify as `own_site`. Task 2.
4. The sign-flip test with all-zero differences must return `p = 1.0`, and with fewer than 6 differences the verdict must be `insufficient` regardless of p. Task 3.
5. Appending a model whose field set later grows must still be readable: the JSONL reader ignores unknown fields instead of failing, so old files keep working after a schema addition. Task 1.

---

### Task 1: Record schema and append-only store

**Files:**
- Create: `src/footnoteone/schema.py`
- Create: `src/footnoteone/store.py`
- Test: `tests/test_schema.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `schema.Prompt`, `schema.Intent`, `schema.EngineConfig` (with `config_sha` property and `surface` default `api:<provider>`), `schema.SourceRef`, `schema.Observation`, `schema.Run`, `schema.SourceRecord`, `schema.Manifest`, `schema.new_id() -> str`, `schema.utcnow() -> datetime`; `store.JsonlStore(root).append(name, model)`, `.iter(name, model_cls)`; `store.RawStore(root).put(obj) -> sha`, `.get(sha) -> dict`, `.exists(sha)`.

- [ ] **Step 1: Write the failing schema tests**

```python
# tests/test_schema.py
import json

from footnoteone.schema import EngineConfig, Intent, Observation, Prompt, Run, SourceRef, utcnow


def test_engine_config_surface_defaults_to_provider_and_sha_is_stable():
    a = EngineConfig(provider="openai", model_requested="gpt-5-mini", tool_version="web_search")
    b = EngineConfig(provider="openai", model_requested="gpt-5-mini", tool_version="web_search")
    assert a.surface == "api:openai"
    assert a.config_sha == b.config_sha
    assert len(a.config_sha) == 64


def test_engine_config_sha_changes_with_params():
    a = EngineConfig(provider="anthropic", model_requested="m", params={"max_uses": 2})
    b = EngineConfig(provider="anthropic", model_requested="m", params={"max_uses": 5})
    assert a.config_sha != b.config_sha


def test_intent_defaults_and_prompts():
    prompts = [Prompt(text="q1"), Prompt(text="q2", paraphrase_idx=1)]
    intent = Intent(label="attribution patching vs activation patching", prompts=prompts)
    assert intent.kind == "unbranded"
    assert [p.paraphrase_idx for p in intent.prompts] == [0, 1]
    assert intent.id


def test_observation_rejects_bad_activation():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Observation(activated="maybe", activation_evidence="", answer_text="")


def test_run_round_trips_through_json_and_ignores_unknown_fields():
    run = Run(
        manifest_id="m1", intent_id="i1", prompt_id="p1", engine_config_id="e" * 64, rep_idx=0,
        status="ok", model_requested="gpt-5-mini", activated="yes", activation_evidence="web_search_call present",
        raw_sha256="a" * 64, parser_version="openai@0.1.0", cost_usd=0.012, started_at=utcnow(), finished_at=utcnow(),
    )
    data = json.loads(run.model_dump_json())
    data["future_field"] = 1
    again = Run.model_validate(data)
    assert again.id == run.id and again.cost_usd == 0.012


def test_source_ref_rank_starts_at_one():
    import pytest
    from pydantic import ValidationError

    SourceRef(url="https://a.example/x", rank=1, provider_field="sources")
    with pytest.raises(ValidationError):
        SourceRef(url="https://a.example/x", rank=0, provider_field="sources")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_schema.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'footnoteone.schema'`

- [ ] **Step 3: Write the schema module**

```python
# src/footnoteone/schema.py
"""Typed records for FootnoteOne. Pure data; no I/O lives here."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


def sha256_of(obj: Any) -> str:
    """sha256 of the canonical JSON form (sorted keys, no spaces)."""
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


class Lenient(BaseModel):
    """Base: unknown fields are ignored so older JSONL files keep loading after a schema addition."""

    model_config = ConfigDict(extra="ignore")


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
    """The pure parse of one raw response."""

    activated: Activation
    activation_evidence: str
    answer_text: str
    consulted: list[SourceRef] = Field(default_factory=list)
    cited: list[SourceRef] = Field(default_factory=list)
    model_returned: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    search_calls: int = 0
    status: RunStatus = "ok"


class Run(Lenient):
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
    raw_sha256: str
    parser_version: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    search_calls: int = 0
    cost_usd: float = 0.0
    started_at: datetime
    finished_at: datetime


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
    started_at: datetime = Field(default_factory=utcnow)
    status: ManifestStatus = "running"
```

- [ ] **Step 4: Run the schema tests to verify they pass**

Run: `uv run pytest tests/test_schema.py -q`
Expected: 6 passed

- [ ] **Step 5: Write the failing store tests**

```python
# tests/test_store.py
from footnoteone.schema import Prompt
from footnoteone.store import JsonlStore, RawStore


def test_jsonl_store_appends_and_iterates_in_order(tmp_path):
    store = JsonlStore(tmp_path / ".footnote")
    store.append("prompts", Prompt(text="first"))
    store.append("prompts", Prompt(text="second"))
    texts = [p.text for p in store.iter("prompts", Prompt)]
    assert texts == ["first", "second"]
    assert (tmp_path / ".footnote" / "prompts.jsonl").exists()


def test_jsonl_store_iter_on_missing_file_is_empty(tmp_path):
    store = JsonlStore(tmp_path / ".footnote")
    assert list(store.iter("runs", Prompt)) == []


def test_jsonl_store_skips_blank_lines_and_ignores_unknown_fields(tmp_path):
    root = tmp_path / ".footnote"
    root.mkdir()
    (root / "prompts.jsonl").write_text('{"id":"x","text":"a","extra":1}\n\n{"id":"y","text":"b"}\n')
    assert [p.text for p in JsonlStore(root).iter("prompts", Prompt)] == ["a", "b"]


def test_raw_store_is_content_addressed_and_idempotent(tmp_path):
    raw = RawStore(tmp_path / ".footnote")
    sha1 = raw.put({"b": 2, "a": 1})
    sha2 = raw.put({"a": 1, "b": 2})
    assert sha1 == sha2 and len(sha1) == 64
    assert raw.exists(sha1)
    assert raw.get(sha1) == {"a": 1, "b": 2}
    assert len(list((tmp_path / ".footnote" / "raw").iterdir())) == 1
```

- [ ] **Step 6: Run the store tests to verify they fail**

Run: `uv run pytest tests/test_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'footnoteone.store'`

- [ ] **Step 7: Write the store module**

```python
# src/footnoteone/store.py
"""Append-only JSONL records and content-addressed raw responses under `.footnote/`."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from footnoteone.schema import sha256_of

M = TypeVar("M", bound=BaseModel)


class JsonlStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.root / f"{name}.jsonl"

    def append(self, name: str, record: BaseModel) -> None:
        with self.path(name).open("a", encoding="utf-8") as fh:
            fh.write(record.model_dump_json() + "\n")

    def iter(self, name: str, model_cls: type[M]) -> Iterator[M]:
        path = self.path(name)
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield model_cls.model_validate_json(line)


class RawStore:
    def __init__(self, root: Path) -> None:
        self.dir = Path(root) / "raw"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, sha: str) -> Path:
        return self.dir / f"{sha}.json"

    def put(self, obj: dict[str, Any]) -> str:
        sha = sha256_of(obj)
        path = self._path(sha)
        if not path.exists():
            path.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str), encoding="utf-8")
        return sha

    def exists(self, sha: str) -> bool:
        return self._path(sha).exists()

    def get(self, sha: str) -> dict[str, Any]:
        return json.loads(self._path(sha).read_text(encoding="utf-8"))
```

- [ ] **Step 8: Run all tests and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all passed; no lint errors

- [ ] **Step 9: Commit**

```bash
git add src/footnoteone/schema.py src/footnoteone/store.py tests/test_schema.py tests/test_store.py
git commit -s -m "feat: record schema and append-only JSONL and raw stores"
```

---

### Task 2: URL canonicalization and owner classification

**Files:**
- Create: `src/footnoteone/canon.py`
- Test: `tests/test_canon.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `canon.canonicalize(url: str) -> str`, `canon.host_of(url: str) -> str` (lowercase, without `www.`), `canon.OwnerClass = Literal["own_site", "own_offsite", "other"]`, `canon.classify_owner(url: str, own_domains: list[str], offsite_prefixes: list[str]) -> OwnerClass`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_canon.py
import pytest

from footnoteone.canon import canonicalize, classify_owner, host_of


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("HTTPS://WWW.Example.com/Guide/", "https://example.com/Guide"),
        ("https://example.com/guide?utm_source=x&utm_medium=y", "https://example.com/guide"),
        ("https://example.com/guide?b=2&a=1&fbclid=zz", "https://example.com/guide?a=1&b=2"),
        ("https://example.com/guide#section-3", "https://example.com/guide"),
        ("https://example.com/guide/amp", "https://example.com/guide"),
        ("https://example.com/guide?amp=1", "https://example.com/guide"),
        ("https://example.com:443/guide", "https://example.com/guide"),
        ("https://example.com/", "https://example.com/"),
        ("https://example.com", "https://example.com/"),
        ("http://Example.com/a%7Eb", "http://example.com/a~b"),
    ],
)
def test_canonicalize(raw, expected):
    assert canonicalize(raw) == expected


def test_canonicalize_is_idempotent():
    url = "https://WWW.example.com/Guide/?utm_campaign=c&z=1#frag"
    assert canonicalize(canonicalize(url)) == canonicalize(url)


def test_host_of_strips_www_and_lowercases():
    assert host_of("https://WWW.Example.com/x") == "example.com"


def test_classify_owner_two_own_domains_and_subdomains():
    own = ["example.org", "notes.example.org"]
    assert classify_owner("https://example.org/p", own, []) == "own_site"
    assert classify_owner("https://www.example.org/p", own, []) == "own_site"
    assert classify_owner("https://notes.example.org/p", own, []) == "own_site"
    assert classify_owner("https://other.com/p", own, []) == "other"


def test_classify_owner_offsite_profiles_by_prefix():
    prefixes = ["https://medium.com/@ali", "https://www.youtube.com/@alichannel", "https://alishehral.substack.com"]
    assert classify_owner("https://medium.com/@ali/why-geo-fails-1234", [], prefixes) == "own_offsite"
    assert classify_owner("https://medium.com/@someone-else/post", [], prefixes) == "other"
    assert classify_owner("https://medium.com/@alice/post", [], prefixes) == "other"
    assert classify_owner("https://alishehral.substack.com/p/first-post", [], prefixes) == "own_offsite"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_canon.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'footnoteone.canon'`

- [ ] **Step 3: Write the module**

```python
# src/footnoteone/canon.py
"""URL canonicalization and owner classification. Pure functions."""

from __future__ import annotations

from typing import Literal
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

OwnerClass = Literal["own_site", "own_offsite", "other"]

TRACKING_PREFIXES = ("utm_",)
TRACKING_KEYS = {"fbclid", "gclid", "dclid", "msclkid", "igshid", "mc_cid", "mc_eid", "ref_src", "srsltid", "amp"}
DEFAULT_PORTS = {"http": "80", "https": "443"}


def _clean_path(path: str) -> str:
    path = quote(unquote(path), safe="/~:@!$&'()*+,;=-._")
    if path.endswith("/amp"):
        path = path[: -len("/amp")]
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path or "/"


def canonicalize(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = f":{parts.port}" if parts.port and str(parts.port) != DEFAULT_PORTS.get(scheme) else ""
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(TRACKING_PREFIXES) and k.lower() not in TRACKING_KEYS
    ]
    query.sort()
    return urlunsplit((scheme, host + port, _clean_path(parts.path), urlencode(query), ""))


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def classify_owner(url: str, own_domains: list[str], offsite_prefixes: list[str]) -> OwnerClass:
    canon = canonicalize(url)
    host = host_of(canon)
    for domain in own_domains:
        d = domain.lower().removeprefix("www.")
        if host == d or host.endswith("." + d):
            return "own_site"
    for prefix in offsite_prefixes:
        p = canonicalize(prefix).rstrip("/")
        if canon == p or canon.startswith(p + "/"):
            return "own_offsite"
    return "other"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_canon.py -q`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add src/footnoteone/canon.py tests/test_canon.py
git commit -s -m "feat: URL canonicalization and owner classification"
```

---

### Task 3: Statistics module

**Files:**
- Create: `src/footnoteone/stats.py`
- Test: `tests/test_stats.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `stats.wilson(k, n, z=1.959964) -> tuple[float, float] | None`, `stats.cluster_bootstrap_mean(groups: list[list[float]], b=2000, seed=0) -> tuple[float, float, float]` (mean of cluster means, lo, hi), `stats.paired_sign_flip_p(diffs: list[float], b=20000, seed=0) -> float`, `stats.holm(pvals: list[float]) -> list[float]`, `stats.mde(p, n_per_arm, m=1, icc=0.3, alpha=0.05, power=0.8) -> float`, `stats.Verdict = Literal["moved", "no_change", "cant_tell", "insufficient"]`, `stats.verdict(diff_lo, diff_hi, p_adj, n_intents, threshold=0.10, min_intents=6) -> Verdict`, `stats.jaccard(a: set, b: set) -> float | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stats.py
import math
import random

import pytest

from footnoteone.stats import cluster_bootstrap_mean, holm, jaccard, mde, paired_sign_flip_p, verdict, wilson


def test_wilson_known_values_and_edges():
    lo, hi = wilson(12, 40)
    assert 0.17 < lo < 0.19 and 0.45 < hi < 0.47  # 12/40 = 0.30, Wilson 95% about (0.18, 0.46)
    assert wilson(0, 10) == pytest.approx((0.0, 0.2775), abs=0.01)
    assert wilson(10, 10)[1] == 1.0
    assert wilson(0, 0) is None


def test_wilson_interval_coverage_is_near_nominal():
    rng = random.Random(1)
    p, n, covered, trials = 0.3, 40, 0, 2000
    for _ in range(trials):
        k = sum(rng.random() < p for _ in range(n))
        lo, hi = wilson(k, n)
        covered += lo <= p <= hi
    assert 0.93 <= covered / trials <= 0.975


def test_cluster_bootstrap_mean_of_cluster_means():
    groups = [[1, 1, 1], [0, 0, 0], [1, 0, 1, 0]]
    mean, lo, hi = cluster_bootstrap_mean(groups, b=500, seed=3)
    assert mean == pytest.approx(0.5)  # (1 + 0 + 0.5) / 3
    assert 0.0 <= lo <= mean <= hi <= 1.0


def test_cluster_bootstrap_single_cluster_is_degenerate_but_defined():
    mean, lo, hi = cluster_bootstrap_mean([[0.2, 0.4]], b=100, seed=0)
    assert mean == lo == hi == pytest.approx(0.3)


def test_sign_flip_exact_small_n_and_zero_diffs():
    assert paired_sign_flip_p([0.0, 0.0, 0.0, 0.0]) == 1.0
    p = paired_sign_flip_p([0.3, 0.2, 0.25, 0.4, 0.35, 0.3])  # all positive, n = 6 -> 2 / 64
    assert p == pytest.approx(2 / 64)


def test_sign_flip_type_one_error_near_alpha_under_null():
    rng = random.Random(7)
    rejections, trials, n = 0, 300, 10
    for _ in range(trials):
        diffs = [rng.gauss(0, 1) for _ in range(n)]
        rejections += paired_sign_flip_p(diffs) < 0.05
    assert rejections / trials < 0.09


def test_sign_flip_large_n_uses_monte_carlo_and_detects_shift():
    rng = random.Random(11)
    diffs = [rng.gauss(0.5, 1) for _ in range(40)]
    assert paired_sign_flip_p(diffs, b=5000, seed=1) < 0.05


def test_holm_adjustment_is_monotone_and_capped():
    adj = holm([0.01, 0.04, 0.03])
    assert adj[0] == pytest.approx(0.03)
    assert adj[2] == pytest.approx(0.06)
    assert adj[1] == pytest.approx(0.06)
    assert all(0 <= a <= 1 for a in holm([0.5, 0.9, 0.7]))


def test_mde_matches_formula_and_grows_with_icc():
    base = mde(0.2, n_per_arm=200, m=1, icc=0.0)
    expected = (1.959964 + 0.841621) * math.sqrt(2 * 0.2 * 0.8 / 200)
    assert base == pytest.approx(expected, rel=1e-4)
    assert mde(0.2, n_per_arm=200, m=6, icc=0.3) > base


def test_verdict_rules():
    assert verdict(0.02, 0.18, p_adj=0.01, n_intents=12) == "moved"
    assert verdict(-0.03, 0.04, p_adj=0.6, n_intents=12) == "no_change"
    assert verdict(-0.15, 0.20, p_adj=0.6, n_intents=12) == "cant_tell"
    assert verdict(0.02, 0.18, p_adj=0.01, n_intents=5) == "insufficient"


def test_jaccard():
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert jaccard(set(), set()) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_stats.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'footnoteone.stats'`

- [ ] **Step 3: Write the module**

```python
# src/footnoteone/stats.py
"""Statistics with their denominators spelled out. Pure functions, standard library only."""

from __future__ import annotations

import itertools
import math
import random
from statistics import NormalDist, mean
from typing import Literal

Verdict = Literal["moved", "no_change", "cant_tell", "insufficient"]
_Z = NormalDist()


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float] | None:
    """Wilson score interval for k successes in n trials. None when n == 0 (never 0)."""
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def cluster_bootstrap_mean(groups: list[list[float]], b: int = 2000, seed: int = 0) -> tuple[float, float, float]:
    """Mean of cluster means (one cluster per intent) with a percentile bootstrap over clusters.

    Resamples clusters with replacement; the statistic is the mean of the resampled clusters' means,
    which matches METRICS.md: intent level = mean over its runs, library level = mean over intents.
    """
    means = [mean(g) for g in groups if g]
    if not means:
        raise ValueError("cluster_bootstrap_mean needs at least one non-empty cluster")
    point = mean(means)
    if len(means) == 1:
        return point, point, point
    rng = random.Random(seed)
    draws = sorted(mean(rng.choices(means, k=len(means))) for _ in range(b))
    lo = draws[int(0.025 * (b - 1))]
    hi = draws[int(0.975 * (b - 1))]
    return point, lo, hi


def paired_sign_flip_p(diffs: list[float], b: int = 20000, seed: int = 0) -> float:
    """Two-sided p for mean(diffs) == 0 by sign flipping. Exact for n <= 12, Monte Carlo above."""
    n = len(diffs)
    if n == 0:
        return 1.0
    observed = abs(sum(diffs))
    if observed == 0:
        return 1.0
    if n <= 12:
        count = 0
        for signs in itertools.product((1, -1), repeat=n):
            if abs(sum(s * d for s, d in zip(signs, diffs, strict=True))) >= observed - 1e-12:
                count += 1
        return count / (2**n)
    rng = random.Random(seed)
    count = 0
    for _ in range(b):
        if abs(sum(d if rng.random() < 0.5 else -d for d in diffs)) >= observed - 1e-12:
            count += 1
    return (count + 1) / (b + 1)


def holm(pvals: list[float]) -> list[float]:
    """Holm step-down adjusted p-values, in the input order."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * pvals[idx])
        adjusted[idx] = min(1.0, running)
    return adjusted


def mde(p: float, n_per_arm: int, m: int = 1, icc: float = 0.3, alpha: float = 0.05, power: float = 0.8) -> float:
    """Minimum detectable difference in proportion units for a two-arm comparison.

    (z_{1-alpha/2} + z_{power}) * sqrt(2 p (1 - p) DEFF / N), DEFF = 1 + (m - 1) ICC,
    m = answers per intent, N = answers per arm (METRICS.md).
    """
    if n_per_arm <= 0:
        raise ValueError("n_per_arm must be positive")
    deff = 1 + (m - 1) * icc
    z = _Z.inv_cdf(1 - alpha / 2) + _Z.inv_cdf(power)
    return z * math.sqrt(2 * p * (1 - p) * deff / n_per_arm)


def verdict(
    diff_lo: float, diff_hi: float, p_adj: float, n_intents: int, threshold: float = 0.10, min_intents: int = 6
) -> Verdict:
    """METRICS.md change verdict. The CI bounds are differences in proportion units."""
    if n_intents < min_intents:
        return "insufficient"
    if p_adj < 0.05:
        return "moved"
    if -threshold <= diff_lo and diff_hi <= threshold:
        return "no_change"
    return "cant_tell"


def jaccard(a: set, b: set) -> float | None:
    union = a | b
    if not union:
        return None
    return len(a & b) / len(union)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_stats.py -q`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/footnoteone/stats.py tests/test_stats.py
git commit -s -m "feat: statistics module (Wilson, cluster bootstrap, sign-flip test, Holm, MDE, verdicts)"
```

---

### Task 4: robots.txt audit (RFC 9309 parser, bot registry, HTTP probes)

**Files:**
- Create: `src/footnoteone/audit/robots.py`
- Create: `src/footnoteone/audit/bots.yaml`
- Create: `src/footnoteone/audit/probe.py`
- Test: `tests/test_robots.py`, `tests/test_probe.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `robots.Rule(allow: bool, pattern: str, line_no: int)`, `robots.RobotsPolicy.rules_for(agent) -> list[Rule]`, `robots.RobotsPolicy.allowed(agent, path) -> tuple[bool, Rule | None]`, `robots.parse_robots(text: str) -> RobotsPolicy`, `robots.load_bots() -> list[Bot]` where `Bot(name, token, vendor, purpose: Literal["search", "user_fetch", "training"], doc_url)`; `probe.fetch_robots(client, origin) -> str | None`, `probe.probe_url(client, url, user_agent) -> int | None`, `probe.access_matrix(client, site_url, paths) -> list[AccessRow]` with `AccessRow(bot, purpose, path, robots_allowed, matched_rule, http_status)`.

- [ ] **Step 1: Write the failing parser tests**

```python
# tests/test_robots.py
from footnoteone.audit.robots import load_bots, parse_robots

ROBOTS = """
# comment
User-agent: *
Disallow: /private/
Allow: /private/public-note

User-agent: GPTBot
User-agent: ClaudeBot
Disallow: /

User-agent: OAI-SearchBot
Allow: /

User-agent: Googlebot
Disallow: /*.pdf$
Disallow: /tmp*
"""


def test_specific_group_wins_over_star_even_when_star_comes_first():
    policy = parse_robots(ROBOTS)
    allowed, rule = policy.allowed("GPTBot", "/posts/one")
    assert allowed is False and rule is not None and rule.pattern == "/" and rule.line_no == 9


def test_two_user_agent_lines_share_one_group():
    policy = parse_robots(ROBOTS)
    assert policy.allowed("ClaudeBot", "/anything")[0] is False


def test_star_group_applies_to_unknown_agents_with_longest_match_and_allow_tiebreak():
    policy = parse_robots(ROBOTS)
    assert policy.allowed("PerplexityBot", "/private/secret")[0] is False
    assert policy.allowed("PerplexityBot", "/private/public-note")[0] is True
    assert policy.allowed("PerplexityBot", "/open")[0] is True


def test_agent_match_is_case_insensitive_on_the_product_token():
    policy = parse_robots(ROBOTS)
    assert policy.allowed("oai-searchbot/1.0", "/private/secret")[0] is True


def test_wildcards_and_end_anchor():
    policy = parse_robots(ROBOTS)
    assert policy.allowed("Googlebot", "/docs/file.pdf")[0] is False
    assert policy.allowed("Googlebot", "/docs/file.pdf?x=1")[0] is True
    assert policy.allowed("Googlebot", "/tmp/anything")[0] is False
    assert policy.allowed("Googlebot", "/tmpest")[0] is False


def test_empty_robots_allows_everything_with_no_rule():
    policy = parse_robots("")
    assert policy.allowed("GPTBot", "/x") == (True, None)


def test_percent_encoding_is_normalized():
    policy = parse_robots("User-agent: *\nDisallow: /caf%C3%A9/\n")
    assert policy.allowed("x", "/café/menu")[0] is False


def test_bot_registry_has_the_known_agents_with_purposes():
    bots = {b.token: b for b in load_bots()}
    for token in ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "Perplexity-User",
                  "ClaudeBot", "Claude-SearchBot", "Claude-User", "Googlebot", "Google-Extended", "Bingbot"]:
        assert token in bots, token
    assert bots["GPTBot"].purpose == "training"
    assert bots["OAI-SearchBot"].purpose == "search"
    assert bots["ChatGPT-User"].purpose == "user_fetch"
    assert bots["Google-Extended"].purpose == "training"
    assert bots["ChatGPT-User"].note and bots["Perplexity-User"].note
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_robots.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'footnoteone.audit.robots'`

- [ ] **Step 3: Write the bot registry**

```yaml
# src/footnoteone/audit/bots.yaml
# Purposes: search = builds the engine's search index; user_fetch = fetches on a user's request at
# answer time; training = collects training data. Document URLs come from the Oct 4, 2026 landscape
# corpus (R307 OpenAI, R308 Perplexity, R309 Anthropic, R310 Google); the Bing page is the vendor's
# crawler help article. `checked` is the date the implementer last fetched every doc_url.
version: "2026-10-04"
checked: "2026-10-05"
bots:
  - {name: "OpenAI search indexer", token: "OAI-SearchBot", vendor: "OpenAI", purpose: "search", doc_url: "https://developers.openai.com/api/docs/bots"}
  - {name: "ChatGPT user fetch", token: "ChatGPT-User", vendor: "OpenAI", purpose: "user_fetch", doc_url: "https://developers.openai.com/api/docs/bots", note: "OpenAI says robots.txt rules may not apply to user-initiated fetches."}
  - {name: "OpenAI training crawler", token: "GPTBot", vendor: "OpenAI", purpose: "training", doc_url: "https://developers.openai.com/api/docs/bots"}
  - {name: "Perplexity search indexer", token: "PerplexityBot", vendor: "Perplexity", purpose: "search", doc_url: "https://docs.perplexity.ai/guides/bots"}
  - {name: "Perplexity user fetch", token: "Perplexity-User", vendor: "Perplexity", purpose: "user_fetch", doc_url: "https://docs.perplexity.ai/guides/bots", note: "Perplexity says this agent generally ignores robots.txt."}
  - {name: "Anthropic search indexer", token: "Claude-SearchBot", vendor: "Anthropic", purpose: "search", doc_url: "https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler"}
  - {name: "Claude user fetch", token: "Claude-User", vendor: "Anthropic", purpose: "user_fetch", doc_url: "https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler"}
  - {name: "Anthropic training crawler", token: "ClaudeBot", vendor: "Anthropic", purpose: "training", doc_url: "https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler"}
  - {name: "Google Search crawler", token: "Googlebot", vendor: "Google", purpose: "search", doc_url: "https://developers.google.com/search/docs/crawling-indexing/google-common-crawlers"}
  - {name: "Google AI training control", token: "Google-Extended", vendor: "Google", purpose: "training", doc_url: "https://developers.google.com/search/docs/crawling-indexing/google-common-crawlers", note: "Does not affect Google Search inclusion, AI Overviews or AI Mode."}
  - {name: "Bing crawler", token: "Bingbot", vendor: "Microsoft", purpose: "search", doc_url: "https://www.bing.com/webmasters/help/which-crawlers-does-bing-use-8c184ec0"}
```

Then fetch each `doc_url` once (WebFetch). If a page is gone, find the vendor's current crawler page, replace the URL, and set `checked` to the date you fetched them.

- [ ] **Step 4: Write the parser**

```python
# src/footnoteone/audit/robots.py
"""robots.txt parsing and matching per RFC 9309, plus the AI bot registry."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Literal
from urllib.parse import quote, unquote

import yaml

Purpose = Literal["search", "user_fetch", "training"]


@dataclass(frozen=True)
class Rule:
    allow: bool
    pattern: str
    line_no: int


@dataclass(frozen=True)
class Bot:
    name: str
    token: str
    vendor: str
    purpose: Purpose
    doc_url: str
    note: str = ""


def _normalize_path(path: str) -> str:
    """Percent-decode then re-encode so `/caf%C3%A9/` and `/café/` compare equal (RFC 9309 2.2.2)."""
    return quote(unquote(path), safe="/*$?=&%~:@!+,;'()-._")


def _product_token(value: str) -> str:
    """Lowercase product token: the user-agent value up to the first slash or whitespace."""
    return re.split(r"[/\s]", value.strip().lower(), maxsplit=1)[0]


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    out = "^"
    for ch in pattern:
        if ch == "*":
            out += ".*"
        elif ch == "$":
            out += "$"
        else:
            out += re.escape(ch)
    return re.compile(out)


@dataclass
class RobotsPolicy:
    groups: dict[str, list[Rule]] = field(default_factory=dict)  # lowercase agent token -> rules

    def rules_for(self, agent: str) -> list[Rule]:
        """The group whose user-agent token equals the crawler's product token (RFC 9309 2.2.1), else `*`."""
        product = _product_token(agent)
        if product != "*" and product in self.groups:
            return self.groups[product]
        return self.groups.get("*", [])

    def allowed(self, agent: str, path: str) -> tuple[bool, Rule | None]:
        """Longest-match rule wins; on equal length allow wins; no matching rule means allowed."""
        target = _normalize_path(path)
        best: Rule | None = None
        best_len = -1
        for rule in self.rules_for(agent):
            if _pattern_to_regex(_normalize_path(rule.pattern)).match(target):
                length = len(rule.pattern)
                if length > best_len or (length == best_len and rule.allow and best is not None and not best.allow):
                    best, best_len = rule, length
        if best is None:
            return True, None
        return best.allow, best


def parse_robots(text: str) -> RobotsPolicy:
    policy = RobotsPolicy()
    current: list[str] = []
    collecting_agents = False
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if not collecting_agents:
                current = []
                collecting_agents = True
            token = _product_token(value)
            current.append(token)
            policy.groups.setdefault(token, [])
        elif key in ("allow", "disallow"):
            collecting_agents = False
            if not current:
                continue
            if value == "" and key == "disallow":
                continue  # empty Disallow means allow all; no rule needed
            for token in current:
                policy.groups[token].append(Rule(allow=(key == "allow"), pattern=value, line_no=line_no))
        else:
            collecting_agents = False  # sitemap, crawl-delay and unknown lines end the agent list
    return policy


def load_bots() -> list[Bot]:
    data = yaml.safe_load(resources.files("footnoteone.audit").joinpath("bots.yaml").read_text(encoding="utf-8"))
    return [Bot(**b) for b in data["bots"]]
```

- [ ] **Step 5: Run the parser tests to verify they pass**

Run: `uv run pytest tests/test_robots.py -q`
Expected: 8 passed

- [ ] **Step 6: Write the failing probe tests**

```python
# tests/test_probe.py
import httpx
import pytest

from footnoteone.audit.probe import access_matrix, fetch_robots, probe_url


@pytest.mark.asyncio
async def test_fetch_robots_returns_text_or_none(httpx_mock):
    httpx_mock.add_response(url="https://site.example/robots.txt", text="User-agent: *\nDisallow: /x\n")
    httpx_mock.add_response(url="https://empty.example/robots.txt", status_code=404)
    async with httpx.AsyncClient() as client:
        assert "Disallow" in await fetch_robots(client, "https://site.example")
        assert await fetch_robots(client, "https://empty.example") is None


@pytest.mark.asyncio
async def test_probe_url_sends_user_agent_and_returns_status(httpx_mock):
    httpx_mock.add_response(url="https://site.example/p", status_code=200, match_headers={"User-Agent": "GPTBot"})
    async with httpx.AsyncClient() as client:
        assert await probe_url(client, "https://site.example/p", "GPTBot") == 200


@pytest.mark.asyncio
async def test_access_matrix_combines_rules_and_probes(httpx_mock):
    httpx_mock.add_response(url="https://site.example/robots.txt", text="User-agent: GPTBot\nDisallow: /\n")
    async with httpx.AsyncClient() as client:
        rows = await access_matrix(client, "https://site.example", ["/p"], probe=False)
    by_bot = {r.bot: r for r in rows}
    assert by_bot["GPTBot"].robots_allowed is False and by_bot["GPTBot"].matched_rule == "Disallow: / (line 2)"
    assert by_bot["OAI-SearchBot"].robots_allowed is True and by_bot["OAI-SearchBot"].matched_rule is None
    assert all(r.http_status is None for r in rows)
```

- [ ] **Step 7: Run the probe tests to verify they fail**

`pytest-asyncio` (asyncio_mode auto) is already in the dev extras; nothing to install.

Run: `uv run pytest tests/test_probe.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'footnoteone.audit.probe'`

- [ ] **Step 8: Write the probe module**

```python
# src/footnoteone/audit/probe.py
"""Network side of the audit: fetch robots.txt and probe URLs with each bot's user agent."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from footnoteone.audit.robots import Purpose, load_bots, parse_robots


@dataclass(frozen=True)
class AccessRow:
    bot: str
    purpose: Purpose
    path: str
    robots_allowed: bool
    matched_rule: str | None
    http_status: int | None


async def fetch_robots(client: httpx.AsyncClient, origin: str) -> str | None:
    parts = urlsplit(origin)
    url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        resp = await client.get(url, timeout=10.0, follow_redirects=True)
    except httpx.HTTPError:
        return None
    return resp.text if resp.status_code == 200 else None


async def probe_url(client: httpx.AsyncClient, url: str, user_agent: str) -> int | None:
    try:
        resp = await client.get(url, headers={"User-Agent": user_agent}, timeout=10.0, follow_redirects=True)
    except httpx.HTTPError:
        return None
    return resp.status_code


async def access_matrix(
    client: httpx.AsyncClient, site_url: str, paths: list[str], probe: bool = True
) -> list[AccessRow]:
    """One row per (bot, path): the robots verdict with its matched rule, and the live status if probed."""
    parts = urlsplit(site_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    policy = parse_robots(await fetch_robots(client, origin) or "")
    rows: list[AccessRow] = []
    for bot in load_bots():
        for path in paths:
            allowed, rule = policy.allowed(bot.token, path)
            matched = None
            if rule is not None:
                matched = f"{'Allow' if rule.allow else 'Disallow'}: {rule.pattern} (line {rule.line_no})"
            ua = f"Mozilla/5.0 (compatible; {bot.token}; +{bot.doc_url})"
            status = await probe_url(client, origin + path, ua) if probe else None
            rows.append(AccessRow(bot.token, bot.purpose, path, allowed, matched, status))
    return rows
```

- [ ] **Step 9: Run all tests and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all passed; no lint errors

- [ ] **Step 10: Commit**

```bash
git add src/footnoteone/audit tests/test_robots.py tests/test_probe.py
git commit -s -m "feat: robots.txt audit (RFC 9309 parser, AI bot registry, HTTP probes)"
```

---

### Task 5: Adapter contract, price table, and the OpenAI web search adapter

**Files:**
- Create: `src/footnoteone/adapters/base.py`
- Create: `src/footnoteone/pricing.yaml`
- Create: `src/footnoteone/pricing.py`
- Create: `src/footnoteone/adapters/openai.py`
- Create: `tests/fixtures/openai_web_search_searched.json`, `tests/fixtures/openai_web_search_no_search.json`
- Test: `tests/test_pricing.py`, `tests/test_adapter_openai.py`

**Interfaces:**
- Consumes: `schema.EngineConfig`, `schema.Observation`, `schema.SourceRef`, `schema.utcnow`.
- Produces: `base.RawResponse(provider, model_requested, request: dict, response: dict, fetched_at)`, `base.Adapter` protocol with `provider`, `version`, `build_request(prompt_text, engine) -> dict`, `async call(client, prompt_text, engine, api_key) -> RawResponse`, `parse(raw) -> Observation`, `price(obs, table) -> float`; `pricing.PriceTable.load() -> PriceTable` with `.version`, `.tool_call_usd(provider) -> float` (per call), `.token_usd(provider, model, input_tokens, output_tokens) -> float`; `openai.OpenAIAdapter`.

**Before writing fixtures:** fetch the current OpenAI Responses API web search documentation (developers.openai.com/api/docs/guides/tools-web-search) and confirm three facts, then record them at the top of the fixture file as a `_doc_note` key: the output item type for a search call (`web_search_call`) and where consulted URLs appear when the request includes `include: ["web_search_call.action.sources"]`; the annotation type (`url_citation`) with `url`, `title`, `start_index`, `end_index` on message content parts; the `usage` field names (`input_tokens`, `output_tokens`). If the live docs differ from the shapes below, follow the docs and adjust the parser and tests together.

- [ ] **Step 1: Write the price table and its tests**

```yaml
# src/footnoteone/pricing.yaml
# List prices from the Oct 4, 2026 landscape corpus: OpenAI R286 (web_search $10 per 1k calls plus
# tokens at model rates), Anthropic R289 ($10 per 1k searches plus tokens), Perplexity R283 (Sonar
# $1/$1 per 1M tokens plus a $5 per 1k request fee at low search context; Agent API presets run on
# third-party models, so re-read docs.perplexity.ai/guides/pricing before trusting the token rows).
# Token prices are per 1M tokens. Unknown models fall back to the provider default and `doctor` flags
# them. Bump `version` whenever a number changes.
version: "2026-10-04"
providers:
  openai:
    tool_call_usd_per_1k: 10.0
    default_model: "gpt-5-mini"
    models:
      gpt-5-mini: {input_per_1m: 0.25, output_per_1m: 2.00}
  anthropic:
    tool_call_usd_per_1k: 10.0
    default_model: "claude-sonnet-4-5"
    models:
      claude-sonnet-4-5: {input_per_1m: 3.00, output_per_1m: 15.00}
  perplexity:
    tool_call_usd_per_1k: 5.0
    default_model: "fast"
    models:
      fast: {input_per_1m: 1.00, output_per_1m: 1.00}
```

```python
# tests/test_pricing.py
import pytest

from footnoteone.pricing import PriceTable


def test_price_table_loads_version_and_tool_fees():
    table = PriceTable.load()
    assert table.version == "2026-10-04"
    assert table.tool_call_usd("openai") == pytest.approx(0.010)
    assert table.tool_call_usd("perplexity") == pytest.approx(0.005)


def test_token_usd_uses_model_prices_and_default_for_unknown_model():
    table = PriceTable.load()
    known = table.token_usd("openai", "gpt-5-mini", input_tokens=1_000_000, output_tokens=0)
    assert known == pytest.approx(0.25)
    unknown = table.token_usd("openai", "gpt-999", input_tokens=1_000_000, output_tokens=0)
    assert unknown == known  # falls back to the provider default model
    assert table.is_known("openai", "gpt-999") is False
```

```python
# src/footnoteone/pricing.py
"""Versioned list prices. Every cost figure in the project points back to this table's version."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Any

import yaml


@dataclass(frozen=True)
class PriceTable:
    version: str
    providers: dict[str, Any]

    @classmethod
    def load(cls) -> PriceTable:
        data = yaml.safe_load(resources.files("footnoteone").joinpath("pricing.yaml").read_text(encoding="utf-8"))
        return cls(version=str(data["version"]), providers=data["providers"])

    def tool_call_usd(self, provider: str) -> float:
        return float(self.providers[provider]["tool_call_usd_per_1k"]) / 1000.0

    def is_known(self, provider: str, model: str) -> bool:
        return model in self.providers[provider]["models"]

    def token_usd(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
        p = self.providers[provider]
        prices = p["models"].get(model) or p["models"][p["default_model"]]
        return input_tokens / 1e6 * float(prices["input_per_1m"]) + output_tokens / 1e6 * float(prices["output_per_1m"])
```

No `pyproject.toml` change is needed for the YAML: hatchling's `packages = ["src/footnoteone"]` ships every file under that directory, not only `.py` files.

Run: `uv run pytest tests/test_pricing.py -q`
Expected: 2 passed

- [ ] **Step 2: Write the adapter contract**

```python
# src/footnoteone/adapters/base.py
"""Adapter contract: `call` talks to the network, `parse` is pure, `price` reads the table."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, Field

from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig, Observation, Provider, utcnow


class RawResponse(BaseModel):
    provider: Provider
    model_requested: str
    request: dict[str, Any]
    response: dict[str, Any]
    fetched_at: datetime = Field(default_factory=utcnow)

    def as_blob(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@runtime_checkable
class Adapter(Protocol):
    provider: Provider
    version: str

    def build_request(self, prompt_text: str, engine: EngineConfig) -> dict[str, Any]: ...

    async def call(self, client: httpx.AsyncClient, prompt_text: str, engine: EngineConfig, api_key: str) -> RawResponse: ...

    def parse(self, raw: RawResponse) -> Observation: ...

    def price(self, obs: Observation, table: PriceTable) -> float: ...


def default_price(provider: Provider, model: str, obs: Observation, table: PriceTable) -> float:
    """Tool-call fee times searches performed, plus token cost. Unknown token counts cost 0 and are flagged upstream."""
    tokens = table.token_usd(provider, model, obs.input_tokens or 0, obs.output_tokens or 0)
    return obs.search_calls * table.tool_call_usd(provider) + tokens
```

- [ ] **Step 3: Write the OpenAI fixtures**

```json
// tests/fixtures/openai_web_search_searched.json
{
  "_doc_note": "Shape per the Responses API web_search guide; replace this sentence with the guide URL and the date you read it. include=['web_search_call.action.sources'] exposes consulted URLs.",
  "provider": "openai",
  "model_requested": "gpt-5-mini",
  "request": {"model": "gpt-5-mini", "tools": [{"type": "web_search"}], "include": ["web_search_call.action.sources"], "input": "What is attribution patching?"},
  "response": {
    "id": "resp_1", "model": "gpt-5-mini-2026-01-01", "status": "completed",
    "output": [
      {"type": "web_search_call", "id": "ws_1", "status": "completed",
       "action": {"type": "search", "query": "attribution patching interpretability",
                  "sources": [{"type": "url", "url": "https://example.org/notes/attribution-patching"},
                              {"type": "url", "url": "https://www.lesswrong.com/posts/abc/attribution-patching"},
                              {"type": "url", "url": "https://arxiv.org/abs/2310.10348"}]}},
      {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
       "content": [{"type": "output_text",
                    "text": "Attribution patching approximates activation patching with gradients. It was introduced as a fast alternative.",
                    "annotations": [
                      {"type": "url_citation", "url": "https://www.lesswrong.com/posts/abc/attribution-patching", "title": "Attribution Patching", "start_index": 0, "end_index": 64},
                      {"type": "url_citation", "url": "https://example.org/notes/attribution-patching?utm_source=chatgpt.com", "title": "Notes", "start_index": 65, "end_index": 112}
                    ]}]}
    ],
    "usage": {"input_tokens": 812, "output_tokens": 96, "total_tokens": 908}
  }
}
```

```json
// tests/fixtures/openai_web_search_no_search.json
{
  "_doc_note": "Same request; the model answered from memory and emitted no web_search_call item.",
  "provider": "openai",
  "model_requested": "gpt-5-mini",
  "request": {"model": "gpt-5-mini", "tools": [{"type": "web_search"}], "include": ["web_search_call.action.sources"], "input": "What is 2 + 2?"},
  "response": {
    "id": "resp_2", "model": "gpt-5-mini-2026-01-01", "status": "completed",
    "output": [{"type": "message", "id": "msg_2", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "4.", "annotations": []}]}],
    "usage": {"input_tokens": 20, "output_tokens": 2, "total_tokens": 22}
  }
}
```

- [ ] **Step 4: Write the failing adapter tests**

```python
# tests/test_adapter_openai.py
import json
from pathlib import Path

import httpx
import pytest

from footnoteone.adapters.base import Adapter, RawResponse
from footnoteone.adapters.openai import OpenAIAdapter
from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> RawResponse:
    data = json.loads((FIX / name).read_text())
    data.pop("_doc_note", None)
    return RawResponse.model_validate(data)


def test_adapter_satisfies_protocol():
    assert isinstance(OpenAIAdapter(), Adapter)


def test_parse_searched_response_separates_consulted_from_cited():
    obs = OpenAIAdapter().parse(load("openai_web_search_searched.json"))
    assert obs.activated == "yes" and "web_search_call" in obs.activation_evidence
    assert [s.url for s in obs.consulted] == [
        "https://example.org/notes/attribution-patching",
        "https://www.lesswrong.com/posts/abc/attribution-patching",
        "https://arxiv.org/abs/2310.10348",
    ]
    assert [s.rank for s in obs.consulted] == [1, 2, 3]
    assert [s.url for s in obs.cited] == [
        "https://www.lesswrong.com/posts/abc/attribution-patching",
        "https://example.org/notes/attribution-patching?utm_source=chatgpt.com",
    ]
    assert obs.cited[0].char_start == 0 and obs.cited[0].char_end == 64
    assert obs.cited[0].provider_field == "message.content.annotations.url_citation"
    assert obs.consulted[0].provider_field == "web_search_call.action.sources"
    assert obs.model_returned == "gpt-5-mini-2026-01-01"
    assert (obs.input_tokens, obs.output_tokens, obs.search_calls) == (812, 96, 1)
    assert obs.answer_text.startswith("Attribution patching")


def test_parse_no_search_response_is_activated_no_with_empty_sets():
    obs = OpenAIAdapter().parse(load("openai_web_search_no_search.json"))
    assert obs.activated == "no"
    assert obs.consulted == [] and obs.cited == []
    assert obs.search_calls == 0 and obs.status == "ok"


def test_parse_marks_incomplete_as_truncated():
    raw = load("openai_web_search_searched.json")
    raw.response["status"] = "incomplete"
    assert OpenAIAdapter().parse(raw).status == "truncated"


def test_price_is_tool_fee_plus_tokens():
    obs = OpenAIAdapter().parse(load("openai_web_search_searched.json"))
    table = PriceTable.load()
    expected = 1 * 0.010 + 812 / 1e6 * 0.25 + 96 / 1e6 * 2.00
    assert OpenAIAdapter().price(obs, table) == pytest.approx(expected)


def test_build_request_includes_sources_and_forced_search_params():
    engine = EngineConfig(provider="openai", model_requested="gpt-5-mini", params={"force_search": True})
    req = OpenAIAdapter().build_request("q", engine)
    assert req["model"] == "gpt-5-mini" and req["input"] == "q"
    assert {"type": "web_search"} in req["tools"] or req["tools"][0]["type"] == "web_search"
    assert "web_search_call.action.sources" in req["include"]
    assert req["tool_choice"] == {"type": "web_search"}


@pytest.mark.asyncio
async def test_call_posts_to_responses_endpoint_with_bearer(httpx_mock):
    fixture = json.loads((FIX / "openai_web_search_searched.json").read_text())
    httpx_mock.add_response(url="https://api.openai.com/v1/responses", json=fixture["response"],
                            match_headers={"Authorization": "Bearer sk-test"})
    engine = EngineConfig(provider="openai", model_requested="gpt-5-mini")
    async with httpx.AsyncClient() as client:
        raw = await OpenAIAdapter().call(client, "q", engine, api_key="sk-test")
    assert raw.response["id"] == "resp_1" and raw.request["input"] == "q"
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `uv run pytest tests/test_adapter_openai.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'footnoteone.adapters.openai'`

- [ ] **Step 6: Write the adapter**

```python
# src/footnoteone/adapters/openai.py
"""OpenAI Responses API with the web_search tool."""

from __future__ import annotations

from typing import Any

import httpx

from footnoteone.adapters.base import RawResponse, default_price
from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig, Observation, SourceRef

ENDPOINT = "https://api.openai.com/v1/responses"
CONSULTED_FIELD = "web_search_call.action.sources"
CITED_FIELD = "message.content.annotations.url_citation"


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
            req["tool_choice"] = {"type": "web_search"}
        if "user_location" in engine.params:
            req["tools"][0]["user_location"] = engine.params["user_location"]
        return req

    async def call(self, client: httpx.AsyncClient, prompt_text: str, engine: EngineConfig, api_key: str) -> RawResponse:
        request = self.build_request(prompt_text, engine)
        resp = await client.post(ENDPOINT, json=request, headers={"Authorization": f"Bearer {api_key}"}, timeout=120.0)
        resp.raise_for_status()
        return RawResponse(provider="openai", model_requested=engine.model_requested, request=request, response=resp.json())

    def parse(self, raw: RawResponse) -> Observation:
        body = raw.response
        consulted: list[SourceRef] = []
        cited: list[SourceRef] = []
        text_parts: list[str] = []
        search_calls = 0
        for item in body.get("output", []):
            if item.get("type") == "web_search_call":
                search_calls += 1
                for src in (item.get("action") or {}).get("sources") or []:
                    if src.get("url"):
                        consulted.append(SourceRef(url=src["url"], rank=len(consulted) + 1, provider_field=CONSULTED_FIELD))
            elif item.get("type") == "message":
                for part in item.get("content", []):
                    if part.get("type") == "output_text":
                        text_parts.append(part.get("text", ""))
                        for ann in part.get("annotations", []) or []:
                            if ann.get("type") == "url_citation" and ann.get("url"):
                                cited.append(
                                    SourceRef(
                                        url=ann["url"], rank=len(cited) + 1, provider_field=CITED_FIELD,
                                        title=ann.get("title"), char_start=ann.get("start_index"), char_end=ann.get("end_index"),
                                    )
                                )
        usage = body.get("usage") or {}
        status_map = {"completed": "ok", "incomplete": "truncated", "failed": "error"}
        status = status_map.get(body.get("status", "completed"), "ok")
        return Observation(
            activated="yes" if search_calls else "no",
            activation_evidence=f"{search_calls} web_search_call item(s) in output" if search_calls else "no web_search_call item in output",
            answer_text="\n".join(text_parts),
            consulted=consulted,
            cited=cited,
            model_returned=body.get("model"),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            search_calls=search_calls,
            status=status,
        )

    def price(self, obs: Observation, table: PriceTable) -> float:
        return default_price("openai", obs.model_returned or table.providers["openai"]["default_model"], obs, table)
```

Note: `default_price` looks the model up by the returned model string; if the dated snapshot name (`gpt-5-mini-2026-01-01`) is not in the table it falls back to the provider default, which is the intended behaviour and is what `doctor` will flag.

- [ ] **Step 7: Run the tests to verify they pass, then lint**

Run: `uv run pytest tests/test_adapter_openai.py tests/test_pricing.py -q && uv run ruff check .`
Expected: 9 passed; no lint errors

- [ ] **Step 8: Commit**

```bash
git add src/footnoteone/adapters/base.py src/footnoteone/adapters/openai.py src/footnoteone/pricing.py src/footnoteone/pricing.yaml tests/fixtures tests/test_pricing.py tests/test_adapter_openai.py pyproject.toml
git commit -s -m "feat: adapter contract, versioned price table, OpenAI web search adapter with fixtures"
```

---

### Task 6: Anthropic web search adapter

**Files:**
- Create: `src/footnoteone/adapters/anthropic.py`
- Create: `tests/fixtures/anthropic_web_search_searched.json`, `tests/fixtures/anthropic_web_search_no_search.json`
- Test: `tests/test_adapter_anthropic.py`

**Interfaces:**
- Consumes: `adapters.base.RawResponse`, `adapters.base.default_price`, `schema.EngineConfig`, `schema.Observation`, `schema.SourceRef`, `pricing.PriceTable`.
- Produces: `anthropic.AnthropicAdapter` (same protocol as Task 5), constants `CONSULTED_FIELD = "web_search_tool_result.content.web_search_result"`, `CITED_FIELD = "text.citations.web_search_result_location"`.

**Before writing fixtures:** fetch the Anthropic web search tool documentation (platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool) and confirm: the request shape (`tools: [{"type": "web_search_20250305", "name": "web_search", "max_uses": N}]`, header `anthropic-version`), the response content blocks (`server_tool_use` with `name: web_search`, `web_search_tool_result` whose `content` lists `web_search_result` items with `url`, `title`, `page_age`, and `text` blocks whose `citations` carry `type: web_search_result_location` with `url`, `title`, `cited_text`), and `usage.server_tool_use.web_search_requests`. Record a `_doc_note` in each fixture. Consulted = the `web_search_result` items in order; cited = the `web_search_result_location` citations in text order. Character offsets are not provided by this API; leave `char_start` and `char_end` as `None`.

- [ ] **Step 1: Write the fixtures**

```json
// tests/fixtures/anthropic_web_search_searched.json
{
  "_doc_note": "Shape per the Anthropic web search tool docs; replace this sentence with the docs URL and the date you read it.",
  "provider": "anthropic",
  "model_requested": "claude-sonnet-4-5",
  "request": {"model": "claude-sonnet-4-5", "max_tokens": 1024, "messages": [{"role": "user", "content": "What is attribution patching?"}],
              "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]},
  "response": {
    "id": "msg_1", "model": "claude-sonnet-4-5-20260101", "stop_reason": "end_turn", "role": "assistant",
    "content": [
      {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "attribution patching"}},
      {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
       "content": [
         {"type": "web_search_result", "url": "https://www.lesswrong.com/posts/abc/attribution-patching", "title": "Attribution Patching", "page_age": "March 2023", "encrypted_content": "x"},
         {"type": "web_search_result", "url": "https://example.org/notes/attribution-patching", "title": "Notes", "page_age": null, "encrypted_content": "y"}
       ]},
      {"type": "text", "text": "Attribution patching uses gradients to approximate patching.",
       "citations": [{"type": "web_search_result_location", "url": "https://example.org/notes/attribution-patching", "title": "Notes", "cited_text": "uses gradients", "encrypted_index": "z"}]},
      {"type": "text", "text": " It is faster than activation patching."}
    ],
    "usage": {"input_tokens": 1200, "output_tokens": 80, "server_tool_use": {"web_search_requests": 1}}
  }
}
```

```json
// tests/fixtures/anthropic_web_search_no_search.json
{
  "_doc_note": "Model answered without invoking the tool; no server_tool_use block.",
  "provider": "anthropic",
  "model_requested": "claude-sonnet-4-5",
  "request": {"model": "claude-sonnet-4-5", "max_tokens": 1024, "messages": [{"role": "user", "content": "What is 2 + 2?"}],
              "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]},
  "response": {"id": "msg_2", "model": "claude-sonnet-4-5-20260101", "stop_reason": "end_turn", "role": "assistant",
               "content": [{"type": "text", "text": "4."}],
               "usage": {"input_tokens": 30, "output_tokens": 2, "server_tool_use": {"web_search_requests": 0}}}
}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_adapter_anthropic.py
import json
from pathlib import Path

import httpx
import pytest

from footnoteone.adapters.anthropic import AnthropicAdapter
from footnoteone.adapters.base import Adapter, RawResponse
from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> RawResponse:
    data = json.loads((FIX / name).read_text())
    data.pop("_doc_note", None)
    return RawResponse.model_validate(data)


def test_protocol():
    assert isinstance(AnthropicAdapter(), Adapter)


def test_parse_searched_keeps_consulted_and_cited_apart():
    obs = AnthropicAdapter().parse(load("anthropic_web_search_searched.json"))
    assert obs.activated == "yes" and obs.search_calls == 1
    assert [s.url for s in obs.consulted] == [
        "https://www.lesswrong.com/posts/abc/attribution-patching",
        "https://example.org/notes/attribution-patching",
    ]
    assert [s.url for s in obs.cited] == ["https://example.org/notes/attribution-patching"]
    assert obs.cited[0].char_start is None
    assert obs.consulted[0].provider_field == "web_search_tool_result.content.web_search_result"
    assert obs.cited[0].provider_field == "text.citations.web_search_result_location"
    assert obs.answer_text == "Attribution patching uses gradients to approximate patching. It is faster than activation patching."
    assert obs.model_returned == "claude-sonnet-4-5-20260101"
    assert (obs.input_tokens, obs.output_tokens) == (1200, 80)


def test_parse_no_search():
    obs = AnthropicAdapter().parse(load("anthropic_web_search_no_search.json"))
    assert obs.activated == "no" and obs.consulted == [] and obs.cited == [] and obs.search_calls == 0


def test_parse_max_tokens_stop_is_truncated():
    raw = load("anthropic_web_search_searched.json")
    raw.response["stop_reason"] = "max_tokens"
    assert AnthropicAdapter().parse(raw).status == "truncated"


def test_price_counts_searches_from_usage():
    obs = AnthropicAdapter().parse(load("anthropic_web_search_searched.json"))
    table = PriceTable.load()
    expected = 1 * 0.010 + 1200 / 1e6 * 3.00 + 80 / 1e6 * 15.00
    assert AnthropicAdapter().price(obs, table) == pytest.approx(expected)


def test_build_request_sets_tool_and_max_uses():
    engine = EngineConfig(provider="anthropic", model_requested="claude-sonnet-4-5", tool_version="web_search_20250305", params={"max_uses": 2})
    req = AnthropicAdapter().build_request("q", engine)
    assert req["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}]
    assert req["messages"][0]["content"] == "q"


@pytest.mark.asyncio
async def test_call_posts_with_api_key_and_version_headers(httpx_mock):
    fixture = json.loads((FIX / "anthropic_web_search_searched.json").read_text())
    httpx_mock.add_response(url="https://api.anthropic.com/v1/messages", json=fixture["response"],
                            match_headers={"x-api-key": "sk-ant-test", "anthropic-version": "2023-06-01"})
    engine = EngineConfig(provider="anthropic", model_requested="claude-sonnet-4-5")
    async with httpx.AsyncClient() as client:
        raw = await AnthropicAdapter().call(client, "q", engine, api_key="sk-ant-test")
    assert raw.response["id"] == "msg_1"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_adapter_anthropic.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'footnoteone.adapters.anthropic'`

- [ ] **Step 4: Write the adapter**

```python
# src/footnoteone/adapters/anthropic.py
"""Anthropic Messages API with the web search server tool."""

from __future__ import annotations

from typing import Any

import httpx

from footnoteone.adapters.base import RawResponse, default_price
from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig, Observation, SourceRef

ENDPOINT = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_TOOL = "web_search_20250305"
CONSULTED_FIELD = "web_search_tool_result.content.web_search_result"
CITED_FIELD = "text.citations.web_search_result_location"


class AnthropicAdapter:
    provider = "anthropic"
    version = "anthropic@0.1.0"

    def build_request(self, prompt_text: str, engine: EngineConfig) -> dict[str, Any]:
        tool: dict[str, Any] = {"type": engine.tool_version or DEFAULT_TOOL, "name": "web_search"}
        if "max_uses" in engine.params:
            tool["max_uses"] = engine.params["max_uses"]
        if "user_location" in engine.params:
            tool["user_location"] = engine.params["user_location"]
        return {
            "model": engine.model_requested,
            "max_tokens": int(engine.params.get("max_tokens", 1024)),
            "messages": [{"role": "user", "content": prompt_text}],
            "tools": [tool],
        }

    async def call(self, client: httpx.AsyncClient, prompt_text: str, engine: EngineConfig, api_key: str) -> RawResponse:
        request = self.build_request(prompt_text, engine)
        headers = {"x-api-key": api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        resp = await client.post(ENDPOINT, json=request, headers=headers, timeout=120.0)
        resp.raise_for_status()
        return RawResponse(provider="anthropic", model_requested=engine.model_requested, request=request, response=resp.json())

    def parse(self, raw: RawResponse) -> Observation:
        body = raw.response
        consulted: list[SourceRef] = []
        cited: list[SourceRef] = []
        text_parts: list[str] = []
        tool_uses = 0
        for block in body.get("content", []):
            kind = block.get("type")
            if kind == "server_tool_use" and block.get("name") == "web_search":
                tool_uses += 1
            elif kind == "web_search_tool_result":
                content = block.get("content")
                if isinstance(content, list):
                    for item in content:
                        if item.get("type") == "web_search_result" and item.get("url"):
                            consulted.append(
                                SourceRef(url=item["url"], rank=len(consulted) + 1, provider_field=CONSULTED_FIELD, title=item.get("title"))
                            )
            elif kind == "text":
                text_parts.append(block.get("text", ""))
                for cit in block.get("citations") or []:
                    if cit.get("type") == "web_search_result_location" and cit.get("url"):
                        cited.append(SourceRef(url=cit["url"], rank=len(cited) + 1, provider_field=CITED_FIELD, title=cit.get("title")))
        usage = body.get("usage") or {}
        searches = int((usage.get("server_tool_use") or {}).get("web_search_requests") or tool_uses)
        status = "truncated" if body.get("stop_reason") == "max_tokens" else "ok"
        return Observation(
            activated="yes" if (tool_uses or searches) else "no",
            activation_evidence=f"{tool_uses} server_tool_use block(s); usage reports {searches} web_search_requests",
            answer_text="".join(text_parts),
            consulted=consulted,
            cited=cited,
            model_returned=body.get("model"),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            search_calls=searches,
            status=status,
        )

    def price(self, obs: Observation, table: PriceTable) -> float:
        return default_price("anthropic", obs.model_returned or table.providers["anthropic"]["default_model"], obs, table)
```

- [ ] **Step 5: Run the tests to verify they pass, then lint**

Run: `uv run pytest tests/test_adapter_anthropic.py -q && uv run ruff check .`
Expected: 7 passed; no lint errors

- [ ] **Step 6: Commit**

```bash
git add src/footnoteone/adapters/anthropic.py tests/fixtures/anthropic_web_search_searched.json tests/fixtures/anthropic_web_search_no_search.json tests/test_adapter_anthropic.py
git commit -s -m "feat: Anthropic web search adapter with fixtures"
```

---

### Task 7: Perplexity Agent API adapter

**Files:**
- Create: `src/footnoteone/adapters/perplexity.py`
- Create: `tests/fixtures/perplexity_agent_searched.json`, `tests/fixtures/perplexity_agent_no_search.json`
- Test: `tests/test_adapter_perplexity.py`

**Interfaces:**
- Consumes: as Task 6.
- Produces: `perplexity.PerplexityAdapter`, constants `CONSULTED_FIELD = "search_results"`, `CITED_FIELD = "citations"`.

**Before writing fixtures:** fetch the Perplexity Agent API documentation (docs.perplexity.ai/docs/agent-api/migrate-from-sonar and the Agent API reference) and confirm the endpoint, the request shape (model or preset, `tools` with `web_search`), and the response fields: `search_results` (title, url, date, last_updated, snippet) and `citations` (list of URLs), plus usage. Record a `_doc_note`. The Oct 4 corpus (R284) says the Agent API returns a typed `output` array with a `message` item and a separate `search_results` item, and that the flat `citations` and `search_results` fields belong to the retired Sonar Chat Completions API. The fixtures below use the flat shape only as a stand-in: rewrite them to the documented shape before writing the parser, keep the test names and the consulted-versus-cited assertions, and verify the endpoint constant. If the Agent API reports tool invocations explicitly, use that count for `search_calls` and activation; otherwise activation is `yes` when `search_results` is non-empty, `no` when the response contains an empty `search_results` list, and `unknown` when the field is absent. Consulted = `search_results` in order; cited = `citations` in order. Perplexity has historically mapped these almost one to one; the test below pins that the two sets are still stored separately.

- [ ] **Step 1: Write the fixtures**

```json
// tests/fixtures/perplexity_agent_searched.json
{
  "_doc_note": "Stand-in shape; rewrite this fixture to the documented Agent API output array and put the docs URL and the date you read it here.",
  "provider": "perplexity",
  "model_requested": "fast",
  "request": {"preset": "fast", "input": "What is attribution patching?", "tools": [{"type": "web_search"}]},
  "response": {
    "id": "pplx_1", "model": "fast",
    "output_text": "Attribution patching approximates activation patching with gradients.",
    "search_results": [
      {"title": "Attribution Patching", "url": "https://www.lesswrong.com/posts/abc/attribution-patching", "date": "2023-03-01", "snippet": "..."},
      {"title": "Notes", "url": "https://example.org/notes/attribution-patching", "date": null, "snippet": "..."},
      {"title": "arXiv", "url": "https://arxiv.org/abs/2310.10348", "date": "2023-10-16", "snippet": "..."}
    ],
    "citations": ["https://www.lesswrong.com/posts/abc/attribution-patching", "https://example.org/notes/attribution-patching"],
    "usage": {"input_tokens": 400, "output_tokens": 60, "web_search_calls": 1}
  }
}
```

```json
// tests/fixtures/perplexity_agent_no_search.json
{
  "_doc_note": "Agent answered without searching: empty search_results and citations, zero web_search_calls.",
  "provider": "perplexity",
  "model_requested": "fast",
  "request": {"preset": "fast", "input": "What is 2 + 2?", "tools": [{"type": "web_search"}]},
  "response": {"id": "pplx_2", "model": "fast", "output_text": "4.", "search_results": [], "citations": [],
               "usage": {"input_tokens": 12, "output_tokens": 2, "web_search_calls": 0}}
}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_adapter_perplexity.py
import json
from pathlib import Path

import httpx
import pytest

from footnoteone.adapters.base import Adapter, RawResponse
from footnoteone.adapters.perplexity import PerplexityAdapter
from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> RawResponse:
    data = json.loads((FIX / name).read_text())
    data.pop("_doc_note", None)
    return RawResponse.model_validate(data)


def test_protocol():
    assert isinstance(PerplexityAdapter(), Adapter)


def test_parse_searched_separates_search_results_from_citations():
    obs = PerplexityAdapter().parse(load("perplexity_agent_searched.json"))
    assert obs.activated == "yes" and obs.search_calls == 1
    assert len(obs.consulted) == 3 and len(obs.cited) == 2
    assert obs.consulted[2].url == "https://arxiv.org/abs/2310.10348"
    assert obs.cited[1].url == "https://example.org/notes/attribution-patching"
    assert obs.consulted[0].provider_field == "search_results" and obs.cited[0].provider_field == "citations"
    assert obs.answer_text.startswith("Attribution patching")


def test_parse_no_search_is_no_with_empty_sets():
    obs = PerplexityAdapter().parse(load("perplexity_agent_no_search.json"))
    assert obs.activated == "no" and obs.consulted == [] and obs.cited == []


def test_parse_missing_search_results_field_is_unknown():
    raw = load("perplexity_agent_searched.json")
    raw.response.pop("search_results")
    raw.response["usage"].pop("web_search_calls")
    assert PerplexityAdapter().parse(raw).activated == "unknown"


def test_price():
    obs = PerplexityAdapter().parse(load("perplexity_agent_searched.json"))
    expected = 1 * 0.005 + 400 / 1e6 * 1.0 + 60 / 1e6 * 1.0
    assert PerplexityAdapter().price(obs, PriceTable.load()) == pytest.approx(expected)


@pytest.mark.asyncio
async def test_call_posts_bearer_to_agent_endpoint(httpx_mock):
    fixture = json.loads((FIX / "perplexity_agent_searched.json").read_text())
    httpx_mock.add_response(url=PerplexityAdapter.endpoint, json=fixture["response"], match_headers={"Authorization": "Bearer pplx-test"})
    engine = EngineConfig(provider="perplexity", model_requested="fast")
    async with httpx.AsyncClient() as client:
        raw = await PerplexityAdapter().call(client, "q", engine, api_key="pplx-test")
    assert raw.response["id"] == "pplx_1"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_adapter_perplexity.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'footnoteone.adapters.perplexity'`

- [ ] **Step 4: Write the adapter**

```python
# src/footnoteone/adapters/perplexity.py
"""Perplexity Agent API with the web_search tool (the Sonar chat completions API ended 2026-09-27)."""

from __future__ import annotations

from typing import Any

import httpx

from footnoteone.adapters.base import RawResponse, default_price
from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig, Observation, SourceRef

CONSULTED_FIELD = "search_results"
CITED_FIELD = "citations"


class PerplexityAdapter:
    provider = "perplexity"
    version = "perplexity@0.1.0"
    endpoint = "https://api.perplexity.ai/v1/responses"  # verify against the Agent API reference and update if it differs

    def build_request(self, prompt_text: str, engine: EngineConfig) -> dict[str, Any]:
        req: dict[str, Any] = {"preset": engine.model_requested, "input": prompt_text, "tools": [{"type": "web_search"}]}
        if "search_domain_filter" in engine.params:
            req["tools"][0]["search_domain_filter"] = engine.params["search_domain_filter"]
        return req

    async def call(self, client: httpx.AsyncClient, prompt_text: str, engine: EngineConfig, api_key: str) -> RawResponse:
        request = self.build_request(prompt_text, engine)
        resp = await client.post(self.endpoint, json=request, headers={"Authorization": f"Bearer {api_key}"}, timeout=120.0)
        resp.raise_for_status()
        return RawResponse(provider="perplexity", model_requested=engine.model_requested, request=request, response=resp.json())

    def parse(self, raw: RawResponse) -> Observation:
        body = raw.response
        results = body.get("search_results")
        consulted = [
            SourceRef(url=r["url"], rank=i + 1, provider_field=CONSULTED_FIELD, title=r.get("title"))
            for i, r in enumerate(results or [])
            if r.get("url")
        ]
        cited = [SourceRef(url=u, rank=i + 1, provider_field=CITED_FIELD) for i, u in enumerate(body.get("citations") or []) if u]
        usage = body.get("usage") or {}
        calls = usage.get("web_search_calls")
        if calls is not None:
            activated = "yes" if int(calls) > 0 else "no"
            evidence = f"usage.web_search_calls = {calls}"
        elif results is None:
            activated, evidence = "unknown", "no search_results field and no web_search_calls in usage"
        else:
            activated, evidence = ("yes" if results else "no"), f"search_results has {len(results)} item(s)"
        return Observation(
            activated=activated,
            activation_evidence=evidence,
            answer_text=body.get("output_text") or "",
            consulted=consulted,
            cited=cited,
            model_returned=body.get("model"),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            search_calls=int(calls or 0),
            status="ok",
        )

    def price(self, obs: Observation, table: PriceTable) -> float:
        return default_price("perplexity", obs.model_returned or table.providers["perplexity"]["default_model"], obs, table)
```

- [ ] **Step 5: Run the tests to verify they pass, then lint and the whole suite**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all passed; no lint errors

- [ ] **Step 6: Commit**

```bash
git add src/footnoteone/adapters/perplexity.py tests/fixtures/perplexity_agent_searched.json tests/fixtures/perplexity_agent_no_search.json tests/test_adapter_perplexity.py
git commit -s -m "feat: Perplexity Agent API adapter with fixtures"
```

---

## Self-review notes

- Spec coverage: section 5 modules `schema`, `canon`, `adapters`, `stats`, `audit` are covered by Tasks 1 to 7; `library`, `plan`, `runner`, `metrics`, `report`, `cli` are plan B. Section 6 metric formulas that are pure functions (Wilson, bootstrap, sign-flip, Holm, MDE, verdict, Jaccard) are in Task 3; the funnel computations that need stored runs are plan B.
- Type consistency: `SourceRef` (parser output) and `SourceRecord` (stored, with `run_id` and `canonical_url`) are distinct on purpose; the runner in plan B maps one to the other through `canon.canonicalize`.
- Review Focus items 1 to 5 each have a test in Tasks 4, 5 to 7, 2, 3 and 1 respectively.
- Known uncertainty: provider response shapes change. Each adapter task starts by reading the live documentation and recording the date in the fixture's `_doc_note`; the Perplexity endpoint constant is explicitly marked for verification.
