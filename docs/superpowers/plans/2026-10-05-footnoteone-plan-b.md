# FootnoteOne plan B: configuration, library, planner, runner, metrics, report, diff and the CLI

## As built (2026-10-06)
- **Departure from METRICS 0.2.0 text, pending RFC-002:** the unconditional cited rate is computed over unbranded intents only (Ruling B28); METRICS line 24 still reads "ok runs with known activation" with no intent scope. The code follows the scope rule (branded intents never enter a headline) until the RFC lands.


The twelve tasks below were built and reviewed on `feat/plan-b`, then a whole-branch review and a final fix wave changed some of the interfaces they describe. Where a task's text and the code differ, the code and this section are right. Each change is a ruling or a review finding recorded in the ledger, `.superpowers/sdd/2026-10-05-footnoteone-plan-b/progress.md` (lines starting "Ruling B" give the reasoning and the cost if wrong); the final wave's brief and report sit beside it.

- **Bursts per month and per-month cost** (Ruling B12): `DesignConfig.bursts_per_month` (default 4, at least 1); `PlanSummary` and `footnote plan` price a month at that many bursts. The detectable move stays burst against burst.
- **Answers per intent** (Ruling B13): `plan.answers_per_intent(intents, reps, paraphrases)` is the m of the MDE and of intents needed, the harmonic mean of wordings x reps over the unbranded intents; the diff uses the same m.
- **Sources name their raw response** (Ruling B14): `SourceRecord.raw_sha256`, written by the runner. The reader rule: a run takes only the sources whose `raw_sha256` equals that of its last record (both None equal), so a superseded or crashed attempt's sources never join the final run.
- **Library refresh** (Rulings B9, B20, B31): `footnote init SITE_URL --pages-only` refreshes `.footnote/pages.jsonl` from the existing footnote.toml and merges by canonical URL (`library.merge_pages(existing, found)`: new pages added, titles and lastmod refreshed, ids and pages no longer found kept); `--prune` rebuilds it; a discovery that finds nothing keeps a library that has pages. `library.write_pages` still replaces. Only sitemap and feed pages count toward `--limit`; a channel's videos come on top.
- **Discovery** (finding E3): `library.discover_pages` returns `Discovery(pages, failed_fetches)`, and init prints "Found 0 pages; N fetches failed".
- **Runner result** (finding D2): `RunnerResult` gains `prior_spent_usd` (what earlier runs of the label spent), `engines` (an `EngineTally` per engine config), `to_call` and `changed`; `manifest` is the manifest's final record. `run_design` takes `on_start(BurstStart)` and `on_call(CallDone)`, raises `RunnerError` before `.footnote/` exists for a provider with no adapter or a key a header cannot carry, and `keys_from_env(config, environ=None)` reads `os.environ` when called.
- **Run.error_kind and the resume policy** (Rulings B23 amended, B30): every run that did not end ok records why (transport, http, unreadable, parse, provider_failed, all_searches_failed, timeout, budget; refused and truncated carry their status), and ok runs carry None. On resume, a run whose last record is refused, truncated, or an error of kind parse is final; every other run that is not ok is asked again. An answer that reports no token usage is charged max(price, worst case) (Ruling B26).
- **Exit codes**: 2 for a problem with the project's setup (config, record files, labels, keys, a library that cannot be written); 1 for `footnote run` when calls were made and none ended ok, or every remaining call was skipped for budget (Ruling B24 amended), and for `footnote doctor` with a failing check; 130 when a run is interrupted.
- **Engine configs from manifests** (finding C1): `metrics.compute` reports every engine config the selected manifests ran (by config_sha, first seen first), then each current engine none of them ran; `EngineMetrics.in_config` marks a config footnote.toml no longer gives. The diff names a same-model config that ran in a window instead, and `footnote run` warns when the label ran one.
- **Unconditional cited scope** (Ruling B28): unconditional cited counts unbranded intents only, numerator and denominator; its method reads "Wilson 95%, unbranded intents".
- **Report scope** (Ruling B29): with no `--label` and no dates the report covers the most recent burst and says so; an unknown label exits 2; the report opens with a summary per engine and a bursts table. The diff prints a Holm-adjusted p below 0.0005 as "p < 0.001" and exits 2 for a label that names no burst or sits in both windows (Ruling B22).
- **Shared-platform sites** (Ruling B27): for a site on a known multi-tenant platform, `write_templates` writes `own_domains = []` and the URL as an offsite prefix; `SiteConfig` allows an empty `own_domains` only beside an offsite prefix; discovery skips the platform's sitemaps; doctor warns.
- **Frozen limits in the template** (Ruling B25): `config.write_templates` writes each engine's planning limits into its `[engines.params]` (`config.TEMPLATE_ENGINES`, `config.template_engine_specs`), so a later planning.yaml change cannot change an existing project's engine configs; `to_engine_config` still fills a limit an engine leaves out.
- **Configuration checks**: engine params are typed (finding I3); `[keys]` entries must be environment variable names and a refused value is never echoed (finding F1); `config.config_sha_of` hashes the site, the engine config shas, the intents with their prompt ids, and the paraphrases and reps only (finding M7).
- **Shared-interfaces correction**: the status helper `metrics` exports is `metrics.effective_view_status`, as the list under "Shared interfaces" now says (it said `metrics.effective_status`); `schema.effective_status` returns the status with its reason, which the runner and the replay turn into `error_kind`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the plan A instrument core into the product: project configuration and a page library, a cost and power planner, a runner that enforces a budget cap, metrics with replay, a Markdown and HTML report, a before/after diff, and the seven CLI commands, all tested against synthetic stores and HTTP mocks with no live provider call.

