# Running bursts on a schedule with GitHub Actions

A burst is one pass over your design: every intent, every wording, every engine, repeated as configured. Running one a week from a GitHub Actions workflow gives you a measurement history without a server. The project folder (with `footnote.toml`, `intents.yaml` and `.footnote/`) lives in a private repository of yours; the keys live in that repository's secrets.

## Workflow

```yaml
name: footnote burst
on:
  schedule:
    - cron: "0 6 * * 1"   # Mondays 06:00 UTC
  workflow_dispatch: {}

permissions:
  contents: write

jobs:
  burst:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - name: Install FootnoteOne
        run: uv tool install git+https://github.com/shehral/footnoteone
      - name: Check the project
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          PERPLEXITY_API_KEY: ${{ secrets.PERPLEXITY_API_KEY }}
        run: footnote doctor
      - name: Run one burst
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          PERPLEXITY_API_KEY: ${{ secrets.PERPLEXITY_API_KEY }}
        run: footnote run --label "$(date -u +%F)"
      - name: Write the report
        run: footnote report
      - name: Keep the history
        run: |
          git config user.name "footnote bot"
          git config user.email "footnote-bot@users.noreply.github.com"
          git add .footnote reports
          git commit -s -m "burst $(date -u +%F)" || echo "nothing new"
          git push
```

## What to know

- The budget cap is per burst label. A rerun with the same label (for example after a failed step) resumes: calls that already succeeded are not paid for again, and the label's earlier spend counts against the cap.
- `footnote run` exits 1 when calls were attempted and none succeeded, or when every remaining call was skipped for budget, so a dead key or an exhausted budget fails the job.
- Keys are read from the environment only. Nothing in `.footnote/` or `reports/` contains them; a committed test checks that.
- Raw responses are stored under `.footnote/raw/`. They grow with every burst (a few kilobytes each). Keep them: they let every report be recomputed when a parser improves. If the repository gets large, move `.footnote/raw/` to Git LFS.
- Weekly bursts and a monthly `footnote diff --before <label> --after <label>` are a reasonable start. The diff needs at least 8 unbranded intents shared by both windows, and a 10-point move usually needs far more intents than a first design has; `footnote plan` prints the number for yours.
