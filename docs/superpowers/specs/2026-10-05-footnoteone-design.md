# FootnoteOne, design spec

**Date:** 2026-10-05
**Status:** Approved by the lead (name and go decided 2026-10-05); this spec fixes the v0.1 scope. Amended 2026-10-05 after plan A was built: section 11 records the as-built interfaces and the decisions the lead approved (METRICS 0.2.0).
**Author:** Mohammad Ali Shehral, with the Oct 4 product fleet synthesis as input (`~/geo-project/research/2026-10-04-landscape/product_vs_elmo_result.json`).

## 1. What it is

FootnoteOne is a free, MIT-licensed instrument for independent creators and small publishers. Using only the sanctioned answer APIs (OpenAI Responses `web_search`, Anthropic web search, Perplexity Agent API), it shows which of a creator's pages AI answer engines search for, read, and cite. Every number carries an interval and its denominator. It says whether a page edit moved anything, changed nothing, or cannot be judged yet. It suggests, and never rewrites.

Three contrasts with existing trackers: creators, not agencies; pages, not brands; measurement, not monitoring.

The name is the headline metric. A creator is "FootnoteOne on 12 of 40 prompts" when their page is the first citation. The CLI is `footnote`; the package is `footnoteone`.

## 2. Who it is for

Primary: a writer, newsletter author, docs or tutorial site, or niche reviewer with 20 to 500 pages, often cross-posted to Substack, Medium or YouTube, with no marketing budget and no analyst. They can paste an API key or fork a GitHub template. They will not run Postgres or read a p-value.

Secondary: the team's own measurement study, for which the product is the instrument; researchers and technical SEOs who need defensible numbers.

Not targeted: agencies that need white-label, multi-brand billing or consumer-UI coverage.

## 3. What v0.1 does (in scope)

1. **Init.** `footnote init <url>` reads the sitemap or RSS feed, builds the page library, canonicalizes URLs, records off-site profiles the user lists (Substack, Medium, YouTube, GitHub), and writes `footnote.toml` and `intents.yaml`. Intent drafting by an LLM is v0.2; v0.1 ships a template the user fills.
2. **Plan.** `footnote plan` prints, for the configured design (intents x paraphrases x reps x engines x bursts), the minimum detectable effect in percentage points and the dollar cost per burst and per month from a versioned price table. Nothing runs.
3. **Run.** `footnote run` executes the design against the three APIs with the user's keys, under a hard budget cap, writing a manifest, one run record per call, raw responses content-addressed, and typed source records (consulted and cited kept separate). `--dry-run` prints the call list and cost without calling.
4. **Report.** `footnote report` computes the funnel per engine (activation, read, cited, conversion), the FootnoteOne rate, pages read but never cited, who was cited instead, and the noise floor, and renders Markdown plus a static HTML page. Verdicts are plain words backed by intervals.
5. **Diff.** `footnote diff --before <window> --after <window>` runs the paired sign-flip test on per-intent cited rates and prints Moved, No change, or Can't tell yet.
6. **Crawl check.** `footnote crawl-check` parses robots.txt under RFC 9309 for every known AI bot (search, user-fetch and training agents), probes with each user agent, and prints an access matrix with the matched rule line.
7. **Doctor.** `footnote doctor` checks keys, price table version, config validity and disk layout.

Out of scope for v0.1 (v0.2 or later): experiments ledger with the 28-day hold, Bing AI Performance import, server-log import, margin-comment suggestions, the local serve-mode app, the MCP server, the Elmo importer and geo-score interop, multilingual UI.

## 4. What it never does

No scraping of consumer interfaces. No Gemini (its grounding terms forbid analysis; the clause is linked in the docs). No scraper vendors, affiliate links, telemetry or hosted tier. No averaging across engines or channels. Every number is labeled "API answers, not the consumer app."

## 5. Architecture

One Python 3.12 package, installed with `uv`. Modules:

- `schema`: Pydantic v2 models for Site, Page, Intent, Prompt, EngineConfig, Manifest, Run, Source, Observation; the JSONL append-only store and content-addressed raw blob store under `.footnote/`.
- `canon`: URL canonicalization (lowercase host, strip fragments and tracking parameters, resolve AMP and trailing-slash variants) and owner classification (own site, own off-site copy, other).
- `library`: sitemap and RSS discovery into Pages.
- `adapters`: a `Adapter` protocol with `call(prompt, engine) -> RawResponse` (network), `parse(raw) -> Observation` (pure), `price(usage, price_table) -> float`; one module per provider; recorded fixtures per provider under `tests/fixtures/`.
- `stats`: Wilson interval; intent-cluster bootstrap for a mean; exact paired sign-flip test with Holm correction; minimum detectable effect from p, n, m and ICC; verdict rules.
- `plan`: the design enumerator and the cost and MDE planner.
- `runner`: executes a design, enforces the budget cap, writes manifests and runs, retries transport errors, records every failure as a status.
- `metrics`: funnel and FootnoteOne computations from stored runs, with denominators and exclusions carried on every value.
- `audit`: robots.txt parser (RFC 9309), bot registry (`audit/bots.yaml`), HTTP probes.
- `report`: Markdown and static HTML rendering with Jinja2.
- `cli`: Typer commands.

Storage: `.footnote/manifests.jsonl`, `.footnote/runs.jsonl`, `.footnote/sources.jsonl`, `.footnote/raw/<sha256>.json`. Config in `footnote.toml` and `intents.yaml`, meant to live in the user's git repo. No database in v0.1; DuckDB is an optional cache later.

## 6. Metric definitions (spec/METRICS.md is normative)

`spec/METRICS.md` 0.2.0 supersedes the list below where they differ: intervals for intent-clustered rates and for differences are Student t over per-intent means (the bootstrap is a sensitivity figure), no verdict is given under 8 shared intents, the sign-flip test is exact up to 12 intents and Monte Carlo above, a run whose every search failed is an error, the MDE baseline is floored at 0.05 as a planning prior, and canonical URLs are recomputed at report time.

- A run counts only when `status = ok`. Errors, refusals, timeouts and budget skips are excluded and reported as a failure rate.
- Branded and placebo intents never enter a headline.
- Engines and channels are never averaged together.
- Activation rate = ok runs with `activated = yes` / ok runs with `activated in {yes, no}`; `unknown` is counted and shown separately.
- Read rate = activated runs whose consulted set contains an owned URL / activated runs; shown as "not exposed" when the engine config does not expose a consulted list, never as 0.
- Cited rate = activated runs whose cited set contains an owned URL / activated runs. Intent level: mean over paraphrases x reps. Library level: mean over unbranded, non-placebo intents. Interval: intent-cluster bootstrap, Wilson for single cells.
- FootnoteOne rate = activated runs where an owned URL is the first citation / activated runs.
- Conversion = activated runs where an owned URL is consulted and cited / activated runs where an owned URL is consulted; suppressed under 20 in the denominator.
- Placebo floor = cited rate on placebo intents; any intent whose interval overlaps it is "indistinguishable from placebo".
- Noise floor = within-burst and between-burst Jaccard of cited canonical URL sets, and the flip rate of owned-cited yes/no between replicate pairs.
- Change verdict = exact paired sign-flip test on per-intent cited rates between two windows, Holm-corrected across engines: Moved if adjusted p < 0.05; No change if the 95% interval of the difference lies inside plus or minus 10 points; otherwise Can't tell yet, with the number of intents needed. No verdict below 6 shared intents.
- MDE = (z_0.975 + z_0.80) x sqrt(2 p (1 - p) DEFF / N), DEFF = 1 + (m - 1) ICC, with ICC defaulting to 0.3 until measured.

## 7. Data model (fields that matter)