**Architecture:** Same package, same rules (pure modules; network only in `library.py`, `runner.py`, `audit/probe.py` and the adapters' `call`). New modules: `config.py` (footnote.toml and intents.yaml), `planning.py` with `planning.yaml` (per-call limits and cost assumptions), `library.py` (sitemap, RSS, YouTube discovery), `design.py` and `plan.py` (call enumeration, MDE and cost), `runner.py` (manifest, budget reservation, write order, resume), `metrics.py` (funnel with replay), `report.py` with Jinja2 templates, `diff.py`, and a `commands/` package with one module per CLI command registered into `cli.py`. Two plan A modules change: `canon.py` moves to rule set version 2 with an owned set, and the adapters pass per-call limits through.

**Tech Stack:** Python 3.12, pydantic>=2.10, httpx, typer, pyyaml, jinja2, `tomllib` (stdlib) for reading TOML; tests with pytest, pytest-httpx, pytest-asyncio (auto mode), typer's CliRunner; ruff.

**Spec:** `docs/superpowers/specs/2026-10-05-footnoteone-design.md` (sections 3, 5, 7, 8, 9, 11) and `spec/METRICS.md` 0.2.0 (normative). Plan A as built: `docs/superpowers/plans/2026-10-05-footnoteone-instrument-core.md`, section "As built".

## Waves

Tasks in one wave touch disjoint files and run in parallel, each in its own worktree; the orchestrator merges a wave before the next starts.

- Wave 1: Task 1 (canon v2), Task 2 (schema additions, config, planning), Task 3 (adapter limits).
- Wave 2a: Task 4 (library), Task 5 (design and plan). Wave 2b: Task 6 (runner), Task 7 (metrics), Task 8 (crawl-check and doctor commands), which import wave 2a modules.
- Wave 3: Task 9 (report), Task 10 (diff), Task 11 (init, plan, run commands).
- Wave 4: Task 12 (CLI wiring, smoke tests, README, fixture recording script).

## Shared interfaces (plan A as built; consume these names exactly)

- `schema`: `Prompt(id, text, paraphrase_idx, lang)`, `Intent(id, label, kind, target_page_ids, prompts)`, `EngineConfig(provider, model_requested, tool_version, params, surface)` frozen with `.config_sha`, `SourceRef`, `Observation(activated, activation_evidence, answer_text, consulted, cited, model_requested, model_returned, input_tokens, output_tokens, search_calls, failed_searches, status)`, `Run(id, manifest_id, intent_id, prompt_id, engine_config_id, rep_idx, status, model_requested, model_returned, tool_version, activated, activation_evidence, raw_sha256|None, parser_version|None, input_tokens, output_tokens, search_calls, cost_usd, started_at, finished_at)`, `SourceRecord(run_id, role, url, canonical_url, rank, provider_field, title, char_start, char_end)`, `Manifest(id, code_version, adapter_versions, config_sha, price_table_version, budget_usd, spent_usd, runner, started_at, status)`, `Lenient` (extra ignored, no inf or nan), `utcnow()`, `sha256_of()`, `canonical_json()`, literals `RunStatus`, `Activation`, `IntentKind`, `Provider`.
- `store`: `JsonlStore(root).append(name, model)`, `.iter(name, model_cls)`, `.path(name)`; `RawStore(root).put(dict) -> sha`, `.get(sha)`, `.exists(sha)`. Files: `manifests.jsonl`, `runs.jsonl`, `sources.jsonl`, `raw/<sha>.json` under `.footnote/`.
- `canon` (before Task 1): `canonicalize(url)`, `host_of(url)`, `classify_owner(url, own_domains, offsite_prefixes)`.
- `stats`: `wilson(k, n)`, `cluster_bootstrap_mean(groups)`, `t_interval(values, alpha)`, `cluster_t_interval(groups, alpha)`, `paired_sign_flip_p(diffs)`, `holm(pvals)`, `mde(p, n_per_arm, m, icc)` (raises unless 0 < p < 1), `verdict(diff_lo, diff_hi, p_adj, n_intents, threshold=0.10, min_intents=6)`, `jaccard(a, b)`.
- `pricing`: `PriceTable.load()`, `.version`, `.tool_call_usd(provider)`, `.is_known(provider, model)`, `.token_usd(provider, model, input_tokens, output_tokens)`, `.providers[provider]["default_model"]`.
- `adapters.base`: `Adapter` protocol (`provider`, `version`, `build_request(prompt_text, engine)`, `async call(client, prompt_text, engine, api_key) -> RawResponse`, `parse(raw) -> Observation`, `price(obs, table) -> float`), `RawResponse(provider, model_requested, request, response, fetched_at)` with `.as_blob()`, `as_dicts`, `as_dict`, `as_str`, `as_count`. Classes `adapters.openai.OpenAIAdapter`, `adapters.anthropic.AnthropicAdapter`, `adapters.perplexity.PerplexityAdapter`.
- `audit.probe`: `async access_matrix(client, site_url, paths, probe=True) -> AccessReport(robots_status, robots_error, robots_access, rows: list[AccessRow(bot, purpose, path, robots_allowed, matched_rule, http_status, final_url, probe_error)])`; `audit.robots.load_bots() -> list[Bot(name, token, vendor, purpose, doc_url, note, crawls)]`.
- `footnoteone.__version__`.

New names defined in this plan and used across tasks: `canon.CANON_VERSION`, `canon.OwnedSet`, `canon.youtube_video_id`; `schema.Page`, `schema.run_key`, new `Manifest` fields; `config.ProjectConfig`, `config.load_config`, `config.load_intents`, `config.engine_configs`, `config.config_sha_of`, `config.write_templates`, `config.ConfigError`; `planning.PlanningAssumptions`, `planning.worst_case_usd`, `planning.typical_usd`; `library.discover_pages`, `library.write_pages`, `library.read_pages`; `design.Call`, `design.enumerate_calls`; `plan.make_plan`, `plan.PlanSummary`, `plan.render_plan_markdown`; `runner.run_design`, `runner.keys_from_env`, `runner.default_adapters`; `metrics.compute`, `metrics.Report`, `metrics.effective_view_status`, `metrics.select_manifests`; `report.write_report`; `diff.compute_diff`, `diff.render_diff_markdown`; `commands.<name>.register(app)`.

## Global Constraints

- Python `>=3.12`; runtime dependencies only `pydantic`, `httpx`, `typer`, `pyyaml`, `jinja2` and the standard library; dev dependencies `pytest`, `pytest-httpx`, `pytest-asyncio`, `ruff`. No task edits `pyproject.toml` or `uv.lock` (hatchling ships every file under `src/footnoteone`, YAML and templates included).
- `ruff check .` passes (E, F, I, B, UP; line length 110); tests are linted too. Code blocks in this plan may exceed 110 characters: wrap them when transcribing.
- No network in tests: HTTP is mocked with pytest-httpx (it asserts every registered response is requested); provider adapters are replaced by fakes in runner tests. No test reads environment API keys.
- Secrets come only from environment variables named in `footnote.toml`; they are never written to disk, logged, or placed in a RawResponse.
- Every stored or displayed metric value carries numerator, denominator, exclusions, number of intents and the interval method (`metrics.MetricValue`). Engines are never averaged together. `n == 0` yields None and renders as "no data", never 0.
- Parsing stays pure; the runner catches every exception from `parse` and `price` and records a status instead of crashing.
- No em dashes anywhere: code, comments, docstrings, strings, YAML, templates, Markdown, commit messages. Plain words in user-facing text; statistics sit behind a `<details>` in HTML and after the verdict line in Markdown.
- Commits are signed off (`git commit -s`), author identity is the repo's configured one, no assistant attribution lines. Work only in your task's worktree and branch; never commit on `main` or `feat/plan-b`.
- Run tests with `uv run pytest -q`; install with `uv sync --all-extras`.
- Do not edit `spec/METRICS.md` or the design spec; if the code cannot meet them, report it.

## Review Focus

1. A `footnote run` interrupted mid-burst (Ctrl-C, crash, or power loss) and rerun with the same label must not pay again for calls that completed, and must leave no half-written record that a later read mistakes for a run (write order raw, sources, run; resume by run key). Task 6.
2. A burst budget below the worst-case cost of one call must make no API call and say so per engine, and a burst that runs out part-way must record every remaining call as `budget_skip` rather than stopping silently. Task 6.
3. Intents edited between bursts: the diff compares only intents shared by both windows and names engines by config sha and surface, never by position in the config file. Task 10.
4. A sitemap listing 50,000 URLs, or URLs on other hosts, or a sitemap index that points to itself, must give a capped, deduplicated, own-host-only library and finish. Task 4.
5. An engine with zero ok runs, or a window with no activated run for an intent, renders "no data" and is excluded from denominators, never divided by zero or shown as 0%. Tasks 7, 9 and 10.

---

### Task 1: Canonicalization version 2 and the owned set

**Files:**
- Modify: `src/footnoteone/canon.py` (rewrite)
- Modify: `tests/test_canon.py` (update the `classify_owner` tests to `OwnedSet`; add the new rows)

**Interfaces:**
- Consumes: nothing.
- Produces: `CANON_VERSION = 2`; `canonicalize(url) -> str`; `host_of(url) -> str`; `youtube_video_id(url) -> str | None`; `OwnedSet` frozen dataclass with `domains: tuple[str, ...]`, `prefixes: tuple[str, ...]`, `urls: frozenset[str]`, `youtube_video_ids: frozenset[str]` and `OwnedSet.build(own_domains, offsite_prefixes, owned_urls=(), youtube_video_ids=()) -> OwnedSet`; `classify_owner(url, owned: OwnedSet) -> OwnerClass`.

Rule set 2 (every rule has a test row): scheme folded, http and https both key as `https`; host lowercased, trailing dot removed, every leading `www.` removed (loop), IPv6 brackets kept, default port dropped; percent-escapes decoded only for unreserved characters (`%7E` becomes `~`, `%2F` stays `%2F`, hex uppercased), raw non-ASCII percent-encoded; `/index.html` and `/index.htm` removed, trailing slash removed, `/amp` suffix removed (loop), root is `/`; fragment dropped; query HTML-unescaped (`&amp;` becomes `&`), tracking keys removed (global list plus per-host list), remaining pairs sorted; YouTube video URLs (`youtube.com/watch?v=ID`, `youtu.be/ID`, `youtube.com/shorts/ID`, `youtube.com/embed/ID`, `m.youtube.com`, `youtube-nocookie.com`) all become `https://youtube.com/watch?v=ID`; never raises.

- [ ] **Step 1: Replace the classify_owner tests and add rows**

In `tests/test_canon.py`, keep the existing never-raise tests, replace the two `classify_owner` tests with the `OwnedSet` tests below, and extend the parametrized table with the new rows (keep all existing rows; change the expected values of `("http://Example.com/a%7Eb", ...)` to `"https://example.com/a~b"` because the scheme now folds).

```python
from footnoteone.canon import CANON_VERSION, OwnedSet, canonicalize, classify_owner, host_of, youtube_video_id

NEW_ROWS = [
    ("http://example.com/x", "https://example.com/x"),
    ("https://example.com./x", "https://example.com/x"),
    ("https://www.www.example.com/x", "https://example.com/x"),
    ("https://example.com/a%2Fb", "https://example.com/a%2Fb"),
    ("https://example.com/a%7eb%2f", "https://example.com/a~b%2F"),
    ("https://example.com/index.html", "https://example.com/"),
    ("https://example.com/docs/index.htm", "https://example.com/docs"),
    ("https://example.com/p?a=1&amp;utm_source=x", "https://example.com/p?a=1"),
    ("https://example.com/p?gbraid=1&si=2&_hsenc=3&mkt_tok=4&b=1", "https://example.com/p?b=1"),
    ("https://medium.com/@ali/post-1?source=rss", "https://medium.com/@ali/post-1"),
    ("https://example.com/p?source=rss", "https://example.com/p?source=rss"),
    ("https://youtu.be/dQw4w9WgXcQ?t=42", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
]


@pytest.mark.parametrize("raw, expected", NEW_ROWS)
def test_canonicalize_v2_rows(raw, expected):
    assert canonicalize(raw) == expected


@pytest.mark.parametrize("raw, expected", NEW_ROWS)
def test_canonicalize_v2_is_idempotent(raw, expected):
    assert canonicalize(expected) == expected


def test_canon_version_is_two():
    assert CANON_VERSION == 2


def test_youtube_video_id():
    assert youtube_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube_video_id("https://youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube_video_id("https://youtube.com/@ali") is None
    assert youtube_video_id("https://example.com/watch?v=dQw4w9WgXcQ") is None


OWNED = OwnedSet.build(
    own_domains=["example.org", "notes.example.org"],
    offsite_prefixes=["https://medium.com/@Ali", "https://www.youtube.com/@alichannel", "https://ali.substack.com"],
    owned_urls=["https://www.facebook.com/profile.php?id=123"],
    youtube_video_ids=["dQw4w9WgXcQ"],
)


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://example.org/p", "own_site"),
        ("http://WWW.example.org/p", "own_site"),
        ("https://deep.notes.example.org/p", "own_site"),
        ("https://example.org./p", "own_site"),
        ("https://notexample.org/p", "other"),
        ("https://example.org.evil.com/p", "other"),
        ("http://medium.com/@ali/why-geo-fails", "own_offsite"),
        ("https://medium.com/@ALI?source=x", "own_offsite"),
        ("https://medium.com/@alice/post", "other"),
        ("https://youtube.com/@AliChannel/videos", "own_offsite"),
        ("https://youtu.be/dQw4w9WgXcQ", "own_offsite"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "own_offsite"),
        ("https://www.youtube.com/watch?v=otherVideo1", "other"),
        ("https://ali.substack.com/p/first", "own_offsite"),
        ("https://facebook.com/profile.php?id=123", "own_offsite"),
        ("https://facebook.com/profile.php?id=124", "other"),
        ("", "other"),
        ("https://[::1/x", "other"),
    ],
)
def test_classify_owner(url, expected):
    assert classify_owner(url, OWNED) == expected


def test_owned_set_build_normalizes_entries():
    owned = OwnedSet.build([" WWW.Example.org "], ["http://Medium.com/@Ali/"], ["http://Example.net/P/"], [])
    assert owned.domains == ("example.org",)
    assert owned.prefixes == ("https://medium.com/@ali",)
    assert owned.urls == frozenset({"https://example.net/P"})
    assert OwnedSet.build([""], [], [], []).domains == ()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_canon.py -q`
Expected: FAIL with ImportError on `CANON_VERSION` / `OwnedSet`.

- [ ] **Step 3: Rewrite canon.py**

```python
"""URL canonicalization and owner classification. Pure functions; never raise.

CANON_VERSION labels the rule set. Stored canonical_url values are a cache: metrics recompute them with the
current version (METRICS.md 0.2.0).
"""

from __future__ import annotations

import html
import re
import string
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

CANON_VERSION = 2
OwnerClass = Literal["own_site", "own_offsite", "other"]

TRACKING_PREFIXES = ("utm_",)
TRACKING_KEYS = frozenset(
    {"fbclid", "gclid", "dclid", "msclkid", "igshid", "mc_cid", "mc_eid", "ref_src", "srsltid", "amp", "gbraid",
     "wbraid", "yclid", "twclid", "ttclid", "_hsenc", "_hsmi", "mkt_tok", "si", "feature"}
)
HOST_TRACKING_KEYS = {"medium.com": frozenset({"source"})}
DEFAULT_PORTS = {"http": "80", "https": "443"}
INDEX_FILES = ("/index.html", "/index.htm")
YOUTUBE_HOSTS = frozenset({"youtube.com", "m.youtube.com", "youtu.be", "youtube-nocookie.com"})
_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _canonical_escape(match: re.Match[str]) -> str:
    char = chr(int(match.group(1), 16))
    return char if char in _UNRESERVED else "%" + match.group(1).upper()


def _clean_path(path: str) -> str:
    path = quote(path, safe="/~:@!$&'()*+,;=-._%")  # keep existing escapes; encode raw non-ASCII and spaces
    path = re.sub(r"%([0-9A-Fa-f]{2})", _canonical_escape, path)  # decode unreserved escapes only
    for index in INDEX_FILES:
        if path.endswith(index):
            path = path[: -len(index)]
    path = path.rstrip("/")
    while path.endswith("/amp"):
        path = path[: -len("/amp")].rstrip("/")
    return path or "/"


def _clean_host(hostname: str | None) -> str:
    host = (hostname or "").lower().rstrip(".")
    while host.startswith("www."):
        host = host[4:]
    if ":" in host:
        host = f"[{host}]"  # .hostname drops the brackets around an IPv6 literal
    return host


def _video_id(host: str, path: str, query: list[tuple[str, str]]) -> str | None:
    if host not in YOUTUBE_HOSTS:
        return None
    candidate = None
    if host == "youtu.be":
        candidate = path.strip("/").split("/")[0]
    elif path == "/watch":
        candidate = next((v for k, v in query if k == "v"), None)
    else:
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
            candidate = parts[1]
    return candidate if candidate and _VIDEO_ID.match(candidate) else None


def youtube_video_id(url: str) -> str | None:
    """The 11-character video id of a YouTube video URL in any of its forms, else None. Never raises."""
    try:
        parts = urlsplit(url.strip())
        return _video_id(_clean_host(parts.hostname), parts.path, parse_qsl(html.unescape(parts.query)))
    except ValueError:
        return None


def _canonicalize_strict(url: str) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme == "http":
        scheme = "https"  # one key per page whatever the scheme the engine returned
    host = _clean_host(parts.hostname)
    query_pairs = parse_qsl(html.unescape(parts.query), keep_blank_values=True)
    video = _video_id(host, parts.path, query_pairs)
    if video:
        return f"https://youtube.com/watch?v={video}"
    try:
        number = parts.port
    except ValueError:
        number = None
    port = f":{number}" if number and str(number) != DEFAULT_PORTS.get(scheme) else ""
    drop = TRACKING_KEYS | HOST_TRACKING_KEYS.get(host, frozenset())
    query = sorted(
        (k, v) for k, v in query_pairs if not k.lower().startswith(TRACKING_PREFIXES) and k.lower() not in drop
    )
    return urlunsplit((scheme, host + port, _clean_path(parts.path), urlencode(query), ""))


def canonicalize(url: str) -> str:
    """Canonical form of url under rule set CANON_VERSION. Never raises: an unparseable URL comes back
    stripped but unchanged."""
    url = url.strip()
    try:
        return _canonicalize_strict(url)
    except ValueError:
        return url


def host_of(url: str) -> str:
    """Lowercase host without leading www or trailing dot. Never raises: an unparseable URL has host ""."""
    try:
        return _clean_host(urlsplit(url.strip()).hostname).strip("[]")
    except ValueError:
        return ""


def _prefix_key(prefix: str) -> str:
    return canonicalize(prefix).split("?", 1)[0].rstrip("/").lower()


@dataclass(frozen=True)
class OwnedSet:
    """Everything that counts as the creator's: own domains (with subdomains), off-site profile prefixes
    (compared without scheme and path case), exact off-site page URLs, and owned YouTube video ids."""

    domains: tuple[str, ...]
    prefixes: tuple[str, ...]
    urls: frozenset[str]
    youtube_video_ids: frozenset[str]

    @classmethod
    def build(
        cls,
        own_domains: list[str] | tuple[str, ...],
        offsite_prefixes: list[str] | tuple[str, ...],
        owned_urls: list[str] | tuple[str, ...] = (),
        youtube_video_ids: list[str] | tuple[str, ...] = (),
    ) -> OwnedSet:
        domains = tuple(dict.fromkeys(h for h in (host_of("https://" + d.strip().lower().removeprefix("https://").removeprefix("http://")) for d in own_domains) if h))
        prefixes = tuple(dict.fromkeys(_prefix_key(p) for p in offsite_prefixes if p.strip()))
        urls = frozenset(canonicalize(u) for u in owned_urls if u.strip())
        ids = frozenset(v.strip() for v in youtube_video_ids if _VIDEO_ID.match(v.strip()))
        return cls(domains, prefixes, urls, ids)


def classify_owner(url: str, owned: OwnedSet) -> OwnerClass:
    """own_site by domain or subdomain; own_offsite by exact URL, owned video id or profile prefix; else other."""
    canon = canonicalize(url)
    host = host_of(canon)
    if host and any(host == d or host.endswith("." + d) for d in owned.domains):
        return "own_site"
    if canon in owned.urls:
        return "own_offsite"
    video = youtube_video_id(canon)
    if video and video in owned.youtube_video_ids:
        return "own_offsite"
    base = canon.split("?", 1)[0].rstrip("/").lower()
    if any(base == p or base.startswith(p + "/") for p in owned.prefixes):
        return "own_offsite"
    return "other"
```

Note on `OwnedSet.build` domains: the long expression normalizes entries like `" WWW.Example.org "` and `"https://example.org"` to `example.org`; split it across lines when transcribing. The `feature` key joins the tracking list because YouTube appends `feature=share`.

- [ ] **Step 4: Run the canon tests, then the full suite**

Run: `uv run pytest tests/test_canon.py -q && uv run pytest -q && uv run ruff check .`
Expected: all pass. If any other test file still calls the old `classify_owner` signature, update it (no plan A test outside test_canon.py does).

- [ ] **Step 5: Commit**

```bash
git add src/footnoteone/canon.py tests/test_canon.py
git commit -s -m "feat: canonicalization rule set 2 with scheme folding, YouTube video keys and an owned set"
```

---

### Task 2: Schema additions, project configuration and planning assumptions

**Files:**
- Modify: `src/footnoteone/schema.py` (add `Page`, `PageSource`, `run_key`; extend `Manifest`)
- Create: `src/footnoteone/config.py`, `src/footnoteone/planning.py`, `src/footnoteone/planning.yaml`
- Test: `tests/test_schema.py` (append), `tests/test_config.py`, `tests/test_planning.py`

**Interfaces:**
- Consumes: `schema`, `pricing.PriceTable`, `canon.host_of` (plan A signature; Task 1 keeps it).
- Produces: `schema.PageSource`, `schema.Page(id, url, canonical_url, title, source, lastmod, discovered_at)` with `Page.id_for(canonical_url)`, `schema.run_key(label, intent_id, prompt_id, engine_config_id, rep_idx) -> str`, `schema.effective_status(status, search_calls, failed_searches) -> tuple[RunStatus, str | None]` (METRICS 0.2.0: a run whose every search failed is an error with reason "all searches failed"; otherwise the status and None); `Manifest` gains `label: str = ""`, `engines: list[EngineConfig]`, `intents: list[Intent]`, `canon_version: int = 0`, `planning_version: str = ""`, `paraphrases: int = 0`, `reps: int = 0`, `finished_at: AwareDatetime | None = None`, `note: str = ""`; `config.ConfigError`, `config.SiteConfig`, `config.EngineSpec`, `config.DesignConfig`, `config.KeysConfig`, `config.ProjectConfig`, `config.IntentSpec`, `config.IntentsFile`, `config.load_config(root) -> ProjectConfig`, `config.load_intents(root) -> list[Intent]`, `config.engine_configs(config, assumptions) -> list[EngineConfig]`, `config.config_sha_of(config, intents, engines) -> str`, `config.write_templates(root, site_url, force=False) -> list[Path]`, `config.ALLOWED_PARAMS`; `planning.ProviderAssumptions`, `planning.PlanningAssumptions.load()`, `planning.max_searches_for(engine, a)`, `planning.max_output_for(engine, a)`, `planning.typical_usd(engine, table, a)`, `planning.worst_case_usd(engine, table, a)`.

- [ ] **Step 1: Schema tests (append to tests/test_schema.py)**

```python
from footnoteone.schema import Manifest, Page, run_key


def test_page_id_is_sixteen_hex_of_canonical_url():
    page = Page(id=Page.id_for("https://example.org/a"), url="https://example.org/a/", canonical_url="https://example.org/a", source="sitemap")
    assert len(page.id) == 16 and page.id == Page.id_for("https://example.org/a")
    assert page.discovered_at.tzinfo is not None


def test_run_key_is_deterministic_and_label_scoped():
    a = run_key("2026-10-07", "i1", "p1", "e" * 64, 0)
    assert a == run_key("2026-10-07", "i1", "p1", "e" * 64, 0) and len(a) == 32
    assert a != run_key("2026-10-14", "i1", "p1", "e" * 64, 0)


def test_effective_status_all_searches_failed_is_error():
    from footnoteone.schema import effective_status

    assert effective_status("ok", 2, 2) == ("error", "all searches failed")
    assert effective_status("ok", 2, 1) == ("ok", None)
    assert effective_status("ok", 0, 0) == ("ok", None)
    assert effective_status("truncated", 1, 1) == ("error", "all searches failed")


def test_manifest_new_fields_default_and_round_trip():
    m = Manifest(code_version="0.0.1", config_sha="c" * 64, price_table_version="v", budget_usd=5.0)
    assert m.label == "" and m.engines == [] and m.intents == [] and m.finished_at is None
    assert Manifest.model_validate_json(m.model_dump_json()) == m
```

- [ ] **Step 2: Schema additions**

```python
PageSource = Literal["sitemap", "rss", "youtube", "manual"]


def run_key(label: str, intent_id: str, prompt_id: str, engine_config_id: str, rep_idx: int) -> str:
    """Deterministic id of one planned call within a burst label; the runner uses it as Run.id and for resume."""
    return sha256_of([label, intent_id, prompt_id, engine_config_id, rep_idx])[:32]


def effective_status(status: RunStatus, search_calls: int, failed_searches: int) -> tuple[RunStatus, str | None]:
    """METRICS 0.2.0 scope rule: a run whose every search failed is an error ("all searches failed"); any other
    run keeps its status. Returns the effective status and the reason, or None."""
    if search_calls > 0 and failed_searches >= search_calls:
        return "error", "all searches failed"
    return status, None


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
```

Add to `Manifest` after `status`: `label: str = ""`, `engines: list[EngineConfig] = Field(default_factory=list)`, `intents: list[Intent] = Field(default_factory=list)`, `canon_version: int = 0`, `planning_version: str = ""`, `paraphrases: int = 0`, `reps: int = 0`, `finished_at: AwareDatetime | None = None`, `note: str = ""`.

- [ ] **Step 3: planning.yaml and planning tests**

```yaml
# src/footnoteone/planning.yaml
# Per-call limits the runner sends and the cost assumptions the planner uses. `limits` are engine params
# merged under the user's own (so they enter config_sha); the rest are planning assumptions until recorded
# responses replace them. Typical figures estimate a burst; the max figures bound one call for the budget
# reservation (tool fee x max_searches + token price x (max_input_tokens + max output)).
version: "2026-10-05"
providers:
  openai:
    limits: {max_output_tokens: 1200, max_tool_calls: 3}
    typical_input_tokens: 2500
    typical_output_tokens: 600
    typical_searches: 1.5
    max_input_tokens: 8000
    max_searches: 3
  anthropic:
    limits: {max_tokens: 1200, max_uses: 3}
    typical_input_tokens: 3000
    typical_output_tokens: 600
    typical_searches: 1.5
    max_input_tokens: 10000
    max_searches: 3
  perplexity:
    limits: {max_output_tokens: 1200}
    typical_input_tokens: 2000
    typical_output_tokens: 600
    typical_searches: 1.5
    max_input_tokens: 8000
    max_searches: 3
```

```python
# tests/test_planning.py
import pytest

from footnoteone.planning import PlanningAssumptions, max_output_for, max_searches_for, typical_usd, worst_case_usd
from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig


def test_assumptions_load_with_limits_per_provider():
    a = PlanningAssumptions.load()
    assert a.version == "2026-10-05"
    assert a.providers["openai"].limits == {"max_output_tokens": 1200, "max_tool_calls": 3}
    assert a.providers["anthropic"].limits["max_uses"] == 3


def test_limits_can_be_overridden_by_engine_params():
    a = PlanningAssumptions.load()
    e = EngineConfig(provider="anthropic", model_requested="claude-sonnet-4-5", params={"max_uses": 1, "max_tokens": 400})
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
```

- [ ] **Step 4: planning.py**

```python
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
    return _first_int(engine.params, SEARCH_LIMIT_KEYS) or _first_int(p.limits, SEARCH_LIMIT_KEYS) or p.max_searches


def max_output_for(engine: EngineConfig, a: PlanningAssumptions) -> int:
    p = a.providers[engine.provider]
    return _first_int(engine.params, OUTPUT_LIMIT_KEYS) or _first_int(p.limits, OUTPUT_LIMIT_KEYS) or p.typical_output_tokens


def typical_usd(engine: EngineConfig, table: PriceTable, a: PlanningAssumptions) -> float:
    """Expected cost of one call: typical searches x tool fee + typical tokens at the model's rows."""
    p = a.providers[engine.provider]
    tokens = table.token_usd(engine.provider, engine.model_requested, p.typical_input_tokens, p.typical_output_tokens)
    return p.typical_searches * table.tool_call_usd(engine.provider) + tokens


def worst_case_usd(engine: EngineConfig, table: PriceTable, a: PlanningAssumptions) -> float:
    """Upper bound the runner reserves before a call: max searches x tool fee + max input and output tokens."""
    p = a.providers[engine.provider]
    tokens = table.token_usd(engine.provider, engine.model_requested, p.max_input_tokens, max_output_for(engine, a))
    return max_searches_for(engine, a) * table.tool_call_usd(engine.provider) + tokens
```

- [ ] **Step 5: Config tests**

```python
# tests/test_config.py
import pytest

from footnoteone.config import (
    ALLOWED_PARAMS, ConfigError, EngineSpec, config_sha_of, engine_configs, load_config, load_intents, write_templates,
)
from footnoteone.planning import PlanningAssumptions

TOML = """
[site]
url = "https://example.org"
offsite_prefixes = ["https://medium.com/@ali"]
youtube_channels = ["UC1234567890123456789012"]

[[engines]]
provider = "openai"
model = "gpt-5-mini"
[engines.params]
force_search = true

[[engines]]
provider = "anthropic"
model = "claude-sonnet-4-5"

[design]
paraphrases = 3
reps = 2
budget_usd_per_burst = 12.5
"""

INTENTS = """
version: 1
intents:
  - id: attribution-patching
    label: attribution patching vs activation patching
    prompts: ["What is attribution patching?", "Explain attribution patching"]
  - id: placebo-water
    label: placebo
    kind: placebo
    prompts: ["Boiling point of water at sea level?"]
"""


def write(root, toml=TOML, intents=INTENTS):
    (root / "footnote.toml").write_text(toml)
    (root / "intents.yaml").write_text(intents)


def test_load_config_derives_own_domain_and_defaults(tmp_path):
    write(tmp_path)
    cfg = load_config(tmp_path)
    assert cfg.site.own_domains == ["example.org"]
    assert cfg.design.icc == 0.3 and cfg.design.min_shared_intents == 8 and cfg.design.reps == 2
    assert cfg.keys.env_name("openai") == "OPENAI_API_KEY"


def test_engine_configs_merge_planning_limits_under_user_params():
    cfg_engines = [EngineSpec(provider="openai", model="gpt-5-mini", params={"force_search": True, "max_tool_calls": 1})]
    [e] = engine_configs_for(cfg_engines)
    assert e.params == {"max_output_tokens": 1200, "max_tool_calls": 1, "force_search": True}
    assert e.surface == "api:openai"


def engine_configs_for(specs):
    a = PlanningAssumptions.load()
    return [s.to_engine_config(a) for s in specs]


@pytest.mark.parametrize(
    "toml, message",
    [
        (TOML.replace('url = "https://example.org"', 'url = "example.org"'), "scheme"),
        (TOML.replace('force_search = true', 'temperature = 0.2'), "temperature"),
        (TOML.replace('budget_usd_per_burst = 12.5', 'budget_usd_per_burst = inf'), "finite"),
        (TOML.replace('budget_usd_per_burst = 12.5', 'budget_usd_per_burst = 0'), "budget"),
        (TOML.replace('[design]', '[design]\nunknown = 1'), "unknown"),
        (TOML + '\n[[engines]]\nprovider = "anthropic"\nmodel = "x"\n[engines.params]\nallowed_domains = ["a"]\nblocked_domains = ["b"]\n', "not both"),
        (TOML + '\n[[engines]]\nprovider = "openai"\nmodel = "gpt-5-mini"\n[engines.params]\nforce_search = true\n', "duplicate"),
    ],
)
def test_load_config_rejects(tmp_path, toml, message):
    write(tmp_path, toml=toml)
    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path)


def test_missing_files_raise_config_error(tmp_path):
    with pytest.raises(ConfigError, match="footnote.toml"):
        load_config(tmp_path)


def test_load_intents_gives_stable_ids(tmp_path):
    write(tmp_path)
    intents = load_intents(tmp_path)
    again = load_intents(tmp_path)
    assert [i.id for i in intents] == ["attribution-patching", "placebo-water"]
    assert intents[0].prompts[1].paraphrase_idx == 1 and len(intents[0].prompts[1].id) == 16
    assert [p.id for p in intents[0].prompts] == [p.id for p in again[0].prompts]
    assert intents[1].kind == "placebo"


@pytest.mark.parametrize(
    "yaml_text, message",
    [
        (INTENTS.replace("placebo-water", "attribution-patching"), "duplicate"),
        (INTENTS.replace("id: placebo-water", "id: Placebo Water"), "slug"),
        (INTENTS.replace('prompts: ["Boiling point of water at sea level?"]', "prompts: []"), "prompt"),
        (INTENTS.replace("kind: placebo", "kind: control"), "kind"),
    ],
)
def test_load_intents_rejects(tmp_path, yaml_text, message):
    write(tmp_path, intents=yaml_text)
    with pytest.raises(ConfigError, match=message):
        load_intents(tmp_path)


def test_config_sha_changes_with_intents_and_engines(tmp_path):
    write(tmp_path)
    cfg, intents = load_config(tmp_path), load_intents(tmp_path)
    engines = engine_configs(cfg, PlanningAssumptions.load())
    sha = config_sha_of(cfg, intents, engines)
    assert sha == config_sha_of(cfg, intents, engines) and len(sha) == 64
    assert sha != config_sha_of(cfg, intents[:1], engines)


def test_write_templates_round_trip(tmp_path):
    paths = write_templates(tmp_path, "https://example.org")
    assert [p.name for p in paths] == ["footnote.toml", "intents.yaml"]
    cfg = load_config(tmp_path)
    assert cfg.site.url == "https://example.org" and len(cfg.engines) == 3
    assert any(i.kind == "placebo" for i in load_intents(tmp_path))
    with pytest.raises(ConfigError, match="exists"):
        write_templates(tmp_path, "https://example.org")
    assert "OPENAI_API_KEY" in (tmp_path / "footnote.toml").read_text()
    assert "sk-" not in (tmp_path / "footnote.toml").read_text()


def test_allowed_params_cover_each_provider():
    assert {"force_search", "user_location", "max_output_tokens", "max_tool_calls"} <= ALLOWED_PARAMS["openai"]
    assert {"max_uses", "allowed_domains", "blocked_domains", "user_location", "max_tokens"} <= ALLOWED_PARAMS["anthropic"]
    assert {"search_domain_filter", "search_recency_filter", "user_location", "max_output_tokens"} <= ALLOWED_PARAMS["perplexity"]
```

- [ ] **Step 6: config.py**

```python
"""Project configuration: footnote.toml and intents.yaml, validated, with stable ids.

Keys never live here: the config names the environment variables that hold them.
"""

from __future__ import annotations

import math
import re
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from footnoteone.canon import host_of
from footnoteone.planning import PlanningAssumptions
from footnoteone.schema import EngineConfig, Intent, IntentKind, Prompt, Provider, sha256_of

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
ALLOWED_PARAMS: dict[str, frozenset[str]] = {
    "openai": frozenset({"force_search", "user_location", "max_output_tokens", "max_tool_calls"}),
    "anthropic": frozenset({"max_uses", "allowed_domains", "blocked_domains", "user_location", "max_tokens"}),
    "perplexity": frozenset({"search_domain_filter", "search_recency_filter", "user_location", "max_output_tokens"}),
}


class ConfigError(ValueError):
    """A readable configuration problem: file, field and what to change."""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def _http_url(value: str, what: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"{what} must be a full http or https URL with a host, got {value!r} (missing scheme?)")
    return value.strip()


class SiteConfig(Strict):
    url: str
    name: str = ""
    own_domains: list[str] = Field(default_factory=list)
    offsite_prefixes: list[str] = Field(default_factory=list)
    youtube_channels: list[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _http_url(v, "site.url")

    @field_validator("offsite_prefixes")
    @classmethod
    def _prefixes(cls, v: list[str]) -> list[str]:
        return [_http_url(p, "site.offsite_prefixes entry") for p in v]

    @model_validator(mode="after")
    def _domains(self) -> SiteConfig:
        cleaned = []
        for d in self.own_domains:
            d = d.strip().lower()
            if not d:
                raise ValueError("site.own_domains has an empty entry")
            if "://" in d or "/" in d:
                raise ValueError(f"site.own_domains entries are bare hosts, not URLs: {d!r}")
            cleaned.append(d.removeprefix("www."))
        self.own_domains = cleaned or [host_of(self.url)]
        return self


class EngineSpec(Strict):
    provider: Provider
    model: str = Field(min_length=1)
    tool_version: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _params(self) -> EngineSpec:
        unknown = set(self.params) - ALLOWED_PARAMS[self.provider]
        if unknown:
            raise ValueError(f"engine {self.provider}/{self.model}: unknown params {sorted(unknown)}; allowed: {sorted(ALLOWED_PARAMS[self.provider])}")
        if {"allowed_domains", "blocked_domains"} <= set(self.params):
            raise ValueError("anthropic takes allowed_domains or blocked_domains, not both")
        for key, value in self.params.items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"param {key} must be finite")
        return self

    def to_engine_config(self, assumptions: PlanningAssumptions) -> EngineConfig:
        """Planning limits merged under the user's params so they enter config_sha."""
        params = {**assumptions.providers[self.provider].limits, **self.params}
        return EngineConfig(provider=self.provider, model_requested=self.model, tool_version=self.tool_version, params=params)


class DesignConfig(Strict):
    paraphrases: int = Field(default=3, ge=1, le=10)
    reps: int = Field(default=2, ge=1, le=10)
    budget_usd_per_burst: float = Field(gt=0)
    baseline_cited_rate: float = Field(default=0.2, ge=0.0, le=1.0)
    icc: float = Field(default=0.3, ge=0.0, le=1.0)
    min_shared_intents: int = Field(default=8, ge=8)
    call_timeout_s: float = Field(default=120.0, gt=0)
    politeness_s: float = Field(default=0.0, ge=0)


class KeysConfig(Strict):
    openai: str = "OPENAI_API_KEY"
    anthropic: str = "ANTHROPIC_API_KEY"
    perplexity: str = "PERPLEXITY_API_KEY"

    def env_name(self, provider: str) -> str:
        return getattr(self, provider)


class ProjectConfig(Strict):
    site: SiteConfig
    engines: list[EngineSpec] = Field(min_length=1)
    design: DesignConfig
    keys: KeysConfig = Field(default_factory=KeysConfig)

    @model_validator(mode="after")
    def _unique_engines(self) -> ProjectConfig:
        seen: set[str] = set()
        for spec in self.engines:
            key = sha256_of([spec.provider, spec.model, spec.tool_version, spec.params])
            if key in seen:
                raise ValueError(f"duplicate engine {spec.provider}/{spec.model} with the same params")
            seen.add(key)
        return self


class IntentSpec(Strict):
    id: str
    label: str = Field(min_length=1)
    kind: IntentKind = "unbranded"
    target_pages: list[str] = Field(default_factory=list)
    prompts: list[str] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG.match(v):
            raise ValueError(f"intent id {v!r} must be a slug: lowercase letters, digits and hyphens, 2 to 64 characters")
        return v

    @field_validator("prompts")
    @classmethod
    def _prompts(cls, v: list[str]) -> list[str]:
        if any(not p.strip() for p in v):
            raise ValueError("every prompt must be non-empty text")
        return [p.strip() for p in v]


class IntentsFile(Strict):
    version: int = 1
    intents: list[IntentSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> IntentsFile:
        ids = [i.id for i in self.intents]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate intent ids: {dupes}")
        return self


def _errors(exc: ValidationError, file: str) -> ConfigError:
    lines = [f"{file}: " + ".".join(str(x) for x in e["loc"]) + f": {e['msg']}" for e in exc.errors()]
    return ConfigError("\n".join(lines))


def load_config(root: Path) -> ProjectConfig:
    path = Path(root) / "footnote.toml"
    if not path.exists():
        raise ConfigError(f"{path} not found; run `footnote init <site url>` first")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: not valid TOML: {exc}") from exc
    try:
        return ProjectConfig.model_validate(data)
    except ValidationError as exc:
        raise _errors(exc, str(path)) from exc


def prompt_id(intent_id: str, paraphrase_idx: int, text: str) -> str:
    return sha256_of([intent_id, paraphrase_idx, text])[:16]


def load_intents(root: Path) -> list[Intent]:
    path = Path(root) / "intents.yaml"
    if not path.exists():
        raise ConfigError(f"{path} not found; run `footnote init <site url>` first")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML: {exc}") from exc
    try:
        parsed = IntentsFile.model_validate(data)
    except ValidationError as exc:
        raise _errors(exc, str(path)) from exc
    return [
        Intent(
            id=spec.id,
            label=spec.label,
            kind=spec.kind,
            target_page_ids=list(spec.target_pages),
            prompts=[Prompt(id=prompt_id(spec.id, i, text), text=text, paraphrase_idx=i) for i, text in enumerate(spec.prompts)],
        )
        for spec in parsed.intents
    ]


def engine_configs(config: ProjectConfig, assumptions: PlanningAssumptions) -> list[EngineConfig]:
    return [spec.to_engine_config(assumptions) for spec in config.engines]


def config_sha_of(config: ProjectConfig, intents: list[Intent], engines: list[EngineConfig]) -> str:
    """Identity of a design: site, design numbers, engine config shas and intent plus prompt ids."""
    return sha256_of(
        {
            "site": config.site.model_dump(),
            "design": config.design.model_dump(),
            "engines": [e.config_sha for e in engines],
            "intents": [[i.id, i.kind, [p.id for p in i.prompts]] for i in intents],
        }
    )


TOML_TEMPLATE = """# footnote.toml: FootnoteOne project configuration. API keys live in environment variables, never here.

[site]
url = "{site_url}"
name = ""
own_domains = ["{host}"]            # subdomains count as yours
offsite_prefixes = []               # for example "https://medium.com/@you", "https://you.substack.com"
youtube_channels = []               # channel URLs or ids; videos are discovered through the channel feed

[[engines]]
provider = "openai"
model = "gpt-5-mini"
[engines.params]
force_search = true

[[engines]]
provider = "anthropic"
model = "claude-sonnet-4-5"

[[engines]]
provider = "perplexity"
model = "fast"

[design]
paraphrases = 3                     # wordings per intent
reps = 2                            # repeats per wording per burst
budget_usd_per_burst = 20.0         # hard cap; `footnote plan` shows the worst case before you spend
baseline_cited_rate = 0.2           # planning prior until you have data
icc = 0.3                           # answers to one intent are correlated; measured later
min_shared_intents = 8              # no verdict below this (METRICS 0.2.0)

[keys]
openai = "OPENAI_API_KEY"
anthropic = "ANTHROPIC_API_KEY"
perplexity = "PERPLEXITY_API_KEY"
"""

INTENTS_TEMPLATE = """# intents.yaml: what readers ask that your pages answer. Ids are permanent; edit wording, not ids.
version: 1
intents:
  - id: example-topic
    label: "A question your page answers"
    kind: unbranded                 # unbranded | branded | placebo
    target_pages: []                # page ids from .footnote/pages.jsonl, or URLs
    prompts:
      - "A question a reader would type, first wording"
      - "The same question, second wording"
      - "The same question, third wording"
  - id: placebo-water
    label: "Placebo: a question your pages cannot answer"
    kind: placebo
    prompts:
      - "What is the boiling point of water at sea level?"
"""


def write_templates(root: Path, site_url: str, force: bool = False) -> list[Path]:
    """Write footnote.toml and intents.yaml for a new project; ConfigError if either exists and not force."""
    site_url = _http_url(site_url, "site url")
    root = Path(root)
    targets = {"footnote.toml": TOML_TEMPLATE.format(site_url=site_url, host=host_of(site_url)), "intents.yaml": INTENTS_TEMPLATE}
    existing = [name for name in targets if (root / name).exists()]
    if existing and not force:
        raise ConfigError(f"{', '.join(existing)} already exists in {root}; pass --force to overwrite")
    written = []
    for name, text in targets.items():
        (root / name).write_text(text, encoding="utf-8")
        written.append(root / name)
    return written
```

- [ ] **Step 7: Run all three test files, then the suite**

Run: `uv run pytest tests/test_schema.py tests/test_planning.py tests/test_config.py -q && uv run pytest -q && uv run ruff check .`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/footnoteone/schema.py src/footnoteone/config.py src/footnoteone/planning.py src/footnoteone/planning.yaml tests/test_schema.py tests/test_config.py tests/test_planning.py
git commit -s -m "feat: project configuration with stable ids, planning assumptions, Page and Manifest records"
```

---

### Task 3: Adapters pass per-call limits through

**Files:**
- Modify: `src/footnoteone/adapters/openai.py` (`build_request`), `src/footnoteone/adapters/perplexity.py` (`build_request`)
- Test: `tests/test_adapter_openai.py`, `tests/test_adapter_perplexity.py` (append)

**Interfaces:**
- Consumes: `EngineConfig.params` keys from `config.ALLOWED_PARAMS` (Task 2 defines them; this task does not import config).
- Produces: OpenAI requests carry `max_output_tokens` and `max_tool_calls` when present in params; Perplexity requests carry the documented output-token limit when `max_output_tokens` is in params. Anthropic already sends `max_tokens` and `max_uses` (no change).

Before writing the Perplexity test, read the Agent API reference (docs.perplexity.ai/api-reference/agent-post) with WebFetch and confirm the request field that caps output tokens; if it is not `max_output_tokens`, map params `max_output_tokens` to the documented field name and say so in the docstring and report. Do not call any API.

- [ ] **Step 1: Tests**

```python
# append to tests/test_adapter_openai.py
def test_build_request_passes_per_call_limits():
    engine = EngineConfig(provider="openai", model_requested="gpt-5-mini", params={"max_output_tokens": 1200, "max_tool_calls": 3})
    req = OpenAIAdapter().build_request("q", engine)
    assert req["max_output_tokens"] == 1200 and req["max_tool_calls"] == 3
    assert "max_output_tokens" not in OpenAIAdapter().build_request("q", EngineConfig(provider="openai", model_requested="gpt-5-mini"))


# append to tests/test_adapter_perplexity.py
def test_build_request_passes_output_limit():
    engine = EngineConfig(provider="perplexity", model_requested="fast", params={"max_output_tokens": 1200})
    req = PerplexityAdapter().build_request("q", engine)
    assert req[PerplexityAdapter.OUTPUT_LIMIT_FIELD] == 1200
    assert PerplexityAdapter.OUTPUT_LIMIT_FIELD not in PerplexityAdapter().build_request("q", EngineConfig(provider="perplexity", model_requested="fast"))
```

- [ ] **Step 2: Run to verify they fail, then implement**

OpenAI: after the `include` line, `for key in ("max_output_tokens", "max_tool_calls"): if key in engine.params: req[key] = int(engine.params[key])`. Perplexity: class attribute `OUTPUT_LIMIT_FIELD = "max_output_tokens"` (or the documented name) and the same one-line pass-through on the request dict. Update each parse docstring's request note.

- [ ] **Step 3: Run both adapter test files, the suite, ruff; commit**

```bash
git add src/footnoteone/adapters/openai.py src/footnoteone/adapters/perplexity.py tests/test_adapter_openai.py tests/test_adapter_perplexity.py
git commit -s -m "feat: adapters pass per-call output and search limits through to requests"
```

---

### Task 4: Page library discovery

**Files:**
- Create: `src/footnoteone/library.py`
- Test: `tests/test_library.py`

**Interfaces:**
- Consumes: `schema.Page`, `canon.canonicalize`, `canon.host_of`, `canon.youtube_video_id`, `canon.OwnedSet`, `config.SiteConfig`, `config.ProjectConfig`.
- Produces: `Found(url, title, lastmod, source)` dataclass; `sitemaps_from_robots(text) -> list[str]`; `parse_sitemap(data: bytes) -> tuple[list[Found], list[str]]`; `parse_feed(data: bytes) -> list[Found]`; `feed_links_from_html(html_text, base_url) -> list[str]`; `youtube_channel_id(ref) -> str | None`; `parse_youtube_feed(data) -> list[Found]`; `async fetch_bytes(client, url, max_bytes=10_000_000) -> bytes | None`; `async discover_pages(client, site, limit=500, clock=utcnow) -> list[Page]`; `write_pages(root, pages) -> Path`; `read_pages(root) -> list[Page]`; `owned_set_for(config, pages) -> OwnedSet`.

Rules: pages from sitemaps and feeds are kept only when their host is one of `site.own_domains` or a subdomain; YouTube video pages come from the channel feed (`https://www.youtube.com/feeds/videos.xml?channel_id=<id>`); a channel reference that is not an id (`UC` plus 22 characters) or a `/channel/<id>` URL is resolved by fetching the channel page and matching `"channelId":"(UC[\w-]{22})"`; sitemap discovery starts from robots.txt `Sitemap:` lines (case-insensitive key) plus `/sitemap.xml` and `/sitemap_index.xml`; a sitemap index is followed breadth-first with a visited set and at most 50 sitemap files; gzip bodies (magic `1f 8b`) are decompressed; any fetch over 10 MB, non-2xx or failing is skipped; feeds come from `<link rel="alternate" type="application/rss+xml|application/atom+xml" href=...>` on the home page, else `/feed`, `/rss.xml`, `/atom.xml`, `/feed.xml`; pages are deduplicated by canonical URL (sitemap first, then feeds, then YouTube) and capped at `limit`; a sitemap with no `<loc>` elements is not an error. `write_pages` rewrites `.footnote/pages.jsonl` atomically (temp file in the same directory, then `os.replace`); it is derived data, so rewriting is allowed. `owned_set_for` builds `OwnedSet` from `config.site.own_domains`, `config.site.offsite_prefixes`, the canonical URLs of pages whose host is not an own domain (manual or feed-found off-site posts), and the video ids of YouTube pages.

- [ ] **Step 1: Tests**

```python
# tests/test_library.py
import gzip

import httpx
import pytest

from footnoteone.config import ProjectConfig
from footnoteone.library import (
    discover_pages, feed_links_from_html, owned_set_for, parse_feed, parse_sitemap, parse_youtube_feed, read_pages,
    sitemaps_from_robots, write_pages, youtube_channel_id,
)
from footnoteone.schema import Page

SM = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{}</urlset>'
IDX = '<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{}</sitemapindex>'


def loc(url, lastmod=None):
    extra = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
    return f"<url><loc>{url}</loc>{extra}</url>"


def site(**overrides):
    data = {"site": {"url": "https://example.org", **overrides}, "engines": [{"provider": "openai", "model": "gpt-5-mini"}], "design": {"budget_usd_per_burst": 1.0}}
    return ProjectConfig.model_validate(data)


def test_sitemaps_from_robots_reads_any_case():
    assert sitemaps_from_robots("User-agent: *\nsitemap: https://example.org/a.xml\nSITEMAP: https://example.org/b.xml\n") == [
        "https://example.org/a.xml", "https://example.org/b.xml"]


def test_parse_sitemap_handles_urlset_index_and_gzip():
    found, children = parse_sitemap(SM.format(loc("https://example.org/a", "2026-01-02") + loc("https://example.org/b")).encode())
    assert [(f.url, f.lastmod) for f in found] == [("https://example.org/a", "2026-01-02"), ("https://example.org/b", None)]
    _, kids = parse_sitemap(IDX.format("<sitemap><loc>https://example.org/s1.xml</loc></sitemap>").encode())
    assert kids == ["https://example.org/s1.xml"]
    found, _ = parse_sitemap(gzip.compress(SM.format(loc("https://example.org/z")).encode()))
    assert found[0].url == "https://example.org/z"
    assert parse_sitemap(b"not xml") == ([], [])


def test_parse_feed_rss_and_atom():
    rss = b'<rss><channel><item><title>T</title><link>https://example.org/p1</link><pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate></item></channel></rss>'
    assert [(f.url, f.title) for f in parse_feed(rss)] == [("https://example.org/p1", "T")]
    atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title><link rel="alternate" href="https://example.org/p2"/><updated>2026-01-03T00:00:00Z</updated></entry></feed>'
    [f] = parse_feed(atom)
    assert (f.url, f.title, f.lastmod, f.source) == ("https://example.org/p2", "A", "2026-01-03T00:00:00Z", "rss")


def test_feed_links_from_html_resolves_relative():
    html = '<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"><link rel="stylesheet" href="/x.css"></head></html>'
    assert feed_links_from_html(html, "https://example.org/") == ["https://example.org/feed.xml"]


def test_youtube_channel_id_forms():
    assert youtube_channel_id("UC1234567890123456789012") == "UC1234567890123456789012"
    assert youtube_channel_id("https://www.youtube.com/channel/UC1234567890123456789012/videos") == "UC1234567890123456789012"
    assert youtube_channel_id("@alichannel") is None


YT = (b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry>'
      b'<yt:videoId>dQw4w9WgXcQ</yt:videoId><title>V</title><published>2026-02-01T00:00:00+00:00</published></entry></feed>')


def test_parse_youtube_feed():
    [f] = parse_youtube_feed(YT)
    assert (f.url, f.title, f.source) == ("https://youtube.com/watch?v=dQw4w9WgXcQ", "V", "youtube")


async def test_discover_follows_index_filters_hosts_dedupes_and_caps(httpx_mock):
    httpx_mock.add_response(url="https://example.org/robots.txt", text="Sitemap: https://example.org/idx.xml\n")
    httpx_mock.add_response(url="https://example.org/idx.xml", text=IDX.format(
        "<sitemap><loc>https://example.org/s1.xml</loc></sitemap><sitemap><loc>https://example.org/s2.xml.gz</loc></sitemap><sitemap><loc>https://example.org/idx.xml</loc></sitemap>"))
    httpx_mock.add_response(url="https://example.org/s1.xml", text=SM.format(loc("https://example.org/a/") + loc("https://example.org/a") + loc("https://other.net/x")))
    httpx_mock.add_response(url="https://example.org/s2.xml.gz", content=gzip.compress(SM.format("".join(loc(f"https://example.org/p{i}") for i in range(10))).encode()))
    httpx_mock.add_response(url="https://example.org/", text="<html></html>")
    for path in ("/feed", "/rss.xml", "/atom.xml", "/feed.xml"):
        httpx_mock.add_response(url=f"https://example.org{path}", status_code=404)
    async with httpx.AsyncClient() as client:
        pages = await discover_pages(client, site().site, limit=5)
    assert [p.canonical_url for p in pages] == ["https://example.org/a", "https://example.org/p0", "https://example.org/p1", "https://example.org/p2", "https://example.org/p3"]
    assert all(p.source == "sitemap" and p.id == Page.id_for(p.canonical_url) for p in pages)


async def test_discover_falls_back_to_feeds_and_youtube(httpx_mock):
    httpx_mock.add_response(url="https://example.org/robots.txt", status_code=404)
    httpx_mock.add_response(url="https://example.org/sitemap.xml", status_code=404)
    httpx_mock.add_response(url="https://example.org/sitemap_index.xml", status_code=404)
    httpx_mock.add_response(url="https://example.org/", text='<link rel="alternate" type="application/atom+xml" href="https://example.org/atom">')
    httpx_mock.add_response(url="https://example.org/atom", content=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title><link rel="alternate" href="https://example.org/p2"/></entry></feed>')
    httpx_mock.add_response(url="https://www.youtube.com/@alichannel", text='... "channelId":"UC1234567890123456789012" ...')
    httpx_mock.add_response(url="https://www.youtube.com/feeds/videos.xml?channel_id=UC1234567890123456789012", content=YT)
    async with httpx.AsyncClient() as client:
        pages = await discover_pages(client, site(youtube_channels=["@alichannel"]).site)
    assert [(p.canonical_url, p.source) for p in pages] == [("https://example.org/p2", "rss"), ("https://youtube.com/watch?v=dQw4w9WgXcQ", "youtube")]


def test_write_and_read_pages_round_trip_and_rewrite(tmp_path):
    a = Page(id=Page.id_for("https://example.org/a"), url="https://example.org/a", canonical_url="https://example.org/a", source="manual")
    b = Page(id=Page.id_for("https://example.org/b"), url="https://example.org/b", canonical_url="https://example.org/b", source="manual")
    write_pages(tmp_path, [a, b])
    assert [p.id for p in read_pages(tmp_path)] == [a.id, b.id]
    write_pages(tmp_path, [b])
    assert [p.id for p in read_pages(tmp_path)] == [b.id]
    assert read_pages(tmp_path / "nowhere") == []


def test_owned_set_for_collects_offsite_pages_and_video_ids():
    cfg = site(offsite_prefixes=["https://medium.com/@ali"])
    pages = [
        Page(id="1", url="https://example.org/a", canonical_url="https://example.org/a", source="sitemap"),
        Page(id="2", url="https://youtu.be/dQw4w9WgXcQ", canonical_url="https://youtube.com/watch?v=dQw4w9WgXcQ", source="youtube"),
        Page(id="3", url="https://dev.to/ali/post", canonical_url="https://dev.to/ali/post", source="manual"),
    ]
    owned = owned_set_for(cfg, pages)
    assert owned.domains == ("example.org",) and owned.prefixes == ("https://medium.com/@ali",)
    assert owned.urls == frozenset({"https://dev.to/ali/post"}) and owned.youtube_video_ids == frozenset({"dQw4w9WgXcQ"})
```

- [ ] **Step 2: Run to verify failure, then write library.py**

Write `library.py` to satisfy the tests. Required shapes: `Found` is a frozen dataclass `(url: str, title: str | None, lastmod: str | None, source: PageSource)`; XML parsing uses `xml.etree.ElementTree.fromstring` inside `try/except ET.ParseError` (return empty on failure); namespaces handled by matching local names (`tag.rsplit('}', 1)[-1]`) so unprefixed and prefixed sitemaps both parse; gzip detected by the two magic bytes; `fetch_bytes` uses `client.get(url, follow_redirects=True, timeout=20.0)` with a `FootnoteOne/<version> (+https://github.com/shehral/footnoteone)` user agent, returns None on `httpx.HTTPError`, a non-2xx status, or a body over `max_bytes`; `discover_pages` keeps a `visited` set of sitemap URLs and stops after 50 sitemap fetches; own-host filter uses `host_of(found.url)` against `site.own_domains` (equal or subdomain); YouTube pages bypass the host filter; the returned list is in discovery order (sitemap, feed, youtube) deduplicated by `canonicalize(url)` and cut at `limit`. `write_pages` writes `root/.footnote/pages.jsonl` through a temp file and `os.replace`; `read_pages` returns `[]` when the file is missing (use `JsonlStore(root / ".footnote").iter("pages", Page)`).

- [ ] **Step 3: Run the library tests, the suite, ruff; commit**

```bash
git add src/footnoteone/library.py tests/test_library.py
git commit -s -m "feat: page library discovery from sitemaps, feeds and YouTube channel feeds"
```

---

### Task 5: Design enumeration and the planner

**Files:**
- Create: `src/footnoteone/design.py`, `src/footnoteone/plan.py`
- Test: `tests/test_design.py`, `tests/test_plan.py`

**Interfaces:**
- Consumes: `schema.Intent`, `schema.Prompt`, `schema.EngineConfig`, `schema.run_key`, `config.ProjectConfig`, `planning.*`, `pricing.PriceTable`, `stats.mde`.
- Produces: `design.Call(intent, prompt, engine, rep_idx)` with `.key(label)`; `design.enumerate_calls(intents, engines, reps) -> list[Call]` (order: rep, then intent, then prompt, then engine, so a budget that runs out leaves every intent partly covered rather than some intents untouched); `design.headline_intents(intents)`; `plan.planning_baseline(p) -> tuple[float, bool]`; `plan.moved_reachable_at(n_engines, min_intents=8) -> int`; `plan.intents_needed(p, m, icc, n_engines, target=0.10, min_intents=8, cap=1000) -> int | None`; `plan.EnginePlan`; `plan.PlanSummary`; `plan.make_plan(config, intents, engines, table, assumptions) -> PlanSummary`; `plan.render_plan_markdown(summary) -> str`.

- [ ] **Step 1: Tests**

```python
# tests/test_design.py
from footnoteone.design import Call, enumerate_calls, headline_intents
from footnoteone.schema import EngineConfig, Intent, Prompt, run_key


def intents(n_unbranded=2):
    out = [Intent(id=f"i{k}", label=f"i{k}", prompts=[Prompt(id=f"i{k}p0", text="a"), Prompt(id=f"i{k}p1", text="b", paraphrase_idx=1)]) for k in range(n_unbranded)]
    out.append(Intent(id="brand", label="brand", kind="branded", prompts=[Prompt(id="bp0", text="c")]))
    return out


def test_enumerate_order_is_rep_intent_prompt_engine():
    engines = [EngineConfig(provider="openai", model_requested="m"), EngineConfig(provider="anthropic", model_requested="n")]
    calls = enumerate_calls(intents(), engines, reps=2)
    assert len(calls) == 2 * (2 * 2 + 1) * 2
    first = [(c.rep_idx, c.intent.id, c.prompt.paraphrase_idx, c.engine.provider) for c in calls[:4]]
    assert first == [(0, "i0", 0, "openai"), (0, "i0", 0, "anthropic"), (0, "i0", 1, "openai"), (0, "i0", 1, "anthropic")]
    assert calls[-1].rep_idx == 1 and calls[-1].intent.id == "brand"


def test_call_key_matches_run_key():
    engine = EngineConfig(provider="openai", model_requested="m")
    call = Call(intents()[0], intents()[0].prompts[1], engine, 1)
    assert call.key("2026-10-07") == run_key("2026-10-07", "i0", "i0p1", engine.config_sha, 1)


def test_headline_intents_are_unbranded_only():
    assert [i.id for i in headline_intents(intents())] == ["i0", "i1"]
```

```python
# tests/test_plan.py
import pytest

from footnoteone.config import ProjectConfig, engine_configs
from footnoteone.plan import intents_needed, make_plan, moved_reachable_at, planning_baseline, render_plan_markdown
from footnoteone.planning import PlanningAssumptions, worst_case_usd
from footnoteone.pricing import PriceTable
from footnoteone.schema import Intent, Prompt
from footnoteone.stats import mde


def project(budget=20.0, engines=None):
    engines = engines or [{"provider": "openai", "model": "gpt-5-mini"}, {"provider": "anthropic", "model": "claude-sonnet-4-5"}]
    return ProjectConfig.model_validate({"site": {"url": "https://example.org"}, "engines": engines, "design": {"paraphrases": 3, "reps": 2, "budget_usd_per_burst": budget}})


def intents(unbranded=8, branded=1, placebo=1):
    def mk(prefix, n, kind):
        return [Intent(id=f"{prefix}{k}", label=prefix, kind=kind, prompts=[Prompt(id=f"{prefix}{k}p{j}", text="q", paraphrase_idx=j) for j in range(3)]) for k in range(n)]
    return mk("u", unbranded, "unbranded") + mk("b", branded, "branded") + mk("p", placebo, "placebo")


def test_planning_baseline_floors_and_caps():
    assert planning_baseline(0.2) == (0.2, False)
    assert planning_baseline(0.0) == (0.05, True)
    assert planning_baseline(1.0) == (0.95, True)


def test_moved_reachable_at_respects_holm_and_floor():
    assert moved_reachable_at(1) == 8 and moved_reachable_at(3) == 8 and moved_reachable_at(3, min_intents=6) == 7
    assert moved_reachable_at(20) == 10  # k = 9: 20 x 2 / 512 = 0.078 fails; k = 10: 20 x 2 / 1024 = 0.039 passes


def test_intents_needed_matches_mde_inversion():
    assert intents_needed(0.2, m=6, icc=0.3, n_engines=3) == 105
    assert mde(0.2, 105 * 6, 6, 0.3) <= 0.10 < mde(0.2, 104 * 6, 6, 0.3)
    assert intents_needed(0.5, m=6, icc=0.3, n_engines=1, cap=50) is None


def test_make_plan_counts_costs_and_power():
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    engines = engine_configs(cfg, a)
    summary = make_plan(cfg, intents(), engines, table, a)
    assert (summary.intents_total, summary.intents_unbranded, summary.intents_branded, summary.intents_placebo) == (10, 8, 1, 1)
    assert summary.paraphrases == 3 and summary.reps == 2 and summary.calls_per_burst_total == 10 * 3 * 2 * 2
    e = summary.engines[0]
    assert e.calls_per_burst == 60 and e.worst_burst_usd == pytest.approx(60 * worst_case_usd(engines[0], table, a))
    assert e.mde_points == pytest.approx(100 * mde(0.2, 8 * 6, 6, 0.3)) and e.baseline_is_prior is False
    assert summary.moved_reachable_at == 8 and summary.intents_needed_for_mde == intents_needed(0.2, 6, 0.3, 2)
    assert summary.worst_total_usd == pytest.approx(sum(x.worst_burst_usd for x in summary.engines))
    assert summary.fits_worst == (summary.worst_total_usd <= 20.0) and summary.min_shared_intents == 8
    assert summary.warnings == [] or all(isinstance(w, str) for w in summary.warnings)


def test_make_plan_warns():
    cfg, table, a = project(budget=0.01, engines=[{"provider": "openai", "model": "gpt-999"}]), PriceTable.load(), PlanningAssumptions.load()
    summary = make_plan(cfg, intents(unbranded=3, placebo=0), engine_configs(cfg, a), table, a)
    text = "\n".join(summary.warnings)
    assert "not priced" in text and "budget" in text and "placebo" in text and "8 unbranded" in text
    assert summary.engines[0].priced is False


def test_render_plan_markdown_has_plain_words_and_no_dashes():
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    md = render_plan_markdown(make_plan(cfg, intents(), engine_configs(cfg, a), table, a))
    assert "worst case" in md and "api:openai" in md and "intents" in md
    assert "\u2014" not in md and "\u2013" not in md
```

- [ ] **Step 2: Run to verify failure, then write design.py and plan.py**

```python
# src/footnoteone/design.py
"""The call list of one burst: every intent x wording x engine x repeat, in budget-fair order."""

from __future__ import annotations

from dataclasses import dataclass

from footnoteone.schema import EngineConfig, Intent, Prompt, run_key


@dataclass(frozen=True)
class Call:
    intent: Intent
    prompt: Prompt
    engine: EngineConfig
    rep_idx: int

    def key(self, label: str) -> str:
        return run_key(label, self.intent.id, self.prompt.id, self.engine.config_sha, self.rep_idx)


def enumerate_calls(intents: list[Intent], engines: list[EngineConfig], reps: int) -> list[Call]:
    """Repeat-major, then intent, wording, engine: if the budget runs out, every intent has some answers
    from every engine rather than some intents having none."""
    return [
        Call(intent, prompt, engine, rep)
        for rep in range(reps)
        for intent in intents
        for prompt in intent.prompts
        for engine in engines
    ]


def headline_intents(intents: list[Intent]) -> list[Intent]:
    return [i for i in intents if i.kind == "unbranded"]
```

```python
# src/footnoteone/plan.py
"""Cost and power planning for a burst: what it costs at most, and what it could ever detect."""

from __future__ import annotations

from dataclasses import dataclass, field

from footnoteone.config import ProjectConfig
from footnoteone.design import headline_intents
from footnoteone.planning import PlanningAssumptions, typical_usd, worst_case_usd
from footnoteone.pricing import PriceTable
from footnoteone.schema import EngineConfig, Intent
from footnoteone.stats import mde

BASELINE_FLOOR, BASELINE_CAP, TARGET_POINTS = 0.05, 0.95, 0.10


def planning_baseline(p: float) -> tuple[float, bool]:
    """The baseline used for power maths: p floored at 0.05 and capped at 0.95; the flag says it is a prior."""
    used = min(max(p, BASELINE_FLOOR), BASELINE_CAP)
    return used, used != p


def moved_reachable_at(n_engines: int, min_intents: int = 8) -> int:
    """Smallest number of shared intents at which Moved is reachable: the most extreme sign-flip p is
    2 / 2**k, and Holm across n engines multiplies it by n, so n x 2 / 2**k must be below 0.05."""
    k = max(1, min_intents)
    while max(1, n_engines) * 2 / 2**k >= 0.05:
        k += 1
    return k


def intents_needed(p: float, m: int, icc: float, n_engines: int, target: float = TARGET_POINTS, min_intents: int = 8, cap: int = 1000) -> int | None:
    """Smallest k >= moved_reachable_at with mde(p, N = k x m, m, icc) <= target; None past cap."""
    p_used, _ = planning_baseline(p)
    for k in range(moved_reachable_at(n_engines, min_intents), cap + 1):
        if mde(p_used, k * m, m, icc) <= target:
            return k
    return None


@dataclass
class EnginePlan:
    engine: EngineConfig
    surface: str
    priced: bool
    calls_per_burst: int
    typical_call_usd: float
    worst_call_usd: float
    typical_burst_usd: float
    worst_burst_usd: float
    baseline_used: float
    baseline_is_prior: bool
    mde_points: float | None  # percentage points, for the headline intents of one burst against another


@dataclass
class PlanSummary:
    engines: list[EnginePlan]
    intents_total: int
    intents_unbranded: int
    intents_branded: int
    intents_placebo: int
    paraphrases: int
    reps: int
    calls_per_burst_total: int
    typical_total_usd: float
    worst_total_usd: float
    budget_usd: float
    fits_typical: bool
    fits_worst: bool
    min_shared_intents: int
    moved_reachable_at: int
    intents_needed_for_mde: int | None
    price_table_version: str
    planning_version: str
    warnings: list[str] = field(default_factory=list)


def make_plan(config: ProjectConfig, intents: list[Intent], engines: list[EngineConfig], table: PriceTable, a: PlanningAssumptions) -> PlanSummary:
    d = config.design
    headline = headline_intents(intents)
    m = d.paraphrases * d.reps
    p_used, is_prior = planning_baseline(d.baseline_cited_rate)
    plans: list[EnginePlan] = []
    warnings: list[str] = []
    calls_per_engine = sum(len(i.prompts) for i in intents) * d.reps
    for e in engines:
        priced = table.is_known(e.provider, e.model_requested)
        typical, worst = typical_usd(e, table, a), worst_case_usd(e, table, a)
        n_headline = len(headline) * m
        points = 100 * mde(p_used, n_headline, m, d.icc) if n_headline else None
        plans.append(EnginePlan(e, e.surface, priced, calls_per_engine, typical, worst, calls_per_engine * typical, calls_per_engine * worst, p_used, is_prior, points))
        if not priced:
            warnings.append(f"{e.surface} {e.model_requested}: not priced in pricing.yaml {table.version}; `footnote run` refuses unpriced engines")
    typical_total = sum(x.typical_burst_usd for x in plans)
    worst_total = sum(x.worst_burst_usd for x in plans)
    if worst_total > d.budget_usd_per_burst:
        warnings.append(f"budget {d.budget_usd_per_burst:.2f} USD is below the worst case {worst_total:.2f} USD; a run records budget_skip once the reserve is spent")
    if not headline:
        warnings.append("no unbranded intents: nothing can enter a headline")
    elif len(headline) < d.min_shared_intents:
        warnings.append(f"{len(headline)} unbranded intents; no verdict is possible below {d.min_shared_intents} unbranded intents (need at least 8 unbranded intents)")
    if not any(i.kind == "placebo" for i in intents):
        warnings.append("no placebo intent: the placebo floor cannot be measured")
    if is_prior:
        warnings.append(f"baseline cited rate {d.baseline_cited_rate} replaced by the planning prior {p_used}")
    return PlanSummary(
        engines=plans, intents_total=len(intents), intents_unbranded=len(headline),
        intents_branded=sum(i.kind == "branded" for i in intents), intents_placebo=sum(i.kind == "placebo" for i in intents),
        paraphrases=d.paraphrases, reps=d.reps, calls_per_burst_total=calls_per_engine * len(engines),
        typical_total_usd=typical_total, worst_total_usd=worst_total, budget_usd=d.budget_usd_per_burst,
        fits_typical=typical_total <= d.budget_usd_per_burst, fits_worst=worst_total <= d.budget_usd_per_burst,
        min_shared_intents=d.min_shared_intents, moved_reachable_at=moved_reachable_at(len(engines), d.min_shared_intents),
        intents_needed_for_mde=intents_needed(d.baseline_cited_rate, m, d.icc, len(engines), min_intents=d.min_shared_intents),
        price_table_version=table.version, planning_version=a.version, warnings=warnings,
    )


def render_plan_markdown(s: PlanSummary) -> str:
    lines = [
        "# Plan for one burst", "",
        f"{s.intents_total} intents ({s.intents_unbranded} unbranded, {s.intents_branded} branded, {s.intents_placebo} placebo) x {s.paraphrases} wordings x {s.reps} repeats = {s.calls_per_burst_total} calls across {len(s.engines)} engines.",
        "", "| Engine | Model | Calls | Typical cost | Worst case | Detectable move (points) |", "|---|---|---|---|---|---|",
    ]
    for e in s.engines:
        points = "no data" if e.mde_points is None else f"{e.mde_points:.0f}"
        priced = "" if e.priced else " (not priced)"
        lines.append(f"| {e.surface} | {e.engine.model_requested}{priced} | {e.calls_per_burst} | {e.typical_burst_usd:.2f} USD | {e.worst_burst_usd:.2f} USD | {points} |")
    fit = "fits" if s.fits_worst else "does not fit"
    lines += [
        "", f"Typical total {s.typical_total_usd:.2f} USD; worst case {s.worst_total_usd:.2f} USD; budget {s.budget_usd:.2f} USD ({fit} the worst case).",
        f"Baseline cited rate used {s.engines[0].baseline_used:.2f}" + (" (planning prior)" if s.engines and s.engines[0].baseline_is_prior else "") + f"; detectable move = minimum detectable effect at 80% power across the {s.intents_unbranded} unbranded intents.",
        f"A Moved verdict needs at least {s.moved_reachable_at} shared intents (Holm across {len(s.engines)} engines); "
        + ("a 10-point move needs about " + str(s.intents_needed_for_mde) + " intents at this design." if s.intents_needed_for_mde else "a 10-point move is out of reach below 1000 intents at this design."),
        f"Prices: pricing.yaml {s.price_table_version}; assumptions: planning.yaml {s.planning_version}. API answers, not the consumer apps.",
    ]
    if s.warnings:
        lines += ["", "Warnings:"] + [f"- {w}" for w in s.warnings]
    return "\n".join(lines) + "\n"
```

- [ ] **Step 3: Run both test files, the suite, ruff; commit**

```bash
git add src/footnoteone/design.py src/footnoteone/plan.py tests/test_design.py tests/test_plan.py
git commit -s -m "feat: burst call enumeration and the cost and power planner"
```

---

### Task 6: The runner

**Files:**
- Create: `src/footnoteone/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `design.enumerate_calls`, `design.Call`, `config.ProjectConfig`, `config.config_sha_of`, `config.ConfigError`, `planning.worst_case_usd`, `planning.PlanningAssumptions`, `pricing.PriceTable`, `schema` (`Manifest`, `Run`, `SourceRecord`, `effective_status`, `utcnow`), `store.JsonlStore`, `store.RawStore`, `canon.canonicalize`, `canon.CANON_VERSION`, the three adapter classes, `footnoteone.__version__`.
- Produces: `runner.RunnerError(ConfigError)`; `runner.default_adapters() -> dict[str, Adapter]`; `runner.keys_from_env(config, environ=os.environ) -> dict[str, str | None]`; `runner.RunnerResult(manifest: Manifest | None, counts: dict[str, int], spent_usd: float, skipped_existing: int, planned_calls: int, dry_run: bool)`; `async runner.run_design(root, config, intents, engines, label, budget_usd, dry_run, adapters, keys, table, assumptions, client=None, clock=utcnow, sleep=asyncio.sleep, timeout_s=None) -> RunnerResult`; constants `RETRY_DELAYS = (2.0, 8.0)`, `TRANSIENT = {408, 409, 425, 429, 500, 502, 503, 504}`.

Behaviour (each line has a test):
- Preflight, before any write: every engine must be priced (`table.is_known`), else `RunnerError` naming the engine; unless dry run, every provider in use must have a key, else `RunnerError` naming the env var. Dry run returns the planned call count and writes nothing.
- A `Manifest` is appended at the start (status running, label, engines, intents, canon_version, planning_version, paraphrases, reps, config_sha, price table version, adapter versions) and appended again at the end with spent, finished_at and status done, or aborted when an exception escapes the loop (the exception still propagates). Last record per manifest id wins.
- Resume: a call whose `run_key` already exists in `runs.jsonl` with status ok is skipped and counted in `skipped_existing`; calls whose last record is not ok are attempted again (a later record with the same Run id supersedes the earlier one; readers take the last record per id).
- Budget: before each call the worst case (`worst_case_usd`) is reserved; when spent + worst case exceeds the budget, the call is recorded as `budget_skip` with cost 0 and an evidence string naming spent, worst case and budget, and the loop continues so every remaining call is recorded.
- Call: `asyncio.wait_for(adapter.call(...), timeout)`; a timeout records status timeout, cost = worst case, no raw; `httpx.HTTPStatusError` with a transient code and `httpx.TransportError` retry with `RETRY_DELAYS` (through the injected `sleep`), then status error with cost 0; any other HTTP error status is error at once; on success the raw blob is stored first, then `parse` runs inside try/except (an exception gives status error, the raw sha and parser version recorded, cost = worst case), then `price` inside try/except (an exception charges the worst case and notes it), then `effective_status` turns an all-searches-failed observation into error with its reason appended to the evidence.
- Write order per call: raw blob, then one `SourceRecord` per consulted and cited ref (`run_id` = the run key, `canonical_url` = `canonicalize(url)`), then the `Run` (id = run key, timestamps from `clock`). Politeness sleep between calls when configured.

- [ ] **Step 1: Tests**

```python
# tests/test_runner.py
import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from footnoteone.adapters.base import RawResponse
from footnoteone.config import ProjectConfig, engine_configs
from footnoteone.planning import PlanningAssumptions, worst_case_usd
from footnoteone.pricing import PriceTable
from footnoteone.runner import RunnerError, keys_from_env, run_design
from footnoteone.schema import Intent, Manifest, Observation, Prompt, Run, SourceRecord, SourceRef
from footnoteone.store import JsonlStore, RawStore

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


class FakeAdapter:
    provider = "openai"
    version = "fake@1"

    def __init__(self, script):
        self.script, self.calls = list(script), 0

    def build_request(self, prompt_text, engine):
        return {"input": prompt_text}

    async def call(self, client, prompt_text, engine, api_key):
        self.calls += 1
        action = self.script.pop(0)
        req = httpx.Request("POST", "https://api.example")
        if action == "slow":
            await asyncio.sleep(10)
        if action == "500":
            raise httpx.HTTPStatusError("boom", request=req, response=httpx.Response(500, request=req))
        if action == "403":
            raise httpx.HTTPStatusError("no", request=req, response=httpx.Response(403, request=req))
        if action == "transport":
            raise httpx.ConnectError("down")
        if action == "interrupt":
            raise KeyboardInterrupt
        body = {"explode": True} if action == "badparse" else {"failed": 2 if action == "allfailed" else 0}
        return RawResponse(provider="openai", model_requested=engine.model_requested, request={"k": api_key[:0]}, response=body)

    def parse(self, raw):
        if raw.response.get("explode"):
            raise RuntimeError("cannot parse")
        return Observation(
            activated="yes", activation_evidence="fake", answer_text="t",
            consulted=[SourceRef(url="http://www.example.org/a/", rank=1, provider_field="f")],
            cited=[SourceRef(url="https://example.org/a", rank=1, provider_field="c")],
            model_requested=raw.model_requested, search_calls=2, failed_searches=raw.response.get("failed", 0),
            input_tokens=10, output_tokens=5,
        )

    def price(self, obs, table):
        return 0.01


def project(budget=5.0, politeness=0.0, model="gpt-5-mini"):
    return ProjectConfig.model_validate({"site": {"url": "https://example.org"}, "engines": [{"provider": "openai", "model": model}],
                                         "design": {"paraphrases": 2, "reps": 1, "budget_usd_per_burst": budget, "politeness_s": politeness}})


INTENTS = [Intent(id="i1", label="i1", prompts=[Prompt(id="p0", text="q0"), Prompt(id="p1", text="q1", paraphrase_idx=1)])]


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


async def go(tmp_path, adapter, budget=5.0, dry_run=False, keys=None, label="b1", politeness=0.0, model="gpt-5-mini", **kw):
    cfg, table, a = project(budget, politeness, model), PriceTable.load(), PlanningAssumptions.load()
    engines = engine_configs(cfg, a)
    delays = []

    async def sleep(s):
        delays.append(s)

    result = await run_design(tmp_path, cfg, INTENTS, engines, label, budget, dry_run, {"openai": adapter},
                              {"openai": "sk-test"} if keys is None else keys, table, a, clock=Clock(), sleep=sleep, **kw)
    return result, engines, delays


def records(tmp_path):
    store = JsonlStore(tmp_path / ".footnote")
    return list(store.iter("manifests", Manifest)), list(store.iter("runs", Run)), list(store.iter("sources", SourceRecord))


async def test_happy_path_writes_manifest_sources_and_runs(tmp_path):
    adapter = FakeAdapter(["ok", "ok"])
    result, engines, _ = await go(tmp_path, adapter)
    manifests, runs, sources = records(tmp_path)
    assert result.counts == {"ok": 2} and result.spent_usd == pytest.approx(0.02) and result.planned_calls == 2
    assert [m.status for m in manifests] == ["running", "done"] and manifests[1].spent_usd == pytest.approx(0.02)
    assert manifests[0].label == "b1" and manifests[0].engines == engines and [i.id for i in manifests[0].intents] == ["i1"]
    assert manifests[1].finished_at == T0 and manifests[0].canon_version == 2
    assert {r.id for r in runs} == {c for c in (f"{x}" for x in [])} | {r.id for r in runs}  # ids are the run keys below
    assert all(r.status == "ok" and r.raw_sha256 and r.parser_version == "fake@1" and r.cost_usd == 0.01 for r in runs)
    assert sorted(s.role for s in sources) == ["cited", "cited", "consulted", "consulted"]
    assert {s.canonical_url for s in sources} == {"https://example.org/a"}
    assert all(RawStore(tmp_path / ".footnote").exists(r.raw_sha256) for r in runs)


async def test_resume_skips_ok_runs(tmp_path):
    adapter = FakeAdapter(["ok", "ok", "ok", "ok"])
    await go(tmp_path, adapter)
    result, _, _ = await go(tmp_path, adapter)
    assert result.skipped_existing == 2 and result.counts == {} and adapter.calls == 2


async def test_budget_below_worst_case_makes_no_call(tmp_path):
    adapter = FakeAdapter(["ok", "ok"])
    result, engines, _ = await go(tmp_path, adapter, budget=0.001)
    _, runs, _ = records(tmp_path)
    assert adapter.calls == 0 and result.counts == {"budget_skip": 2}
    assert all(r.status == "budget_skip" and r.cost_usd == 0 and "budget" in r.activation_evidence for r in runs)


async def test_budget_runs_out_midway(tmp_path):
    adapter = FakeAdapter(["ok", "ok"])
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    worst = worst_case_usd(engine_configs(cfg, a)[0], table, a)
    result, _, _ = await go(tmp_path, adapter, budget=worst + 0.005)
    assert result.counts == {"ok": 1, "budget_skip": 1} and adapter.calls == 1


async def test_timeout_is_charged_at_worst_case(tmp_path):
    adapter = FakeAdapter(["slow", "ok"])
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    worst = worst_case_usd(engine_configs(cfg, a)[0], table, a)
    result, _, _ = await go(tmp_path, adapter, timeout_s=0.01)
    _, runs, _ = records(tmp_path)
    timed = next(r for r in runs if r.status == "timeout")
    assert timed.cost_usd == pytest.approx(worst) and timed.raw_sha256 is None and result.counts == {"timeout": 1, "ok": 1}


async def test_transient_errors_retry_then_fail(tmp_path):
    adapter = FakeAdapter(["500", "ok", "transport", "transport", "transport"])
    result, _, delays = await go(tmp_path, adapter)
    _, runs, _ = records(tmp_path)
    assert result.counts == {"ok": 1, "error": 1} and delays[:1] == [2.0] and delays.count(8.0) == 1
    assert adapter.calls == 5 and all(r.cost_usd == 0 for r in runs if r.status == "error")


async def test_non_transient_http_error_is_immediate(tmp_path):
    adapter = FakeAdapter(["403", "ok"])
    result, _, delays = await go(tmp_path, adapter)
    assert result.counts == {"error": 1, "ok": 1} and delays == [] and adapter.calls == 2


async def test_parse_failure_keeps_raw_and_charges_worst_case(tmp_path):
    adapter = FakeAdapter(["badparse", "ok"])
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    worst = worst_case_usd(engine_configs(cfg, a)[0], table, a)
    await go(tmp_path, adapter)
    _, runs, _ = records(tmp_path)
    bad = next(r for r in runs if r.status == "error")
    assert bad.raw_sha256 and bad.parser_version == "fake@1" and bad.cost_usd == pytest.approx(worst) and "parse failed" in bad.activation_evidence


async def test_all_searches_failed_is_an_error(tmp_path):
    adapter = FakeAdapter(["allfailed", "ok"])
    result, _, _ = await go(tmp_path, adapter)
    _, runs, _ = records(tmp_path)
    assert result.counts == {"error": 1, "ok": 1}
    assert any("all searches failed" in r.activation_evidence for r in runs)


async def test_missing_key_and_unpriced_model_refuse_before_writing(tmp_path):
    with pytest.raises(RunnerError, match="OPENAI_API_KEY"):
        await go(tmp_path, FakeAdapter([]), keys={"openai": None})
    with pytest.raises(RunnerError, match="gpt-999"):
        await go(tmp_path, FakeAdapter([]), model="gpt-999")
    assert not (tmp_path / ".footnote" / "manifests.jsonl").exists()


async def test_dry_run_counts_without_keys_or_writes(tmp_path):
    result, _, _ = await go(tmp_path, FakeAdapter([]), dry_run=True, keys={"openai": None})
    assert result.dry_run and result.planned_calls == 2 and result.manifest is None
    assert not (tmp_path / ".footnote" / "manifests.jsonl").exists()


async def test_write_order_is_raw_sources_run(tmp_path, monkeypatch):
    order = []
    real_append, real_put = JsonlStore.append, RawStore.put
    monkeypatch.setattr(JsonlStore, "append", lambda self, name, rec: (order.append(name), real_append(self, name, rec))[1])
    monkeypatch.setattr(RawStore, "put", lambda self, obj: (order.append("raw"), real_put(self, obj))[1])
    await go(tmp_path, FakeAdapter(["ok", "ok"]))
    assert order == ["manifests", "raw", "sources", "sources", "runs", "raw", "sources", "sources", "runs", "manifests"]


async def test_interrupt_marks_manifest_aborted(tmp_path):
    with pytest.raises(KeyboardInterrupt):
        await go(tmp_path, FakeAdapter(["ok", "interrupt"]))
    manifests, runs, _ = records(tmp_path)
    assert manifests[-1].status == "aborted" and len(runs) == 1


async def test_politeness_sleep_between_calls(tmp_path):
    _, _, delays = await go(tmp_path, FakeAdapter(["ok", "ok"]), politeness=0.5)
    assert delays == [0.5, 0.5]


def test_keys_from_env_reads_configured_names():
    cfg = project()
    assert keys_from_env(cfg, {"OPENAI_API_KEY": "sk-x"}) == {"openai": "sk-x", "anthropic": None, "perplexity": None}
```

(The odd assertion in the first test about ids is a placeholder; replace it with: `assert {r.id for r in runs} == {c.key("b1") for c in enumerate_calls(INTENTS, engines, 1)}` importing `enumerate_calls` from `footnoteone.design`.)

- [ ] **Step 2: Run to verify failure, then write runner.py**

```python
"""Run one burst: reserve the worst case before each call, store raw first, then sources, then the run."""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

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
    EngineConfig, Intent, Manifest, Observation, Run, RunStatus, SourceRecord, effective_status, utcnow,
)
from footnoteone.store import JsonlStore, RawStore

