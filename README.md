# FootnoteOne

**Be the first footnote.** FootnoteOne is a free, open-source instrument for independent creators and small publishers. It measures which of your pages the AI answer engines search for, read, and cite, puts an honest error bar on every number, and tells you whether a change you made moved anything, changed nothing, or cannot be judged yet.

[![CI](https://github.com/shehral/footnoteone/actions/workflows/ci.yml/badge.svg)](https://github.com/shehral/footnoteone/actions/workflows/ci.yml)
Licence: MIT. Status: v0.1 instrument and command line built (October 2026), no live run recorded yet.

It measures only the sanctioned answer APIs, with your own keys: OpenAI web search (Responses API), Anthropic web search, and the Perplexity Agent API. API answers are not the consumer apps, and every report says so on its first line.

## Three ideas

- **Creators, not agencies. Pages, not brands. Measurement, not monitoring.** The unit is one of your pages (including cross-posts on Medium, Substack or YouTube), not a brand mention. Nothing is white-labelled, nothing is hosted, nothing phones home.
- **A funnel, not a score.** For every searched answer: did the engine *read* one of your pages, did it *cite* one, and was yours the *first* footnote? Read-but-not-cited is a first-class result, because it tells you the page was found and passed over.
- **Honest statistics.** Every value carries its numerator, denominator, exclusions, number of intents and interval method. "No data" is never shown as 0%. A verdict says *Moved*, *No change* or *Can't tell yet*, and when it cannot tell, it says how many intents it would take.

## Quick start

Requirements: Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) (or pipx). Keys for the engines you want, as environment variables.

```bash
uv tool install git+https://github.com/shehral/footnoteone   # PyPI release to follow

footnote init https://your-site.example      # writes footnote.toml and intents.yaml, discovers your pages
$EDITOR intents.yaml                          # the questions readers ask that your pages answer
export OPENAI_API_KEY=... ANTHROPIC_API_KEY=... PERPLEXITY_API_KEY=...
footnote doctor                               # keys, prices, config, library, store
footnote plan                                 # cost per burst and per month, and what it can detect
footnote run --dry-run                        # the calls a burst would make, and their cost
footnote run                                  # one burst under the budget cap
footnote report                               # reports/<label>/report.md and report.html
footnote diff --before 2026-10-07 --after 2026-10-28
footnote crawl-check                          # can the AI crawlers reach your pages at all?
```

Edit `footnote.toml` to pick engines, wordings, repeats and the budget. Keys never go in that file: it names the environment variables that hold them.

## What each command does

| Command | What it does |
|---|---|
| `init SITE_URL` | Writes `footnote.toml` and `intents.yaml`, then finds your pages in sitemaps, feeds and YouTube channel feeds. `--pages-only` refreshes the page library later without touching your edits (`--prune` rebuilds it). |
| `plan` | Prints the cost of one burst and of a month of bursts (typical and worst case), the smallest move a burst can detect, and how many intents a verdict needs. Nothing runs. |
| `run` | Executes one burst: every intent x wording x engine x repeat, sequentially, under a hard budget cap. Prints progress, stores every raw response, and resumes if interrupted. `--dry-run` lists the calls and the cost. |
| `report` | Computes the funnel per engine for the chosen bursts (latest by default) and writes Markdown and a self-contained HTML page. |
| `diff` | Compares two windows of bursts: a paired sign-flip test on per-intent cited rates, Holm across engines, a Student t interval of the change, and a plain-word verdict. |
| `crawl-check` | Parses your robots.txt under RFC 9309 for every known AI crawler (search, user-fetch and training agents), probes each page with that agent's user-agent string, and prints the access matrix with the matched rule. |
| `doctor` | Checks keys (names only, never values), price table age, configuration, page library, store layout, parser drift. |

## What the output looks like

`footnote plan` on the template design (12 intents, 3 wordings, 2 repeats, 3 engines):

```
| Engine         | Model             | Calls | Typical cost | Worst case | Detectable move (points) |
|----------------|-------------------|-------|--------------|------------|--------------------------|
| api:openai     | gpt-5-mini        | 64    | 1.08 USD     | 2.20 USD   | 32                       |
| api:anthropic  | claude-sonnet-4-5 | 64    | 2.11 USD     | 4.99 USD   | 32                       |
| api:perplexity | fast              | 64    | 0.36 USD     | 0.82 USD   | 32                       |

Per burst: typical 3.55 USD; worst case 8.02 USD; budget 20.00 USD (fits the typical cost and the worst case).
Per month at 4 bursts: typical 14.20 USD; worst case 32.07 USD.
A Moved verdict needs at least 8 shared intents (Holm across 3 engines); a 10-point move needs about 105 intents at this design.
```

The report opens with a summary and a bursts table, then one section per engine:

```
- api:openai gpt-5-mini (config d3e4754d): cited rate 28% (15 to 42) over 10 intents.
- api:anthropic claude-sonnet-4-5 (config 805143d5): cited rate 9% (-2 to 20) over 10 intents.
- api:perplexity fast (config 8d1021ca): cited rate 38% (24 to 53) over 10 intents.

| Step                                                  | Value (95% interval) | Statistics                                             |
|-------------------------------------------------------|----------------------|--------------------------------------------------------|
| Activation: answers that searched the web             | 100% (94 to 100)     | Wilson 95%; 62 of 62 runs; left out: 2 error           |
| Read: searched answers that consulted one of your pages | 46% (34 to 58)     | Student t over intents; 27 of 58 runs, 10 intents      |
| Cited: searched answers that cited one of your pages  | 28% (15 to 42)       | Student t over intents; 17 of 58 runs, 10 intents      |
| FootnoteOne: first citation is one of your pages      | 28% (15 to 42)       | Student t over intents; 17 of 58 runs, 10 intents      |
| Conversion: cited, among those that read one          | 63% (44 to 78)       | Wilson 95%; 17 of 27 runs                              |
```

Then the placebo floor, the noise floor (how much answers move when nothing changed), a per-intent table, "Read but never cited" pages, and the hosts cited instead of you. In the HTML every interval and method sits behind a details toggle, so the page reads in plain words first.

`footnote diff` speaks in sentences:

```
api:openai gpt-5-mini (d3e4754d): Can't tell yet. Mean change +4 points, 95% interval -17 to +25,
across 10 shared intents; about 122 intents would be needed to see a 10-point move at this design.
```

(These samples come from a synthetic store; no provider has been called live yet.)

## How it measures

1. **Intents and wordings.** `intents.yaml` holds the questions your readers ask, each with a few wordings. Intents are `unbranded` (the headline), `branded` (they name you; never in a headline) or `placebo` (questions your pages cannot answer; they set the floor).
2. **Bursts.** `footnote run` asks every engine every wording, repeated, and records one *run* per call: status, whether the engine searched, tokens, cost, and the raw response under its hash. Consulted sources (what the engine searched or opened) and cited sources (what the answer footnotes) are stored separately.
3. **The owned set.** Your site's domains, your off-site pages (exact URLs and profile prefixes such as `https://medium.com/@you`), and your YouTube video ids. Every URL is canonicalized (scheme, `www`, tracking parameters, AMP and index variants, YouTube forms) before it is compared.
4. **The funnel per engine**, over searched answers to unbranded intents: *activation* (did it search), *read* (consulted an owned page), *cited*, *FootnoteOne* (first citation owned), *conversion* (cited given read), plus the unconditional cited rate, the placebo floor and the noise floor.
5. **Replay.** Reports are computed from the stored raw responses with the current parsers, so a parser improvement updates history without new calls, and canonical URLs are recomputed with the current rules.

Definitions are normative in [`spec/METRICS.md`](spec/METRICS.md) (version 0.2.0). Changing one needs an RFC and a version bump.

## The statistics, in short

- Single rates over runs use a Wilson 95% interval. Rates that are means over intents (read, cited, FootnoteOne) and the before/after change use a Student t interval over per-intent means, which is honest at six to twenty intents where a percentile bootstrap is not.
- A zero-width interval (every intent identical) is "cannot judge", never "no change".
- The change verdict is a paired sign-flip test on per-intent cited rates (exact up to 12 intents, Monte Carlo above), Holm-corrected across engines: *Moved* when the adjusted p is below 0.05; *No change* when the 95% interval of the change lies inside plus or minus 10 points; otherwise *Can't tell yet*, with the number of intents needed. No verdict below 8 shared intents.
- `footnote plan` computes the minimum detectable effect at 80% power for your design and says how many intents a 10-point move would need. The honest answer is often "more than a hundred": that is the nature of the measurement, and the planner says it before you spend.

## Configuration

`footnote.toml`:

| Key | Meaning |
|---|---|
| `site.url`, `site.own_domains` | Your site; subdomains count. A site on a shared platform (Medium, Substack, GitHub, YouTube, X...) owns no domain; its URL becomes an off-site prefix instead. |
| `site.offsite_prefixes` | Profile URLs whose pages are yours, such as `https://medium.com/@you` or `https://you.substack.com`. Give prefixes without a query. |
| `site.youtube_channels` | Channel URLs or ids. Videos come from the channel feed (its 15 newest); list older videos as URLs in `offsite_prefixes`. |
| `[[engines]]` | `provider` (`openai`, `anthropic`, `perplexity`), `model`, optional `tool_version` and `params` (an allowlist per provider: forced search, user location, output and search limits, domain filters). `init` writes the per-call limits in, so they are part of each engine's identity. |
| `design` | `paraphrases`, `reps`, `budget_usd_per_burst` (a hard cap), `bursts_per_month`, `baseline_cited_rate`, `icc`, `min_shared_intents`, `call_timeout_s`, `politeness_s`. |
| `keys` | The environment variable name per provider. Values are never read from this file. |

`intents.yaml`: a list of intents with a permanent `id` (slug), a `label`, a `kind`, optional `target_pages`, and `prompts` (the wordings). Edit wording freely; never reuse an id for a different question, because the diff pairs windows by id.

Data lives in `.footnote/` (`manifests.jsonl`, `runs.jsonl`, `sources.jsonl`, `pages.jsonl`, `raw/<sha256>.json`) and `reports/<label>/`. Nothing is written anywhere else.

## Costs

Prices come from a versioned table shipped with the package (`src/footnoteone/pricing.yaml`) and planning assumptions (`planning.yaml`); `doctor` warns when they are old and refuses to run an engine that has no price row. Before every call the runner reserves that call's worst case (maximum searches times the tool fee, plus maximum tokens at the model's rates) and records budget skips once the cap would be crossed; timeouts and failures after the request was sent are charged at the worst case. The cap is per burst label across reruns.

