# Contributing to FootnoteOne

Thank you for helping creators see how AI answer engines treat their work.

## Ground rules

- Every metric change needs a short RFC (an issue titled `RFC: ...`) and bumps the metric version in `spec/METRICS.md`.
- No scraper vendors, affiliate links, telemetry, white-label code or feature-gated tiers. The project measures only sanctioned answer APIs with the user's own keys.
- Never record a number without its denominator, its exclusions and the channel it came from.
- Tests first. A change without a failing test that it makes pass will be sent back.

## Sign-off (DCO)

We use the Developer Certificate of Origin, not a CLA. Add `Signed-off-by: Your Name <you@example.com>` to each commit (`git commit -s`). Nobody can relicense your contribution.

## Development

```bash
uv sync --all-extras
uv run pytest
uv run ruff check .
```