RETRY_DELAYS = (2.0, 8.0)
TRANSIENT = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
PROVIDERS = ("openai", "anthropic", "perplexity")


class RunnerError(ConfigError):
    """The run cannot start: an unpriced engine or a missing key. Nothing is written."""


def default_adapters() -> dict[str, Adapter]:
    return {"openai": OpenAIAdapter(), "anthropic": AnthropicAdapter(), "perplexity": PerplexityAdapter()}


def keys_from_env(config: ProjectConfig, environ: Mapping[str, str] = os.environ) -> dict[str, str | None]:
    return {p: environ.get(config.keys.env_name(p)) or None for p in PROVIDERS}


@dataclass
class RunnerResult:
    manifest: Manifest | None
    counts: dict[str, int]
    spent_usd: float
    skipped_existing: int
    planned_calls: int
    dry_run: bool


@dataclass
class _Outcome:
    status: RunStatus
    obs: Observation | None
    raw_sha: str | None
    parser_version: str | None
    cost: float
    evidence: str


async def _execute(client, adapter: Adapter, raw_store: RawStore, call: Call, api_key: str, table: PriceTable, timeout: float, worst: float, sleep) -> _Outcome:
    attempt = 0
    while True:
        try:
            raw = await asyncio.wait_for(adapter.call(client, call.prompt.text, call.engine, api_key), timeout)
            break
        except TimeoutError:
            return _Outcome("timeout", None, None, None, worst, f"no response within {timeout:g}s; charged the worst case")
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in TRANSIENT and attempt < len(RETRY_DELAYS):
                await sleep(RETRY_DELAYS[attempt])
                attempt += 1
                continue
            return _Outcome("error", None, None, None, 0.0, f"HTTP {code} from the provider after {attempt + 1} attempt(s)")
        except httpx.TransportError as exc:
            if attempt < len(RETRY_DELAYS):
                await sleep(RETRY_DELAYS[attempt])
                attempt += 1
                continue
            return _Outcome("error", None, None, None, 0.0, f"transport error: {type(exc).__name__} after {attempt + 1} attempt(s)")
    raw_sha = raw_store.put(raw.as_blob())
    try:
        obs = adapter.parse(raw)
    except Exception as exc:  # the parser is pure and should never raise; if it does, the blob is kept for replay
        return _Outcome("error", None, raw_sha, adapter.version, worst, f"parse failed: {type(exc).__name__}: {exc}; charged the worst case")
    note = ""
    try:
        cost = adapter.price(obs, table)
    except Exception as exc:
        cost, note = worst, f"; price failed ({type(exc).__name__}), charged the worst case"
    status, reason = effective_status(obs.status, obs.search_calls, obs.failed_searches)
    evidence = obs.activation_evidence + (f"; {reason}" if reason else "") + note
    return _Outcome(status, obs, raw_sha, adapter.version, cost, evidence)


