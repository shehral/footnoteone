# FootnoteOne

Be the first footnote.

FootnoteOne is a free, open-source instrument for independent creators and small publishers. It shows which of your pages AI answer engines search for, read, and cite, with an error bar on every number, and it tells you whether a change you made moved anything, changed nothing, or cannot be judged yet. It suggests; it never rewrites your text.

It measures only the sanctioned answer APIs (OpenAI web search, Anthropic web search, the Perplexity Agent API) with your own keys. API answers are not the consumer apps, and every chart says so.

Status: pre-alpha scaffold (October 2026). See `docs/superpowers/specs/2026-10-05-footnoteone-design.md` for the design and `spec/METRICS.md` for metric definitions.

## Quick start (planned for v0.1)

```bash
uv tool install footnoteone
footnote init https://your-site.example
footnote plan
footnote run --dry-run
footnote report
```

## Principles

- Creators, not agencies. Pages, not brands. Measurement, not monitoring.
- Every number carries its numerator, denominator, exclusions and interval.
- Consulted and cited sources are stored separately; read-but-not-cited is a first-class result.
- No scraping, no Gemini, no vendors, no telemetry, no hosted tier.

## Licence

MIT. Contributions are signed off under the Developer Certificate of Origin; there is no CLA.