- `Run`: id, manifest_id, intent_id, prompt_id, engine_config_id, rep_idx, status, model_requested, model_returned, tool_version, activated (yes | no | unknown), activation_evidence, raw_sha256, parser_version, input_tokens, output_tokens, search_calls, cost_usd, started_at, finished_at.
- `Source`: run_id, role (consulted | cited), url, canonical_url, rank, title, char_start, char_end, provider_field.
- `Manifest`: id, code_version, adapter_versions, config_sha, price_table_version, budget_usd, spent_usd, runner (local | gha), started_at, status.
- `EngineConfig`: provider, model_requested, tool_version, params, surface label (for example `api:openai`), config_sha.
- `Intent`: id, label, kind (unbranded | branded | placebo), target_page_ids, prompts (paraphrases).

## 8. Constraints

- Python 3.12 or newer; dependencies limited to pydantic, httpx, typer, pyyaml, jinja2 and the standard library (TOML is read with `tomllib`; `footnote init` writes TOML from a template); dev dependencies pytest, pytest-httpx, pytest-asyncio, ruff.
- All network calls go through adapters; parsing is pure and replayable from stored raw responses.
- Every stored metric value carries numerator, denominator, exclusion counts and the interval method.
- Secrets come only from environment variables; nothing is written to disk except under `.footnote/`.
- No em dashes in user-facing text; plain words; the stats sit one click behind the verdict.
- Licence MIT, DCO sign-off, no CLA, no telemetry.

## 9. Testing

Unit tests per module against recorded fixtures and simulations; statistics validated by simulation (coverage of nominal intervals, type I error of the sign-flip test under the null); parsers tested against one real recorded response per provider plus edge cases (no search, refusal, truncated); a smoke test that runs `footnote plan` and `footnote report` on the fixture store.

## 10. Risks carried into the plan

API answers are not the consumer app (5 to 27% overlap), so every surface is labeled. Most verdicts will read "Can't tell yet" at creator budgets, so the report leads with descriptive findings that hold at any n. Provider fields drift, so manifests pin versions and parsing is replayable. A semester project needs named maintainers; GOVERNANCE.md names the rule.

## 11. As built after plan A (2026-10-05)

Plan A (`docs/superpowers/plans/2026-10-05-footnoteone-instrument-core.md`) built sections 5 to 8 with these differences, all approved by the lead on 2026-10-05:

- Audit: `fetch_robots` returns `RobotsFetch(status, text, error, access)`, `probe_url` returns `ProbeResult(status, final_url, error)`, `access_matrix` returns `AccessReport(robots_status, robots_error, robots_access, rows)`. An unreachable robots.txt (1xx, 429, 5xx or a failed request) means complete disallow; an unavailable one (other 4xx, a final 3xx, too many redirects) means no rules (RFC 9309 2.3.1). Control tokens (`crawls: false`) are not probed. The probe column means what a request carrying that user-agent string from the user's own machine receives; bot-verifying firewalls may answer the real bot differently.
- Adapters: `Observation` gains `model_requested` and `failed_searches`; parsers never raise on JSON-shaped input; consulted includes pages the engine opened or fetched; cited is recorded per occurrence; Perplexity follows the Agent API (`POST /v1/agent`, presets, inline citation markers resolved by result id when annotations are empty). Refusals, truncations and context-window cutoffs are statuses, never ok runs.
- Records: `Run.raw_sha256` and `Run.parser_version` are optional (None when no response body was received); non-finite floats are rejected; `Manifest` carries the engine configs and intents it ran (plan B); `Page` is the library record (plan B).
- Statistics: Student t intervals beside the bootstrap; zero-width verdict guard; input validation; incomplete beta from DLMF 8.17.22 and the modified Lentz algorithm.
- Pricing: Perplexity rows follow its Agent API page; dated model snapshots resolve to their row; unknown models fall back to the provider default and `doctor` flags them. The runner must refuse unpriced models and reserve the worst-case cost of each call before making it.
- Canonicalization is versioned (`canon.CANON_VERSION`) and recomputed at report time; the owned set includes exact off-site URLs and owned YouTube video ids, and profile prefixes compare without scheme and path case.
- Section 9: no provider has been called live; the fixtures are hand-written from the vendor docs read on 2026-10-05. Recording one real response per provider is the first live step of plan B, before any report is trusted.