def _record(store: JsonlStore, manifest_id: str, key: str, call: Call, o: _Outcome, started: datetime, finished: datetime) -> None:
    obs = o.obs
    if obs is not None:
        for role, refs in (("consulted", obs.consulted), ("cited", obs.cited)):
            for ref in refs:
                store.append("sources", SourceRecord(
                    run_id=key, role=role, url=ref.url, canonical_url=canonicalize(ref.url), rank=ref.rank,
                    provider_field=ref.provider_field, title=ref.title, char_start=ref.char_start, char_end=ref.char_end))
    store.append("runs", Run(
        id=key, manifest_id=manifest_id, intent_id=call.intent.id, prompt_id=call.prompt.id,
        engine_config_id=call.engine.config_sha, rep_idx=call.rep_idx, status=o.status,
        model_requested=call.engine.model_requested, model_returned=obs.model_returned if obs else None,
        tool_version=call.engine.tool_version, activated=obs.activated if obs else "unknown",
        activation_evidence=o.evidence, raw_sha256=o.raw_sha, parser_version=o.parser_version,
        input_tokens=obs.input_tokens if obs else None, output_tokens=obs.output_tokens if obs else None,
        search_calls=obs.search_calls if obs else 0, cost_usd=o.cost, started_at=started, finished_at=finished))