## Running on a schedule

See [`docs/github-actions.md`](docs/github-actions.md) for a weekly workflow that runs a burst from your repository's secrets, writes the report and keeps the history.

## Safety and privacy

- Keys come from environment variables only, travel in request headers only, and are never written, stored, logged or printed. A committed test (`tests/test_no_key_leaks.py`) exercises every path.
- No scraping of consumer interfaces, no scraper vendors, no affiliate links, no telemetry, no hosted tier.
- Google's Gemini grounding is not supported: its terms forbid analysing grounded results.
- `crawl-check` probes your own pages with each crawler's user-agent string from your machine; bot-verifying firewalls may answer the real crawler differently. Run it only against sites you own.

## Status and limits

- v0.1 is complete as an instrument: 768 tests, no network in tests. **No provider has been called live yet.** The adapter fixtures are hand-written from the vendor documentation read on 2026-10-05; `scripts/record_fixtures.py` records real responses with your keys and is the first live step.
- API answers are not the ChatGPT, Claude or Perplexity apps. Published comparisons put the overlap between API and app citations between 5% and 27%.
- Power is the real constraint: a 10-point move at a 20% baseline needs many intents. The planner tells you the number for your design rather than hiding it.
- Out of scope for v0.1: an experiments ledger with a hold for indexing lag, Bing AI Performance import, server log import, margin-comment suggestions, a local app, an MCP server, multilingual UI.

