# Changelog

All notable changes are listed here. Versions follow semantic versioning once 0.1.0 is tagged.

## Unreleased (0.1.0 in progress)

First public version of the instrument and the command line. Built in two reviewed stages in October 2026.

### Added
- Records and stores: typed run, source and manifest records as append-only JSONL under `.footnote/`, raw responses content-addressed by hash, torn-write tolerant reads, atomic blob writes.
- Adapters for the sanctioned answer APIs: OpenAI Responses web search, Anthropic web search, Perplexity Agent API; consulted and cited sources kept apart; refusals, truncations and failed searches as statuses; pure, replayable parsers.
- Canonicalization rule set 2 with an owned set (domains, exact URLs, YouTube video ids, profile prefixes).
- Statistics: Wilson and Student t intervals, exact and Monte Carlo paired sign-flip test, Holm correction, minimum detectable effect, verdict rules with a zero-width guard (`spec/METRICS.md` 0.2.0).
- Robots audit: RFC 9309 parser, a registry of AI crawlers with their purposes, per-bot probes.
- Project configuration (`footnote.toml`, `intents.yaml`), planning assumptions, page library discovery from sitemaps, feeds and YouTube channel feeds.
- Planner (cost per burst and per month, detectable move, intents needed), runner (worst-case reservation, per-burst cap across reruns, resume, retries, structured error kinds), metrics with replay, Markdown and HTML report, before/after diff.
- Commands: `init`, `plan`, `run`, `report`, `diff`, `crawl-check`, `doctor`.
- A fixture recording script for the first live calls.

### Known limits
- No provider has been called live yet; fixtures are hand-written from the vendor documentation read on 2026-10-05.
- API answers are not the consumer apps; every report says so.