async def run_design(
    root: Path, config: ProjectConfig, intents: list[Intent], engines: list[EngineConfig], label: str,
    budget_usd: float, dry_run: bool, adapters: Mapping[str, Adapter], keys: Mapping[str, str | None],
    table: PriceTable, assumptions: PlanningAssumptions, client: httpx.AsyncClient | None = None,
    clock: Callable[[], datetime] = utcnow, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    timeout_s: float | None = None,
) -> RunnerResult:
    calls = enumerate_calls(intents, engines, config.design.reps)
    unpriced = [e for e in engines if not table.is_known(e.provider, e.model_requested)]
    if unpriced:
        names = ", ".join(f"{e.provider}/{e.model_requested}" for e in unpriced)
        raise RunnerError(f"engines not priced in pricing.yaml {table.version}: {names}; add a row before running")
    if dry_run:
        return RunnerResult(None, {}, 0.0, 0, len(calls), True)
    providers = sorted({e.provider for e in engines})
    missing = [config.keys.env_name(p) for p in providers if not keys.get(p)]
    if missing:
        raise RunnerError(f"no API key found in environment variable(s) {', '.join(missing)}")
    store, raw_store = JsonlStore(root / ".footnote"), RawStore(root / ".footnote")
    existing = {r.id: r.status for r in store.iter("runs", Run)}  # last record per id wins
    manifest = Manifest(
        code_version=__version__, adapter_versions={p: adapters[p].version for p in providers},
        config_sha=config_sha_of(config, intents, engines), price_table_version=table.version, budget_usd=budget_usd,
        label=label, engines=list(engines), intents=list(intents), canon_version=CANON_VERSION,
        planning_version=assumptions.version, paraphrases=config.design.paraphrases, reps=config.design.reps,
        started_at=clock())
    store.append("manifests", manifest)
    counts: dict[str, int] = defaultdict(int)
    spent, skipped = 0.0, 0
    timeout = timeout_s or config.design.call_timeout_s
    own_client = client is None
    client = client or httpx.AsyncClient(max_redirects=5)
    final_status = "aborted"
    try:
        for call in calls:
            key = call.key(label)
            if existing.get(key) == "ok":
                skipped += 1
                continue
            started = clock()
            worst = worst_case_usd(call.engine, table, assumptions)
            if spent + worst > budget_usd:
                outcome = _Outcome("budget_skip", None, None, None, 0.0, f"budget: spent {spent:.4f} + worst case {worst:.4f} > budget {budget_usd:.4f} USD")
            else:
                adapter, api_key = adapters[call.engine.provider], keys[call.engine.provider] or ""
                outcome = await _execute(client, adapter, raw_store, call, api_key, table, timeout, worst, sleep)
            spent += outcome.cost
            _record(store, manifest.id, key, call, outcome, started, clock())
            counts[outcome.status] += 1
            if config.design.politeness_s and outcome.status != "budget_skip":
                await sleep(config.design.politeness_s)
        final_status = "done"
    finally:
        store.append("manifests", manifest.model_copy(update={"spent_usd": spent, "status": final_status, "finished_at": clock()}))
        if own_client:
            await client.aclose()
    return RunnerResult(manifest, dict(counts), spent, skipped, len(calls), False)
```

- [ ] **Step 3: Run the runner tests, the suite, ruff; commit**

```bash
git add src/footnoteone/runner.py tests/test_runner.py
git commit -s -m "feat: burst runner with worst-case budget reservation, retries, resume and raw-first write order"
```

---

### Task 7: Metrics with replay

**Files:**
- Create: `src/footnoteone/metrics.py`, `tests/helpers.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `schema` (`Manifest`, `Run`, `SourceRecord` with its optional `raw_sha256`, `Intent`, `EngineConfig`, `effective_status`), `store`, `canon` (`canonicalize`, `host_of`, `classify_owner`, `OwnedSet`, `CANON_VERSION`), `library.owned_set_for`, `library.read_pages`, `stats` (`wilson`, `cluster_t_interval`, `cluster_bootstrap_mean`, `jaccard`), `adapters.base.RawResponse`, `config.ProjectConfig`.
- Produces: `metrics.MetricValue(value, lo, hi, numerator, denominator, excluded: dict[str, int], n_intents, method, note)` with `MetricValue.no_data(method, note)`; `metrics.wilson_value(k, n, excluded=None)`; `metrics.t_value(groups: dict[str, list[float]], excluded=None)` (point = mean of per-intent means; interval from `cluster_t_interval`; also fills `note` with the bootstrap interval as "sensitivity: bootstrap lo to hi"); `metrics.RunView`; `metrics.IntentRow`; `metrics.PageRow`; `metrics.HostRow`; `metrics.NoiseFloor`; `metrics.EngineMetrics`; `metrics.Report`; `metrics.load_manifests(store) -> dict[str, Manifest]` (last wins); `metrics.load_runs(store) -> dict[str, Run]` (last wins); `metrics.load_sources(store) -> dict[str, list[SourceRecord]]`; `metrics.select_manifests(manifests, labels=None, since=None, until=None) -> list[Manifest]`; `metrics.run_views(root, adapters, manifests, owned) -> list[RunView]`; `metrics.compute(root, config, intents, engines, pages, adapters, labels=None, since=None, until=None) -> Report`; `tests/helpers.py: build_store(root, labels, engines, intents, reps, pattern) -> None`.