## Repository layout

```
src/footnoteone/
  schema.py store.py        records and the append-only store
  canon.py                  canonical URLs and the owned set
  config.py planning.py     footnote.toml, intents.yaml, per-call limits and cost assumptions
  library.py                page discovery (sitemaps, feeds, YouTube)
  design.py plan.py         the call list; cost and power planning
  runner.py                 bursts under a budget cap, resume, retries
  adapters/                 OpenAI, Anthropic, Perplexity (call, pure parse, price)
  metrics.py report.py diff.py   the funnel, the report, the before/after verdict
  audit/                    robots.txt parser, AI crawler registry, probes
  commands/ cli.py          the seven commands
  pricing.yaml planning.yaml templates/
spec/METRICS.md             normative metric definitions
docs/superpowers/           design spec and the two implementation plans, with as-built notes
tests/                      768 tests; fixtures under tests/fixtures
scripts/record_fixtures.py  the first live step
```

## Development

```bash
git clone https://github.com/shehral/footnoteone && cd footnoteone
uv sync --all-extras
uv run pytest -q
uv run ruff check .
uv run footnote --help
```

CI runs a locked sync, ruff, the suite, a DCO check and a wheel smoke test (`init` and `plan` from the built wheel).

## Contributing, governance, licence

Contributions are welcome under the Developer Certificate of Origin (`git commit -s`); there is no CLA, so nobody can relicense your work. Metric changes go through an RFC issue and a METRICS version bump. See [`CONTRIBUTING.md`](CONTRIBUTING.md), [`GOVERNANCE.md`](GOVERNANCE.md), [`SECURITY.md`](SECURITY.md) and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

MIT licence. Copyright 2026 Mohammad Ali Shehral and FootnoteOne contributors.

## About

FootnoteOne is maintained by [Mohammad Ali Shehral](https://github.com/shehral). It grew out of a research project on how AI answer engines treat independent creators; the product is the instrument that project uses, released so that creators can run the same measurement on their own pages.