Definitions follow METRICS 0.2.0 exactly: effective status from stored Run.status (the runner applied the all-failed rule) or recomputed on replay; activation over ok runs; headline rates over activated runs of unbranded intents with per-intent means; read = owned URL in consulted; cited = owned URL in cited; FootnoteOne = first citation (rank 1) owned; unconditional cited over ok runs with known activation; conversion suppressed under 20; placebo floor over placebo intents; noise floor pairs; replay when `run.raw_sha256` exists and `run.parser_version != adapters[provider].version` (re-parse the stored blob, derive consulted and cited from the fresh Observation, canonicalize with the current rules); otherwise sources from `sources.jsonl` with `canonical_url` recomputed as `canonicalize(url)`; sources whose run id has no run record are ignored; an engine or window with nothing to count yields `MetricValue.no_data`.

- [ ] **Step 1: Test helper and tests**

```python
# tests/helpers.py
"""Build a synthetic .footnote store for metrics, report and diff tests. Deterministic; no network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from footnoteone.canon import canonicalize
from footnoteone.design import enumerate_calls
from footnoteone.schema import EngineConfig, Intent, Manifest, Run, SourceRecord
from footnoteone.store import JsonlStore

OWN = "https://example.org/guide"
OTHER = "https://other.net/page"


def build_store(root: Path, labels: list[str], engines: list[EngineConfig], intents: list[Intent], reps: int, pattern, start=datetime(2026, 10, 1, tzinfo=UTC)) -> list[Manifest]:
    """pattern(label, intent, prompt, engine, rep) -> (status, activated, consulted_urls, cited_urls) or None for no record."""
    store = JsonlStore(root / ".footnote")
    manifests = []
    for day, label in enumerate(labels):
        t = start + timedelta(days=7 * day)
        m = Manifest(code_version="0.0.1", adapter_versions={e.provider: "fake@1" for e in engines}, config_sha="c" * 64,
                     price_table_version="2026-10-05", budget_usd=10.0, label=label, engines=engines, intents=intents,
                     canon_version=2, paraphrases=max(len(i.prompts) for i in intents), reps=reps, started_at=t, status="done", finished_at=t)
        store.append("manifests", m)
        manifests.append(m)
        for call in enumerate_calls(intents, engines, reps):
            got = pattern(label, call.intent, call.prompt, call.engine, call.rep_idx)
            if got is None:
                continue
            status, activated, consulted, cited = got
            key = call.key(label)
            for role, urls in (("consulted", consulted), ("cited", cited)):
                for rank, url in enumerate(urls, start=1):
                    store.append("sources", SourceRecord(run_id=key, role=role, url=url, canonical_url=canonicalize(url), rank=rank, provider_field="f"))
            store.append("runs", Run(id=key, manifest_id=m.id, intent_id=call.intent.id, prompt_id=call.prompt.id, engine_config_id=call.engine.config_sha,
                                     rep_idx=call.rep_idx, status=status, model_requested=call.engine.model_requested, activated=activated,
                                     activation_evidence="synthetic", raw_sha256=None, parser_version="fake@1", search_calls=1 if activated == "yes" else 0,
                                     cost_usd=0.01, started_at=t, finished_at=t))
    return manifests
```

```python
# tests/test_metrics.py
import json
from pathlib import Path

import pytest

from footnoteone.adapters.base import RawResponse
from footnoteone.adapters.openai import OpenAIAdapter
from footnoteone.canon import OwnedSet
from footnoteone.config import ProjectConfig
from footnoteone.metrics import MetricValue, compute, effective_view_status, load_runs, select_manifests, t_value, wilson_value
from footnoteone.schema import EngineConfig, Intent, Prompt, Run
from footnoteone.store import JsonlStore, RawStore
from helpers import OTHER, OWN, build_store

E1 = EngineConfig(provider="openai", model_requested="gpt-5-mini")
E2 = EngineConfig(provider="anthropic", model_requested="claude-sonnet-4-5")
CFG = ProjectConfig.model_validate({"site": {"url": "https://example.org"}, "engines": [{"provider": "openai", "model": "gpt-5-mini"}], "design": {"budget_usd_per_burst": 1.0}})


def intents():
    def mk(prefix, n, kind):
        return [Intent(id=f"{prefix}{k}", label=f"{prefix}{k}", kind=kind, prompts=[Prompt(id=f"{prefix}{k}p{j}", text="q", paraphrase_idx=j) for j in range(2)]) for k in range(n)]
    return mk("u", 4, "unbranded") + mk("b", 1, "branded") + mk("p", 1, "placebo")


def pattern_cited_half(label, intent, prompt, engine, rep):
    """Unbranded intents u0 and u1 cite the owned page in every run; u2 and u3 never; branded always; placebo never.
    Every run consulted the owned page; rep 1 of u3 is an error; engine E2 answers from memory (activated no)."""
    if engine == E2:
        return ("ok", "no", [], [])
    if intent.id == "u3" and rep == 1:
        return ("error", "unknown", [], [])
    cites = intent.id in ("u0", "u1", "b0")
    return ("ok", "yes", ["http://www.example.org/guide/", OTHER], [OWN, OTHER] if cites else [OTHER])


def test_wilson_and_t_values_carry_denominators():
    v = wilson_value(3, 10, excluded={"unknown": 2})
    assert (v.numerator, v.denominator, v.excluded, v.method) == (3, 10, {"unknown": 2}, "Wilson 95%") and 0.1 < v.lo < 0.3 < v.hi < 0.7
    assert wilson_value(0, 0).value is None and wilson_value(0, 0).note == "no data"
    t = t_value({"a": [1, 1], "b": [0, 0], "c": [1, 0]})
    assert t.value == pytest.approx(0.5) and t.n_intents == 3 and t.numerator == 3 and t.denominator == 6 and "bootstrap" in t.note
    assert t_value({"a": [1.0]}).lo is None and t_value({}).value is None


def test_compute_funnel_matches_hand_counts(tmp_path):
    build_store(tmp_path, ["w1"], [E1, E2], intents(), reps=2, pattern=pattern_cited_half)
    report = compute(tmp_path, CFG, intents(), [E1, E2], pages=[], adapters={}, labels=["w1"])
    by = {e.surface: e for e in report.engines}
    e1 = by["api:openai"]
    # 6 intents x 2 prompts x 2 reps = 24 runs; 2 errors (u3 rep 1 on both prompts)
    assert e1.runs_total == 24 and e1.failures == {"error": 2}
    assert (e1.activation.numerator, e1.activation.denominator) == (22, 22) and e1.unknown_activation == 0
    # headline: unbranded activated runs = u0,u1,u2 (4 each) + u3 (2) = 14; cited in u0,u1 = 8
    assert (e1.cited.numerator, e1.cited.denominator, e1.cited.n_intents) == (8, 14, 4)
    assert e1.cited.value == pytest.approx((1 + 1 + 0 + 0) / 4)
    assert e1.read.value == pytest.approx(1.0) and e1.footnote_one.value == pytest.approx(0.5)
    assert (e1.unconditional_cited.numerator, e1.unconditional_cited.denominator) == (8 + 4, 22)  # branded runs cite too
    assert e1.conversion.value is None and "suppressed" in e1.conversion.note  # 14 < 20
    assert e1.placebo_floor.value == pytest.approx(0.0)
    assert [r.canonical_url for r in e1.read_never_cited] == []  # the owned page is cited by u0 and u1
    assert e1.cited_instead and e1.cited_instead[0].host == "other.net"
    assert e1.noise.flip_rate.denominator == 6 and e1.noise.flip_rate.numerator == 0  # u0, u1, u2: 2 pairs each; u3 has no pair
    e2 = by["api:anthropic"]
    assert e2.activation.value == 0.0 and e2.cited.value is None and e2.cited.note == "no data"


def test_last_record_per_run_id_wins(tmp_path):
    build_store(tmp_path, ["w1"], [E1], intents()[:1], reps=1, pattern=lambda *a: ("error", "unknown", [], []))
    store = JsonlStore(tmp_path / ".footnote")
    runs = list(store.iter("runs", Run))
    store.append("runs", runs[0].model_copy(update={"status": "ok", "activated": "yes"}))
    assert load_runs(store)[runs[0].id].status == "ok"


def test_select_manifests_by_label_and_date(tmp_path):
    ms = build_store(tmp_path, ["w1", "w2", "w3"], [E1], intents()[:1], reps=1, pattern=lambda *a: ("ok", "yes", [], []))
    assert [m.label for m in select_manifests(ms, labels=["w2"])] == ["w2"]
    assert [m.label for m in select_manifests(ms, since=ms[1].started_at)] == ["w2", "w3"]
    assert [m.label for m in select_manifests(ms, until=ms[1].started_at)] == ["w1", "w2"]


def test_replay_uses_current_parser_when_version_differs(tmp_path):
    build_store(tmp_path, ["w1"], [E1], intents()[:1], reps=1, pattern=lambda *a: ("ok", "no", [], []))
    fixture = json.loads((Path(__file__).parent / "fixtures" / "openai_web_search_searched.json").read_text())
    fixture.pop("_doc_note", None)
    raw = RawResponse.model_validate(fixture)
    store, raw_store = JsonlStore(tmp_path / ".footnote"), RawStore(tmp_path / ".footnote")
    sha = raw_store.put(raw.as_blob())
    run = list(store.iter("runs", Run))[0]
    store.append("runs", run.model_copy(update={"raw_sha256": sha, "parser_version": "openai@0.0.0", "activated": "no"}))
    report = compute(tmp_path, CFG, intents()[:1], [E1], pages=[], adapters={"openai": OpenAIAdapter()}, labels=["w1"])
    e1 = report.engines[0]
    assert e1.replayed == 1 and e1.activation.numerator == 1  # the fixture searched, whatever the stale record said
    assert e1.read.value == pytest.approx(1.0)  # the fixture consulted https://example.org/..., which is owned


def test_effective_view_status_all_failed():
    assert effective_view_status("ok", 2, 2) == "error" and effective_view_status("ok", 2, 1) == "ok" and effective_view_status("ok", 0, 0) == "ok"
```

- [ ] **Step 2: Run to verify failure, then write metrics.py**

Write `metrics.py` with these dataclasses and functions; the shapes below are binding, the bodies follow the definitions above.

```python
@dataclass
class MetricValue:
    value: float | None
    lo: float | None
    hi: float | None
    numerator: float | None
    denominator: int | None
    excluded: dict[str, int]
    n_intents: int | None
    method: str
    note: str = ""

    @classmethod
    def no_data(cls, method: str, note: str = "no data") -> MetricValue: ...

def wilson_value(k: int, n: int, excluded: dict[str, int] | None = None, method: str = "Wilson 95%") -> MetricValue
def t_value(groups: dict[str, list[float]], excluded=None, method: str = "Student t over intents") -> MetricValue
def effective_view_status(status: RunStatus, search_calls: int, failed_searches: int) -> RunStatus  # effective_status(...)[0]

@dataclass
class RunView:
    run: Run; label: str; engine: EngineConfig; intent: Intent | None; status: RunStatus; activated: Activation
    consulted: set[str]; cited: list[str]; owned_consulted: bool; owned_cited: bool; owned_first: bool; replayed: bool

@dataclass
class IntentRow: intent_id: str; label: str; kind: str; runs_ok: int; activated: int; read: MetricValue; cited: MetricValue; footnote_one: MetricValue; indistinguishable_from_placebo: bool
@dataclass
class PageRow: canonical_url: str; consulted_runs: int; cited_runs: int
@dataclass
class HostRow: host: str; cited_runs: int; share: float
@dataclass
class NoiseFloor: within_burst_jaccard: MetricValue; between_burst_jaccard: MetricValue; flip_rate: MetricValue
@dataclass
class EngineMetrics:
    engine: EngineConfig; surface: str; tool_version: str | None; runs_total: int; failures: dict[str, int]
    activation: MetricValue; unknown_activation: int; read: MetricValue; cited: MetricValue; footnote_one: MetricValue
    unconditional_cited: MetricValue; conversion: MetricValue; placebo_floor: MetricValue; noise: NoiseFloor
    per_intent: list[IntentRow]; read_never_cited: list[PageRow]; cited_instead: list[HostRow]; replayed: int
@dataclass
class Report:
    generated_at: datetime; labels: list[str]; manifests: list[Manifest]; engines: list[EngineMetrics]
    canon_version: int; intents_total: int; owned_domains: list[str]; note: str
```

Rules for the bodies: `load_*` take the last record per id; `select_manifests` filters by label membership and by `started_at` bounds inclusive, sorted by `started_at`; `run_views` joins runs to their manifest's engines (by `engine_config_id`) and intents (by id), keeps only runs of the selected manifests, replays when `adapters.get(provider)` exists, the raw blob exists and the version differs (consulted = set of canonicalized consulted URLs, cited = canonicalized cited URLs in rank order, status via `effective_view_status`), otherwise takes sources from `sources.jsonl` with `canonicalize(source.url)`, keeping only the sources whose `raw_sha256` equals the run's last record's `raw_sha256` (both None counts as equal; `SourceRecord.raw_sha256` was added by Task 6 so a resumed or crashed attempt's sources never join the final run); `owned_*` flags use `classify_owner(url, owned) != "other"`; `compute` groups views by engine config sha and builds `EngineMetrics` per engine in `engines` order (an engine with no views gets `no_data` everywhere and `runs_total 0`); per-intent `t_value` groups are keyed by intent id with one 0 or 1 per activated run; `noise` pairs: within-burst = all pairs of distinct reps with the same (label, intent, prompt), between-burst = pairs with the same (intent, prompt) and different labels, Jaccard skipped when both cited sets are empty, flip = pairs whose `owned_cited` differ, Wilson over all pairs; `read_never_cited` lists owned canonical URLs consulted in activated headline runs with zero cited runs, by consulted count descending, top 20; `cited_instead` counts, per host, the headline activated runs that cited that host while not citing an owned URL, `share` = that count / headline activated runs with any citation, top 10; `placebo_floor` = `t_value` over placebo intents; `indistinguishable_from_placebo` is true when an unbranded intent's cited interval overlaps the placebo floor interval (false when either is no data); `owned` comes from `library.owned_set_for(config, pages)`.

- [ ] **Step 3: Run the metrics tests (add `tests/conftest.py` with `sys.path` insertion only if `from helpers import` fails under pytest's rootdir; pytest's default rootdir conftest-less import works when `tests/` has no `__init__.py`), the suite, ruff; commit**

```bash
git add src/footnoteone/metrics.py tests/helpers.py tests/test_metrics.py
git commit -s -m "feat: funnel metrics with denominators, noise floor, placebo floor and parser replay"
```

---

### Task 8: crawl-check and doctor commands

**Files:**
- Create: `src/footnoteone/commands/__init__.py` (docstring only), `src/footnoteone/commands/common.py`, `src/footnoteone/commands/crawl_check.py`, `src/footnoteone/commands/doctor.py`
- Test: `tests/test_cmd_crawl_check.py`, `tests/test_cmd_doctor.py`

**Interfaces:**
- Consumes: `config.load_config`, `config.load_intents`, `config.engine_configs`, `config.ConfigError`, `library.read_pages`, `audit.probe.access_matrix`, `audit.robots.load_bots`, `pricing.PriceTable`, `planning.*`, `runner.keys_from_env`, `runner.default_adapters`, `store.JsonlStore`, `schema.Run`.
- Produces: `commands.common.RootOption` (an `Annotated[Path, typer.Option("--root", help=...)]` defaulting to `Path(".")`), `commands.common.fail(message) -> NoReturn` (prints to stderr, `raise typer.Exit(2)`), `commands.common.load_project(root) -> tuple[ProjectConfig, list[Intent]]` (ConfigError becomes `fail`); `commands.crawl_check.register(app)` adding `crawl-check`; `commands.doctor.register(app)` adding `doctor`. Every command module exposes only `register(app: typer.Typer) -> None`.

CLI conventions for every command in this plan: `--root PATH` (default `.`); output through `typer.echo`; configuration problems exit with code 2 and the `ConfigError` text on stderr; nothing is printed that came from an environment variable's value; no em dashes; plain words first, numbers after.

crawl-check: `footnote crawl-check [--root .] [--path /p ...] [--no-probe]`. Paths default to `/` plus the paths of the first ten library pages on the site host. Creates `httpx.AsyncClient(max_redirects=5)`, runs `access_matrix(client, config.site.url, paths, probe=not no_probe)` and prints: one header line (`robots.txt: HTTP 200, success` or the error and access class), then a table with columns bot, purpose, path, robots verdict (`allowed` or `blocked`), matched rule, probe (`200`, `403`, `not probed: control token`, or the error), and a last line: "Probe = what a request carrying that user-agent string from this machine receives; bot-verifying firewalls may answer the real bot differently. Unreachable robots.txt means complete disallow (RFC 9309)." Exit 0 even when everything is blocked; exit 2 on a ConfigError or invalid path.

doctor: `footnote doctor [--root .]` prints one line per check prefixed `ok`, `warn` or `fail`, and exits 1 when any check fails: footnote.toml loads; intents.yaml loads (count by kind); pages library present (count) or warn "run footnote init"; for each configured provider, the key env var is set or missing (print the variable name only); pricing.yaml version and its age in days (warn over 90 days); planning.yaml version; each engine priced (fail when not); budget versus the worst case for one burst (warn when below); unbranded intents versus `min_shared_intents` (warn); store layout (`.footnote/` present, counts of manifests, runs, sources, raw blobs); runs whose parser_version differs from the current adapter version (info: "N runs will be replayed with the current parser"). Age is computed from `PriceTable.version` parsed as a date against today (injectable `today` parameter on the check function for tests).

- [ ] **Step 1: Tests**

```python
# tests/test_cmd_crawl_check.py
import httpx
import typer
from typer.testing import CliRunner

from footnoteone.commands.crawl_check import register
from footnoteone.config import write_templates


def app_with_command():
    app = typer.Typer()
    register(app)
    return app


def test_crawl_check_prints_matrix_without_probes(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    httpx_mock.add_response(url="https://example.org/robots.txt", text="User-agent: GPTBot\nDisallow: /\n")
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--no-probe"])
    assert result.exit_code == 0, result.output
    assert "robots.txt: HTTP 200" in result.output and "GPTBot" in result.output and "blocked" in result.output
    assert "Disallow: / (line 2)" in result.output and "not probed" in result.output
    assert "OAI-SearchBot" in result.output and "allowed" in result.output
    assert "\u2014" not in result.output


def test_crawl_check_probes_given_paths(tmp_path, httpx_mock):
    write_templates(tmp_path, "https://example.org")
    httpx_mock.add_response(url="https://example.org/robots.txt", status_code=404)
    httpx_mock.add_response(url="https://example.org/p", status_code=403, match_headers={"User-Agent": "Mozilla/5.0 (compatible; GPTBot; +https://developers.openai.com/api/docs/bots)"})
    httpx_mock.add_response(url="https://example.org/p", status_code=200)  # every other crawling bot
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--path", "/p"])
    assert result.exit_code == 0, result.output
    assert "unavailable" in result.output and "403" in result.output and "200" in result.output


def test_crawl_check_without_config_exits_2(tmp_path):
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path)])
    assert result.exit_code == 2 and "footnote.toml" in (result.output + str(result.exception or ""))
```

For the second test, register the 200 response with `is_reusable=True` (pytest-httpx) so every crawling bot except GPTBot can consume it; keep the GPTBot-specific 403 registered first.

```python
# tests/test_cmd_doctor.py
from datetime import date

import typer
from typer.testing import CliRunner

from footnoteone.commands.doctor import checks, register
from footnoteone.config import write_templates


def test_doctor_reports_each_check(tmp_path, monkeypatch):
    write_templates(tmp_path, "https://example.org")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    lines = checks(tmp_path, today=date(2026, 10, 10))
    text = "\n".join(f"{level} {msg}" for level, msg in lines)
    assert "ok footnote.toml" in text and "ok intents.yaml" in text and "2 intents" in text
    assert "ok key OPENAI_API_KEY set" in text and "warn key ANTHROPIC_API_KEY missing" in text
    assert "sk-secret-value" not in text
    assert "pricing.yaml 2026-10-05" in text and "planning.yaml" in text
    assert "warn" in text and "pages" in text  # no library yet
    assert all(level in ("ok", "warn", "fail") for level, _ in lines)


def test_doctor_fails_on_unpriced_engine_and_old_prices(tmp_path):
    write_templates(tmp_path, "https://example.org")
    toml = (tmp_path / "footnote.toml").read_text().replace('model = "gpt-5-mini"', 'model = "gpt-999"')
    (tmp_path / "footnote.toml").write_text(toml)
    lines = checks(tmp_path, today=date(2027, 6, 1))
    text = "\n".join(f"{level} {msg}" for level, msg in lines)
    assert "fail" in text and "gpt-999" in text and "not priced" in text
    assert "warn pricing.yaml" in text and "days old" in text
    app = typer.Typer()
    register(app)
    result = CliRunner().invoke(app, ["--root", str(tmp_path)])
    assert result.exit_code == 1 and "fail" in result.output


def test_doctor_without_config_is_a_fail_line(tmp_path):
    lines = checks(tmp_path)
    assert lines[0][0] == "fail" and "footnote.toml" in lines[0][1]
```

- [ ] **Step 2: Run to verify failure, then write the modules**

`common.py`: `RootOption`, `fail`, `load_project`. `crawl_check.py`: `register(app)` defines `crawl_check(root: RootOption, path: list[str] | None = typer.Option(None, "--path"), no_probe: bool = typer.Option(False, "--no-probe"))` named `crawl-check`, which loads the config (ConfigError -> fail), picks paths (given, else `/` plus library page paths whose host matches the site host, at most ten), runs `asyncio.run(access_matrix(...))` inside `async with httpx.AsyncClient(max_redirects=5)`, prints the header, the table (`str.ljust` columns; rules truncated at 60 characters) and the footer. `doctor.py`: `checks(root, today=None) -> list[tuple[str, str]]` runs every check in the order listed above, and `register(app)` defines `doctor(root: RootOption)` that prints each line as `f"{level:4} {message}"` and exits 1 when any level is `fail`. Pages come from `library.read_pages`; run counts and parser drift from `JsonlStore(root / ".footnote").iter("runs", Run)` compared with `default_adapters()` versions.

- [ ] **Step 3: Run both test files, the suite, ruff; commit**

```bash
git add src/footnoteone/commands tests/test_cmd_crawl_check.py tests/test_cmd_doctor.py
git commit -s -m "feat: crawl-check and doctor commands"
```

---

### Task 9: Report rendering and the report command

**Files:**
- Create: `src/footnoteone/report.py`, `src/footnoteone/templates/report.md.j2`, `src/footnoteone/templates/report.html.j2`, `src/footnoteone/commands/report.py`
- Test: `tests/test_report.py`, `tests/test_cmd_report.py`

**Interfaces:**
- Consumes: `metrics.Report`, `metrics.EngineMetrics`, `metrics.MetricValue`, `metrics.compute`, `diff.DiffReport` (optional argument typed as `object | None` until Task 10 merges; the template only reads `diff.engines` and `diff.verdict_lines` when present), `library.read_pages`, `runner.default_adapters`, `config.*`, `commands.common.*`.
- Produces: `report.fmt_pct(mv: MetricValue) -> str` (`"32% (18 to 46)"`, `"no data"`, `"suppressed: <note>"`, `"not exposed"`); `report.fmt_count(mv) -> str` (`"8 of 14 runs, 4 intents"`); `report.render_markdown(report, diff=None) -> str`; `report.render_html(report, diff=None) -> str`; `report.write_report(root, report, diff=None, out_dir=None) -> tuple[Path, Path]` writing `reports/<first label or generated date>/report.md` and `report.html`; `commands.report.register(app)` adding `report` with `--root`, repeatable `--label`, `--since`, `--until` (ISO dates), `--out`.

Required content, asserted by tests: title "FootnoteOne report"; the line "API answers, not the consumer apps"; generated time, labels, canonical rule version; per engine a heading with surface, model and tool version; a funnel table with rows activation, read, cited, FootnoteOne, unconditional cited, conversion, each with `fmt_pct` and `fmt_count`; failures by reason; unknown activation count; placebo floor; noise floor (within-burst Jaccard, between-burst Jaccard, flip rate); the per-intent table (intent, kind, activated runs, read, cited, FootnoteOne, "indistinguishable from placebo" flag); "Read but never cited" list; "Cited instead" hosts with shares; replayed count with the sentence "re-read with the current parser"; an engine with no runs prints "no data for this engine"; the diff section when a diff is given (its verdict lines verbatim); in HTML every interval, numerator and method sits inside a `<details><summary>statistics</summary>` block beside the plain-word value; Markdown puts them after the value in parentheses. No em dashes. The HTML is a single self-contained file (inline CSS, no external assets, no scripts).

- [ ] **Step 1: Tests**

```python
# tests/test_report.py
from footnoteone.config import ProjectConfig
from footnoteone.metrics import MetricValue, compute
from footnoteone.report import fmt_count, fmt_pct, render_html, render_markdown, write_report
from footnoteone.schema import EngineConfig, Intent, Prompt
from helpers import OTHER, OWN, build_store

E1 = EngineConfig(provider="openai", model_requested="gpt-5-mini")
E2 = EngineConfig(provider="anthropic", model_requested="claude-sonnet-4-5")
CFG = ProjectConfig.model_validate({"site": {"url": "https://example.org"}, "engines": [{"provider": "openai", "model": "gpt-5-mini"}], "design": {"budget_usd_per_burst": 1.0}})


def intents():
    return [Intent(id=f"u{k}", label=f"topic {k}", prompts=[Prompt(id=f"u{k}p0", text="q")]) for k in range(8)] + [
        Intent(id="p0", label="placebo", kind="placebo", prompts=[Prompt(id="p0p0", text="q")])]


def pattern(label, intent, prompt, engine, rep):
    if engine == E2:
        return None
    cites = intent.id in ("u0", "u1", "u2")
    return ("ok", "yes", [OWN, OTHER], [OWN] if cites else [OTHER])


def test_formatters():
    assert fmt_pct(MetricValue(0.32, 0.18, 0.46, 8, 25, {}, 5, "Student t over intents")) == "32% (18 to 46)"
    assert fmt_pct(MetricValue.no_data("Wilson 95%")) == "no data"
    assert fmt_pct(MetricValue(None, None, None, 3, 10, {}, None, "Wilson 95%", note="suppressed: fewer than 20 runs read an owned page")).startswith("suppressed")
    assert fmt_count(MetricValue(0.32, 0.18, 0.46, 8, 25, {}, 5, "m")) == "8 of 25 runs, 5 intents"


def test_markdown_and_html_contain_required_sections(tmp_path):
    build_store(tmp_path, ["w1"], [E1, E2], intents(), reps=2, pattern=pattern)
    report = compute(tmp_path, CFG, intents(), [E1, E2], pages=[], adapters={}, labels=["w1"])
    md, html = render_markdown(report), render_html(report)
    for text in (md, html):
        assert "FootnoteOne report" in text and "API answers, not the consumer apps" in text
        assert "api:openai" in text and "gpt-5-mini" in text and "api:anthropic" in text
        assert "no data for this engine" in text
        assert "Read but never cited" in text and "Cited instead" in text and "other.net" in text
        assert "Placebo floor" in text and "Noise floor" in text and "flip rate" in text.lower()
        assert "topic 0" in text and "indistinguishable" in text
        assert "\u2014" not in text and "0%" not in text.replace("100%", "").replace("10%", "").replace("20%", "").replace("30%", "").replace("40%", "").replace("50%", "").replace("60%", "").replace("70%", "").replace("80%", "").replace("90%", "")
    assert "<details>" in html and "statistics" in html and "<script" not in html and "http" not in html.split("<style")[1].split("</style>")[0]
    assert "(" in md and "intents" in md


def test_write_report_paths(tmp_path):
    build_store(tmp_path, ["w1"], [E1], intents(), reps=1, pattern=pattern)
    report = compute(tmp_path, CFG, intents(), [E1], pages=[], adapters={}, labels=["w1"])
    md_path, html_path = write_report(tmp_path, report)
    assert md_path == tmp_path / "reports" / "w1" / "report.md" and html_path.exists()
```

(The long `"0%"` assertion checks that no bare zero percentage appears for empty denominators; replace it with a regex `re.search(r"(?<!\d)0% \(", text) is None` if simpler.)

```python
# tests/test_cmd_report.py
import typer
from typer.testing import CliRunner

from footnoteone.commands.report import register
from footnoteone.config import write_templates
from footnoteone.schema import EngineConfig, Intent, Prompt
from helpers import OWN, build_store


def test_report_command_writes_files(tmp_path):
    write_templates(tmp_path, "https://example.org")
    engine = EngineConfig(provider="openai", model_requested="gpt-5-mini", params={"max_output_tokens": 1200, "max_tool_calls": 3, "force_search": True})
    intents = [Intent(id="example-topic", label="x", prompts=[Prompt(id="p", text="q")])]
    build_store(tmp_path, ["w1"], [engine], intents, reps=1, pattern=lambda *a: ("ok", "yes", [OWN], [OWN]))
    app = typer.Typer()
    register(app)
    result = CliRunner().invoke(app, ["--root", str(tmp_path), "--label", "w1"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "reports" / "w1" / "report.html").exists() and "report.md" in result.output
```

- [ ] **Step 2: Run to verify failure, then write report.py, the templates and the command**

`report.py` builds a `jinja2.Environment(loader=PackageLoader("footnoteone", "templates"), autoescape=select_autoescape(["html", "j2"]), trim_blocks=True, lstrip_blocks=True)` and registers `fmt_pct`, `fmt_count` and a `pts` filter (difference in points); `render_markdown` renders `report.md.j2` with `autoescape` off for that template; `write_report` names the directory after `report.labels[0]` when there is exactly one label, else the generated date `YYYY-MM-DD`. The Markdown template is a plain-text layout with one `##` per engine; the HTML template carries inline CSS only (a readable serif body, monospace numbers, a muted color for statistics), the same sections, and wraps each value's statistics in `<details><summary>statistics</summary>method, numerator of denominator, intents, interval</details>`. `commands/report.py` registers `report(root: RootOption, label: Annotated[list[str] | None, typer.Option("--label")] = None, since: Annotated[str | None, typer.Option("--since")] = None, until: Annotated[str | None, typer.Option("--until")] = None, out: Annotated[Path | None, typer.Option("--out")] = None)` (Annotated options, as Task 8's commands do), parses dates with `datetime.date.fromisoformat` into aware midnight UTC datetimes, loads config, intents, pages and engines (`engine_configs(config, PlanningAssumptions.load())`), computes, writes, and echoes both paths.

- [ ] **Step 3: Run both test files, the suite, ruff; commit**

```bash
git add src/footnoteone/report.py src/footnoteone/templates src/footnoteone/commands/report.py tests/test_report.py tests/test_cmd_report.py
git commit -s -m "feat: Markdown and HTML report with statistics one click behind every value"
```

---

### Task 10: The before/after diff

**Files:**
- Create: `src/footnoteone/diff.py`, `src/footnoteone/commands/diff.py`
- Test: `tests/test_diff.py`, `tests/test_cmd_diff.py`

**Interfaces:**
- Consumes: `metrics.run_views`, `metrics.load_manifests`, `metrics.select_manifests`, `metrics.RunView`, `library.owned_set_for`, `stats.paired_sign_flip_p`, `stats.holm`, `stats.t_interval`, `stats.verdict`, `plan.intents_needed`, `plan.planning_baseline`, `config.ProjectConfig`.
- Produces: `diff.EngineDiff(engine, surface, shared_intents, before_rate, after_rate, mean_diff, diff_lo, diff_hi, p_raw, p_adj, verdict, intents_needed, note, per_intent: list[tuple[str, float, float]])`; `diff.DiffReport(before_labels, after_labels, engines, min_shared_intents, generated_at, verdict_lines: list[str])`; `diff.intent_rates(views) -> dict[str, float]` (unbranded intents only: owned-cited activated ok runs / activated ok runs; intents with no activated run omitted); `diff.compute_diff(root, config, intents, engines, pages, adapters, before: list[str], after: list[str]) -> DiffReport` (labels select manifests); `diff.render_diff_markdown(d) -> str`; `commands.diff.register(app)` adding `diff` with repeatable `--before` and `--after` labels.

Rules (METRICS 0.2.0): per engine, shared intents = ids present in both windows' rate maps; differences = after minus before per shared intent; `p_raw = paired_sign_flip_p(diffs)`; Holm is applied across the engines that reach `min_shared_intents` (an engine below it is `insufficient` and outside the family); interval = `t_interval(diffs)` (None when fewer than 2 diffs); `verdict(lo, hi, p_adj, k, min_intents=config.design.min_shared_intents)`; when the interval is None and k >= min, the verdict is `cant_tell`; `intents_needed = plan.intents_needed(before_rate, m, icc, family_size, min_intents=...)` with `m = plan.answers_per_intent(intents, config.design.reps, config.design.paraphrases)` (the harmonic mean of the unbranded intents' answers per window, which Task 5 added so the planner and the diff agree) and `before_rate` the mean of the before rates over shared intents (planning prior applied inside); engines are named by surface, model and the first 8 characters of the config sha; `verdict_lines` are the plain-word sentences the report shows, one per engine:
- Moved: "{surface} {model}: Moved. Cited rate went from {b}% to {a}% across {k} shared intents (mean change {d:+} points, 95% interval {lo:+} to {hi:+}; Holm-adjusted p {p})."
- No change: "...: No change. The 95% interval of the change, {lo:+} to {hi:+} points, lies inside plus or minus 10 points across {k} shared intents."
- Can't tell yet: "...: Can't tell yet. Mean change {d:+} points, 95% interval {lo:+} to {hi:+}, across {k} shared intents; about {n} intents would be needed to see a 10-point move at this design." (or "the interval has no width" when lo == hi, or "fewer than 2 shared intents have spread" when the interval is None)
- Insufficient: "...: Not enough shared intents ({k} of {min}); no verdict."

- [ ] **Step 1: Tests**

```python
# tests/test_diff.py
import pytest

from footnoteone.config import ProjectConfig
from footnoteone.diff import compute_diff, render_diff_markdown
from footnoteone.schema import EngineConfig, Intent, Prompt
from footnoteone.stats import paired_sign_flip_p, t_interval
from helpers import OTHER, OWN, build_store

E1 = EngineConfig(provider="openai", model_requested="gpt-5-mini")
CFG = ProjectConfig.model_validate({"site": {"url": "https://example.org"}, "engines": [{"provider": "openai", "model": "gpt-5-mini"}], "design": {"paraphrases": 1, "reps": 1, "budget_usd_per_burst": 1.0}})


def intents(n=10):
    return [Intent(id=f"u{k}", label=f"u{k}", prompts=[Prompt(id=f"u{k}p0", text="q")]) for k in range(n)]


def pattern_before_after(label, intent, prompt, engine, rep):
    k = int(intent.id[1:])
    cites = k < 2 if label == "w1" else k < 6
    return ("ok", "yes", [OWN], [OWN] if cites else [OTHER])


def test_diff_matches_stats_and_reads_cant_tell(tmp_path):
    build_store(tmp_path, ["w1", "w2"], [E1], intents(), reps=1, pattern=pattern_before_after)
    d = compute_diff(tmp_path, CFG, intents(), [E1], pages=[], adapters={}, before=["w1"], after=["w2"])
    [e] = d.engines
    diffs = [1.0] * 4 + [0.0] * 6  # u2..u5 gained a citation; the others are unchanged
    assert e.shared_intents == 10 and e.before_rate == pytest.approx(0.2) and e.after_rate == pytest.approx(0.6)
    assert e.p_raw == pytest.approx(paired_sign_flip_p(diffs)) and e.p_adj == pytest.approx(e.p_raw)  # one engine: Holm is identity
    mean, lo, hi = t_interval(diffs)
    assert (e.mean_diff, e.diff_lo, e.diff_hi) == pytest.approx((mean, lo, hi))
    assert e.verdict == "cant_tell" and e.intents_needed is not None and e.intents_needed > 10
    assert d.verdict_lines[0].startswith("api:openai gpt-5-mini") and "Can't tell yet" in d.verdict_lines[0]
    assert "+40" in d.verdict_lines[0] and "10 shared intents" in d.verdict_lines[0]


def test_diff_insufficient_below_minimum_and_ignores_unshared_intents(tmp_path):
    def pattern(label, intent, prompt, engine, rep):
        if label == "w1" and intent.id == "u6":
            return None  # u6 did not exist in the first window
        return pattern_before_after(label, intent, prompt, engine, rep)
    build_store(tmp_path, ["w1", "w2"], [E1], intents(7), reps=1, pattern=pattern)
    d = compute_diff(tmp_path, CFG, intents(7), [E1], pages=[], adapters={}, before=["w1"], after=["w2"])
    [e] = d.engines
    assert e.shared_intents == 6 and e.verdict == "insufficient" and "Not enough shared intents (6 of 8)" in d.verdict_lines[0]


def test_diff_moved_when_every_intent_gains(tmp_path):
    def pattern(label, intent, prompt, engine, rep):
        return ("ok", "yes", [OWN], [OWN] if label == "w2" else [OTHER])
    build_store(tmp_path, ["w1", "w2"], [E1], intents(10), reps=1, pattern=pattern)
    d = compute_diff(tmp_path, CFG, intents(10), [E1], pages=[], adapters={}, before=["w1"], after=["w2"])
    [e] = d.engines
    assert e.p_raw == pytest.approx(2 / 1024) and e.verdict == "moved" and "Moved" in d.verdict_lines[0]
    assert "\u2014" not in render_diff_markdown(d)


def test_diff_no_change_when_rates_are_stable_with_spread(tmp_path):
    def pattern(label, intent, prompt, engine, rep):
        k = int(intent.id[1:])
        cites = (k % 2 == 0) if rep == 0 else (k % 2 == 1)  # each intent cites in exactly one of two reps, both windows
        return ("ok", "yes", [OWN], [OWN] if cites else [OTHER])
    cfg = CFG.model_copy(update={"design": CFG.design.model_copy(update={"reps": 2})})
    build_store(tmp_path, ["w1", "w2"], [E1], intents(10), reps=2, pattern=pattern)
    d = compute_diff(tmp_path, cfg, intents(10), [E1], pages=[], adapters={}, before=["w1"], after=["w2"])
    [e] = d.engines
    assert e.mean_diff == pytest.approx(0.0) and e.diff_lo == e.diff_hi == 0.0 and e.verdict == "cant_tell"
    assert "no width" in d.verdict_lines[0]
```

```python
# tests/test_cmd_diff.py
import typer
from typer.testing import CliRunner

from footnoteone.commands.diff import register
from footnoteone.config import write_templates
from footnoteone.schema import EngineConfig, Intent, Prompt
from helpers import OWN, build_store


def test_diff_command_prints_verdicts(tmp_path):
    write_templates(tmp_path, "https://example.org")
    engine = EngineConfig(provider="openai", model_requested="gpt-5-mini", params={"max_output_tokens": 1200, "max_tool_calls": 3, "force_search": True})
    intents = [Intent(id=f"u{k}", label="x", prompts=[Prompt(id=f"u{k}p", text="q")]) for k in range(8)]
    (tmp_path / "intents.yaml").write_text("version: 1\nintents:\n" + "".join(f"  - id: u{k}\n    label: x\n    prompts: [q]\n" for k in range(8)))
    build_store(tmp_path, ["w1", "w2"], [engine], intents, reps=1, pattern=lambda *a: ("ok", "yes", [OWN], [OWN]))
    app = typer.Typer()
    register(app)
    result = CliRunner().invoke(app, ["--root", str(tmp_path), "--before", "w1", "--after", "w2"])
    assert result.exit_code == 0, result.output
    assert "api:openai" in result.output and "shared intents" in result.output
```

Note for the last test: the stored engine must equal the config's engine config (same params) for the diff to find it; `write_templates` configures three engines, but only runs for the OpenAI one exist, so the other two print "no data". Build the engine exactly as `engine_configs(config, PlanningAssumptions.load())[0]` would, or import that function in the test instead of hand-writing params.

- [ ] **Step 2: Run to verify failure, then write diff.py and the command**

```python
# src/footnoteone/diff.py
"""Before and after: did the cited rate move between two windows of bursts? (METRICS 0.2.0 change verdict)"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from footnoteone.adapters.base import Adapter
from footnoteone.config import ProjectConfig
from footnoteone.library import owned_set_for
from footnoteone.metrics import RunView, load_manifests, run_views, select_manifests
from footnoteone.plan import answers_per_intent, intents_needed
from footnoteone.schema import EngineConfig, Intent, Page, utcnow
from footnoteone.stats import holm, paired_sign_flip_p, t_interval, verdict
from footnoteone.store import JsonlStore


@dataclass
class EngineDiff:
    engine: EngineConfig
    surface: str
    shared_intents: int
    before_rate: float | None
    after_rate: float | None
    mean_diff: float | None
    diff_lo: float | None
    diff_hi: float | None
    p_raw: float | None
    p_adj: float | None
    verdict: str
    intents_needed: int | None
    note: str
    per_intent: list[tuple[str, float, float]] = field(default_factory=list)


@dataclass
class DiffReport:
    before_labels: list[str]
    after_labels: list[str]
    engines: list[EngineDiff]
    min_shared_intents: int
    generated_at: datetime
    verdict_lines: list[str] = field(default_factory=list)


def intent_rates(views: list[RunView]) -> dict[str, float]:
    """Per unbranded intent: owned-cited runs / activated ok runs; intents with no activated run are absent."""
    hits: dict[str, list[int]] = defaultdict(list)
    for v in views:
        if v.intent is not None and v.intent.kind == "unbranded" and v.status == "ok" and v.activated == "yes":
            hits[v.intent.id].append(1 if v.owned_cited else 0)
    return {intent_id: sum(h) / len(h) for intent_id, h in hits.items()}


def _name(e: EngineConfig) -> str:
    return f"{e.surface} {e.model_requested}"


def _line(d: EngineDiff, min_intents: int) -> str:
    name = _name(d.engine)
    k = d.shared_intents
    if d.verdict == "insufficient":
        return f"{name}: Not enough shared intents ({k} of {min_intents}); no verdict."
    b, a = (round(100 * x) for x in (d.before_rate or 0.0, d.after_rate or 0.0))
    if d.mean_diff is None or d.diff_lo is None or d.diff_hi is None:
        return f"{name}: Can't tell yet. Fewer than 2 shared intents have spread; cited rate {b}% before and {a}% after across {k} shared intents."
    m, lo, hi = (round(100 * x) for x in (d.mean_diff, d.diff_lo, d.diff_hi))
    span = f"mean change {m:+d} points, 95% interval {lo:+d} to {hi:+d}"
    if d.verdict == "moved":
        return f"{name}: Moved. Cited rate went from {b}% to {a}% across {k} shared intents ({span}; Holm-adjusted p {d.p_adj:.3f})."
    if d.verdict == "no_change":
        return f"{name}: No change. The 95% interval of the change, {lo:+d} to {hi:+d} points, lies inside plus or minus 10 points across {k} shared intents."
    if d.diff_lo == d.diff_hi:
        return f"{name}: Can't tell yet. The interval has no width (every shared intent changed by the same amount, {m:+d} points) across {k} shared intents, which is no evidence of no change."
    need = f"about {d.intents_needed} intents would be needed to see a 10-point move at this design" if d.intents_needed else "a 10-point move is out of reach below 1000 intents at this design"
    return f"{name}: Can't tell yet. {span[0].upper() + span[1:]}, across {k} shared intents; {need}."


def compute_diff(root: Path, config: ProjectConfig, intents: list[Intent], engines: list[EngineConfig], pages: list[Page], adapters: dict[str, Adapter], before: list[str], after: list[str]) -> DiffReport:
    store = JsonlStore(Path(root) / ".footnote")
    manifests = list(load_manifests(store).values())
    owned = owned_set_for(config, pages)
    views_b = run_views(root, adapters, select_manifests(manifests, labels=before), owned)
    views_a = run_views(root, adapters, select_manifests(manifests, labels=after), owned)
    min_k = config.design.min_shared_intents
    m = answers_per_intent(intents, config.design.reps, config.design.paraphrases)
    rows: list[EngineDiff] = []
    for e in engines:
        rb = intent_rates([v for v in views_b if v.engine.config_sha == e.config_sha])
        ra = intent_rates([v for v in views_a if v.engine.config_sha == e.config_sha])
        shared = sorted(set(rb) & set(ra))
        diffs = [ra[i] - rb[i] for i in shared]
        before_rate = sum(rb[i] for i in shared) / len(shared) if shared else None
        after_rate = sum(ra[i] for i in shared) / len(shared) if shared else None
        p_raw = paired_sign_flip_p(diffs) if shared else None
        interval = t_interval(diffs) if len(diffs) >= 2 else None
        rows.append(EngineDiff(e, e.surface, len(shared), before_rate, after_rate, *(interval or (None, None, None)), p_raw, None, "insufficient", None, "", [(i, rb[i], ra[i]) for i in shared]))
    family = [r for r in rows if r.shared_intents >= min_k]
    adjusted = holm([r.p_raw for r in family]) if family else []
    for r, p_adj in zip(family, adjusted, strict=True):
        r.p_adj = p_adj
        if r.diff_lo is None or r.diff_hi is None:
            r.verdict = "cant_tell"
        else:
            r.verdict = verdict(r.diff_lo, r.diff_hi, p_adj, r.shared_intents, min_intents=min_k)
        if r.verdict == "cant_tell":
            r.intents_needed = intents_needed(r.before_rate or 0.0, m, config.design.icc, len(family), min_intents=min_k)
        r.note = f"family of {len(family)} engine(s) under Holm; m = {m} answers per intent per window"
    report = DiffReport(before, after, rows, min_k, utcnow())
    report.verdict_lines = [_line(r, min_k) for r in rows]
    return report


def render_diff_markdown(d: DiffReport) -> str:
    lines = [f"# Change between bursts {', '.join(d.before_labels)} and {', '.join(d.after_labels)}", ""]
    lines += d.verdict_lines
    lines += ["", f"Shared intents are unbranded intents with at least one activated answer in both windows; no verdict under {d.min_shared_intents}. Statistics: paired sign-flip test per engine, Holm across engines, Student t interval of the mean change. API answers, not the consumer apps.", ""]
    for e in d.engines:
        if e.per_intent:
            lines += [f"## {e.surface} {e.engine.model_requested} ({e.engine.config_sha[:8]})", "", "| Intent | Before | After |", "|---|---|---|"]
            lines += [f"| {i} | {round(100 * b)}% | {round(100 * a)}% |" for i, b, a in e.per_intent]
            lines.append("")
    return "\n".join(lines)
```

`commands/diff.py`: `register(app)` adds `diff(root: RootOption, before: Annotated[list[str], typer.Option("--before")], after: Annotated[list[str], typer.Option("--after")])` (ruff B008 rejects a `typer.Option(...)` default on a list parameter; `root: RootOption` is declared bare because `common.RootOption` carries its default through default_factory, as Task 8 built it), loads config, intents, pages, engines, calls `compute_diff` with `default_adapters()` and echoes `render_diff_markdown`.

- [ ] **Step 3: Run both test files, the suite, ruff; commit**

```bash
git add src/footnoteone/diff.py src/footnoteone/commands/diff.py tests/test_diff.py tests/test_cmd_diff.py
git commit -s -m "feat: before/after diff with sign-flip test, Holm, t interval and plain-word verdicts"
```

---

### Task 11: init, plan and run commands

**Files:**
- Create: `src/footnoteone/commands/init.py`, `src/footnoteone/commands/plan.py`, `src/footnoteone/commands/run.py`
- Test: `tests/test_cmd_init.py`, `tests/test_cmd_plan.py`, `tests/test_cmd_run.py`

**Interfaces:**
- Consumes: `config.write_templates`, `config.load_config`, `config.load_intents`, `config.engine_configs`, `library.discover_pages`, `library.write_pages`, `library.read_pages`, `plan.make_plan`, `plan.render_plan_markdown`, `runner.run_design`, `runner.keys_from_env`, `runner.default_adapters`, `runner.RunnerError`, `planning.PlanningAssumptions`, `pricing.PriceTable`, `commands.common.*`.
- Produces: `commands.init.register(app)` adding `init SITE_URL [--root .] [--force] [--no-discover] [--limit 500]`; `commands.plan.register(app)` adding `plan [--root .]`; `commands.run.register(app)` adding `run [--root .] [--label YYYY-MM-DD] [--budget USD] [--dry-run]`.

Behaviour: `init` writes the two templates (ConfigError on existing files without `--force` exits 2), then unless `--no-discover` opens `httpx.AsyncClient(max_redirects=5)`, discovers pages for the site, writes `.footnote/pages.jsonl`, and prints the page count by source plus the next steps ("edit intents.yaml, then run footnote doctor and footnote plan"). `plan` loads the project, builds engine configs with the planning assumptions, prints `render_plan_markdown(make_plan(...))`, exits 0 (warnings are in the text). `run` defaults the label to today's date (UTC, ISO) and the budget to `design.budget_usd_per_burst`; with `--dry-run` it prints the planned call count and the typical and worst-case totals from `make_plan` and makes no call; otherwise it reads keys from the environment (`keys_from_env`), calls `asyncio.run(run_design(...))` with `default_adapters()`, and prints planned, skipped, each status count, spent and the manifest id; `RunnerError` and `ConfigError` exit 2 with the message; a `KeyboardInterrupt` prints "interrupted; the manifest is marked aborted and a rerun with the same label resumes" and exits 130.

- [ ] **Step 1: Tests**

```python
# tests/test_cmd_init.py
import typer
from typer.testing import CliRunner

from footnoteone.commands.init import register
from footnoteone.library import read_pages

SM = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.org/a</loc></url><url><loc>https://example.org/b</loc></url></urlset>'


def app_with_command():
    app = typer.Typer()
    register(app)
    return app


def test_init_writes_templates_and_discovers_pages(tmp_path, httpx_mock):
    httpx_mock.add_response(url="https://example.org/robots.txt", status_code=404)
    httpx_mock.add_response(url="https://example.org/sitemap.xml", text=SM)
    httpx_mock.add_response(url="https://example.org/sitemap_index.xml", status_code=404)
    httpx_mock.add_response(url="https://example.org/", text="<html></html>")
    for path in ("/feed", "/rss.xml", "/atom.xml", "/feed.xml"):
        httpx_mock.add_response(url=f"https://example.org{path}", status_code=404)
    result = CliRunner().invoke(app_with_command(), ["https://example.org", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "footnote.toml").exists() and (tmp_path / "intents.yaml").exists()
    assert len(read_pages(tmp_path)) == 2 and "2 pages" in result.output and "intents.yaml" in result.output


def test_init_refuses_to_overwrite_without_force(tmp_path):
    first = CliRunner().invoke(app_with_command(), ["https://example.org", "--root", str(tmp_path), "--no-discover"])
    assert first.exit_code == 0 and read_pages(tmp_path) == []
    second = CliRunner().invoke(app_with_command(), ["https://example.org", "--root", str(tmp_path), "--no-discover"])
    assert second.exit_code == 2 and "exists" in (second.output + str(second.exception or ""))
    third = CliRunner().invoke(app_with_command(), ["https://example.org", "--root", str(tmp_path), "--no-discover", "--force"])
    assert third.exit_code == 0
```

```python
# tests/test_cmd_plan.py
import typer
from typer.testing import CliRunner

from footnoteone.commands.plan import register
from footnoteone.config import write_templates


def test_plan_prints_costs_and_power(tmp_path):
    write_templates(tmp_path, "https://example.org")
    app = typer.Typer()
    register(app)
    result = CliRunner().invoke(app, ["--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "worst case" in result.output and "api:openai" in result.output and "Warnings" in result.output
    assert "unbranded" in result.output and "\u2014" not in result.output


def test_plan_without_config_exits_2(tmp_path):
    app = typer.Typer()
    register(app)
    assert CliRunner().invoke(app, ["--root", str(tmp_path)]).exit_code == 2
```

```python
# tests/test_cmd_run.py
import typer
from typer.testing import CliRunner

from footnoteone.commands import run as run_cmd
from footnoteone.config import write_templates
from footnoteone.schema import Run
from footnoteone.store import JsonlStore
from test_runner import FakeAdapter


def app_with_command():
    app = typer.Typer()
    run_cmd.register(app)
    return app


def test_dry_run_needs_no_keys(tmp_path, monkeypatch):
    write_templates(tmp_path, "https://example.org")
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PERPLEXITY_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "calls" in result.output and "worst case" in result.output and not (tmp_path / ".footnote" / "manifests.jsonl").exists()


def test_missing_key_exits_2_with_variable_name(tmp_path, monkeypatch):
    write_templates(tmp_path, "https://example.org")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--label", "b1"])
    assert result.exit_code == 2 and "OPENAI_API_KEY" in (result.output + str(result.exception or ""))


def test_run_with_fake_adapters_records_runs(tmp_path, monkeypatch):
    write_templates(tmp_path, "https://example.org")
    toml = (tmp_path / "footnote.toml").read_text()
    toml = toml.split("[[engines]]")[0] + '[[engines]]\nprovider = "openai"\nmodel = "gpt-5-mini"\n\n' + "[design]" + toml.split("[design]")[1]
    (tmp_path / "footnote.toml").write_text(toml)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake = FakeAdapter(["ok"] * 100)
    monkeypatch.setattr(run_cmd, "default_adapters", lambda: {"openai": fake})
    result = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--label", "b1", "--budget", "5"])
    assert result.exit_code == 0, result.output
    runs = list(JsonlStore(tmp_path / ".footnote").iter("runs", Run))
    assert runs and all(r.status == "ok" for r in runs) and "ok" in result.output and "spent" in result.output
    again = CliRunner().invoke(app_with_command(), ["--root", str(tmp_path), "--label", "b1", "--budget", "5"])
    assert "skipped" in again.output and fake.calls == len(runs)
```

- [ ] **Step 2: Run to verify failure, then write the three command modules**

Each module imports its collaborators at module top so tests can monkeypatch `run_cmd.default_adapters`. Declare `root: RootOption` bare (its default comes from `common.RootOption`'s default_factory, as Task 8 built it); use `Annotated[<type>, typer.Option(...)] = <default>` for every option so ruff B008 cannot fire (it rejects a `typer.Option(...)` default on list parameters). `init`: `init(site_url: str, root: RootOption, force: Annotated[bool, typer.Option("--force")] = False, no_discover: Annotated[bool, typer.Option("--no-discover")] = False, limit: Annotated[int, typer.Option("--limit")] = 500)`. `plan`: `plan(root: RootOption)`. `run`: `run(root: RootOption, label: Annotated[str | None, typer.Option("--label")] = None, budget: Annotated[float | None, typer.Option("--budget")] = None, dry_run: Annotated[bool, typer.Option("--dry-run")] = False)`; the run summary prints this invocation's spend and the burst's total spend so far (the runner caps per burst label across invocations) and the remaining budget; the dry-run summary comes from `make_plan` (typical and worst totals) plus `RunnerResult.planned_calls`.

- [ ] **Step 3: Run the three test files, the suite, ruff; commit**

```bash
git add src/footnoteone/commands/init.py src/footnoteone/commands/plan.py src/footnoteone/commands/run.py tests/test_cmd_init.py tests/test_cmd_plan.py tests/test_cmd_run.py
git commit -s -m "feat: init, plan and run commands"
```

---

### Task 12: CLI wiring, smoke tests, README and the fixture recording script

**Files:**
- Modify: `src/footnoteone/cli.py`, `src/footnoteone/commands/__init__.py`, `README.md`, `tests/fixtures/README.md`
- Create: `scripts/record_fixtures.py`, `tests/test_cli_smoke.py`
- Test: `tests/test_cli_smoke.py`

**Interfaces:**
- Consumes: every `commands.<name>.register`.
- Produces: `commands.ALL: tuple[module, ...]` in the order init, plan, run, report, diff, crawl_check, doctor; `cli.app` with all seven commands registered; `scripts/record_fixtures.py` (not packaged).

- [ ] **Step 1: Smoke tests**

```python
# tests/test_cli_smoke.py
from typer.testing import CliRunner

from footnoteone.cli import app
from footnoteone.config import engine_configs, load_config, write_templates
from footnoteone.planning import PlanningAssumptions
from footnoteone.schema import Intent, Prompt
from helpers import OWN, build_store


def test_help_lists_every_command():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ("init", "plan", "run", "report", "diff", "crawl-check", "doctor"):
        assert name in result.output


def test_plan_and_report_run_on_a_fixture_store(tmp_path):
    write_templates(tmp_path, "https://example.org")
    engines = engine_configs(load_config(tmp_path), PlanningAssumptions.load())
    intents = [Intent(id="example-topic", label="x", prompts=[Prompt(id="p", text="q")]), Intent(id="placebo-water", label="p", kind="placebo", prompts=[Prompt(id="pp", text="q")])]
    build_store(tmp_path, ["w1"], engines[:1], intents, reps=1, pattern=lambda *a: ("ok", "yes", [OWN], [OWN]))
    plan = CliRunner().invoke(app, ["plan", "--root", str(tmp_path)])
    report = CliRunner().invoke(app, ["report", "--root", str(tmp_path), "--label", "w1"])
    assert plan.exit_code == 0 and "worst case" in plan.output
    assert report.exit_code == 0 and (tmp_path / "reports" / "w1" / "report.html").exists()
    doctor = CliRunner().invoke(app, ["doctor", "--root", str(tmp_path)])
    assert doctor.exit_code in (0, 1) and "pricing.yaml" in doctor.output
```

- [ ] **Step 2: Wire the CLI, write the script and the docs**

`commands/__init__.py`: import the seven modules and expose `ALL`. `cli.py`: after `app` is created, `for module in ALL: module.register(app)`. `scripts/record_fixtures.py`: a standalone script (docstring: "Record one real response per provider for the test fixtures; needs the provider keys in the environment and spends a few cents") that, for each provider whose key is set, builds the default `EngineConfig` through `EngineSpec(...).to_engine_config(PlanningAssumptions.load())`, calls the adapter once with a searching prompt and once with a prompt that needs no search, writes each `RawResponse.as_blob()` with a `_doc_note` ("recorded <date> with <adapter version>") to `tests/fixtures/recorded/<provider>_<searched|no_search>.json`, prints the paths and the parsed `Observation` summary (activated, consulted and cited counts, status, cost), and never prints or stores a key. `tests/fixtures/README.md` gains: "Recorded responses live in `recorded/`; run `uv run python scripts/record_fixtures.py` with your keys to refresh them." `README.md` replaces "Quick start (planned for v0.1)" with the real sequence (`uv tool install footnoteone` stays as planned until the first release; `footnote init https://your-site.example`, edit `intents.yaml`, `footnote doctor`, `footnote plan`, `footnote run --dry-run`, `footnote run`, `footnote report`, `footnote diff --before <label> --after <label>`, `footnote crawl-check`), a "Keys" section (environment variables named in footnote.toml; never written to disk), a "What the numbers mean" section (one paragraph each for activation, read, cited, FootnoteOne, verdicts, with the sentence that API answers are not the consumer apps), and the status line "v0.1 instrument and CLI built October 2026; no live run recorded yet".

- [ ] **Step 3: Run the suite, ruff, `uv run footnote --help`; commit**

```bash
git add src/footnoteone/cli.py src/footnoteone/commands/__init__.py scripts/record_fixtures.py tests/test_cli_smoke.py README.md tests/fixtures/README.md
git commit -s -m "feat: wire the seven CLI commands; smoke tests; README quick start; fixture recording script"
```

---

## Self-review notes

- Spec coverage: section 3 items 1 to 7 map to Tasks 11 (init, plan, run), 9 (report), 10 (diff), 8 (crawl-check, doctor); section 5 modules `library`, `plan`, `runner`, `metrics`, `report`, `cli` plus the new `config`, `planning`, `design`, `diff`, `commands`; section 7's `Page` and the Manifest fields in Task 2; section 9's smoke test in Task 12 and the recorded-response path in the script; section 11's gates: unpriced engines refused and worst case reserved (Task 6), canonical URLs recomputed (Task 7), owned set with video ids (Tasks 1 and 4), max_redirects=5 (Tasks 8 and 11), recorded fixtures (Task 12).
- Type consistency: `run_key` is the Run id everywhere (Tasks 2, 5, 6, 7); `RunView` fields used by Task 10 are those Task 7 defines; `MetricValue` fields used by Task 9 formatters are Task 7's; `OwnedSet.build` keyword names match between Tasks 1, 4 and 7; `EngineConfig.config_sha` names engines in manifests, runs and diffs.
- Review Focus: item 1 in Task 6 (resume, write order, abort), item 2 in Task 6 (budget tests), item 3 in Task 10 (unshared intents, config-sha naming), item 4 in Task 4 (cap, host filter, self-referencing index), item 5 in Tasks 7 (no-data engine), 9 (no bare 0%), 10 (insufficient and no-width lines).
- Known uncertainties: the Perplexity output-limit field name (Task 3 verifies the docs); YouTube handle resolution relies on a `channelId` string in the channel page HTML (Task 4; falls back to no videos); the exact wording of plain-language lines is normative only where a test pins it; `pytest` sibling-module imports (`from helpers import`, `from test_runner import`) rely on `tests/` having no `__init__.py`, which is the current layout.
