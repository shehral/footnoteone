# FootnoteOne

Be the first footnote.

FootnoteOne is a free, open-source instrument for independent creators and small publishers. It shows which of your pages AI answer engines search for, read, and cite, with an error bar on every number, and it tells you whether a change you made moved anything, changed nothing, or cannot be judged yet. It suggests; it never rewrites your text.

It measures only the sanctioned answer APIs (OpenAI web search, Anthropic web search, the Perplexity Agent API) with your own keys. API answers are not the consumer apps, and every chart says so.

Status: v0.1 instrument and CLI built October 2026. No live provider response has been recorded yet: the parsers are checked against fixtures hand-written from the vendor docs, and `scripts/record_fixtures.py` records real responses with your keys. See `docs/superpowers/specs/2026-10-05-footnoteone-design.md` for the design and `spec/METRICS.md` for metric definitions.

## Quick start

`uv tool install footnoteone` is the install command from the first release on. Until then, clone this repository and run each command below as `uv run --project <your clone> footnote ...` from your project folder.

```bash
uv tool install footnoteone                     # from the first release on
footnote init https://your-site.example         # writes footnote.toml and intents.yaml, then finds your pages
# edit intents.yaml: the questions readers ask that your pages answer, in a few wordings each
footnote doctor                                 # checks the files, keys, prices and the .footnote store
footnote plan                                   # what a burst and a month cost, and the smallest move a burst can detect
footnote run --dry-run                          # lists the calls a burst would make, its cost and what the label has spent; no call is made
footnote run                                    # one burst under the budget cap, one line per call as it goes
footnote report                                 # report.md and report.html under reports/, for the most recent burst
footnote diff --before <label> --after <label>  # did the cited rate move? one verdict per engine
footnote crawl-check                            # which AI crawlers robots.txt lets in, and what each one gets
```

Every command takes `--root` for a project folder other than the current one; `footnote <command> --help` lists the rest.

- A burst is one pass of the design: every wording of every intent, repeated, on every engine. `footnote run` names it with `--label` (default: today's date, UTC), `footnote report` covers the most recent burst unless you name others with `--label` (or pick a date range with `--since` and `--until`), and `footnote diff` takes `--before` and `--after` once per burst in each window. The budget cap (`budget_usd_per_burst` in footnote.toml, or `--budget`) is per burst label across reruns: a rerun with the same label resumes the burst, leaves alone the calls that already ended for good (an ok answer, a refusal, a cut-off answer, or a response the parser could not read, which is kept for replay), asks the others again, and counts what the earlier runs of that label spent.
- An engine is its provider, model and params, and `footnote init` writes each engine's call limits into footnote.toml, so a later change to the planning defaults leaves your engines as they are. Change an engine's params yourself and its later bursts are reported apart from its earlier ones; the report and the diff say so.
- If your site lives on a platform many creators share (Medium, Substack, YouTube, GitHub, X and the like), give `footnote init` your profile's address, such as `https://medium.com/@you`: footnote.toml then lists no domain of yours, and only the pages under that address, your `offsite_prefixes`, count as yours. A site on its own host, including `you.substack.com`, keeps its domain.
- After you add `youtube_channels` or `own_domains` to footnote.toml, `footnote init https://your-site.example --pages-only` refreshes the page library from footnote.toml without touching your edits to it or to intents.yaml. It merges what it finds into the library by canonical URL: new pages are added, titles and dates are updated, and pages it no longer finds are kept. Add `--prune` to rebuild the library from scratch.
- A YouTube channel feed lists only the channel's 15 newest videos, so a first `footnote init` finds only those; a later `--pages-only` refresh keeps the ones it found before. List older videos as URLs in `offsite_prefixes` so that citations of them count as yours.

## Keys

Each provider's API key comes from an environment variable, and the `[keys]` table in footnote.toml names which one: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` and `PERPLEXITY_API_KEY` unless you change them. Put variable names there, never keys: footnote.toml does not load with a `[keys]` entry that is not a variable name (capital letters, digits and underscores), and the error names the entry, not what you typed. FootnoteOne reads keys only from the environment, never writes one to disk (no stored request, raw response or report holds one) and never prints one: `footnote doctor` names each variable and says only whether it is set, and a key that a request header cannot carry stops `footnote run` before any call, naming its variable. Of the commands, only `footnote run` without `--dry-run` needs keys, and only for the providers your engines use.

## What the numbers mean

API answers are not the consumer apps: every number comes from an answer API with web search, called with your keys, and the ChatGPT, Claude and Perplexity apps can answer the same question differently. Each engine is reported on its own, labeled with its channel (such as `api:openai`), and never averaged with another. An answer counts only when its call ended ok; errors, refusals, cut-off answers, timeouts and budget skips are left out and shown as a failure rate by reason. Your pages are your site and its subdomains (`own_domains`), the off-site pages and profiles you list in `offsite_prefixes`, and the videos the library found through `youtube_channels`. Branded and placebo intents never enter a headline, and every value carries what was counted and a 95% interval.

**Activation.** How often an answer searched the web at all: the answers that searched, out of the answers whose searching is known, with a Wilson 95% interval. An answer whose response does not show whether it searched is counted as unknown and shown beside the rate, never folded into yes or no. Read, cited and FootnoteOne count only the answers that searched.

**Read.** Of the answers that searched, the share whose consulted sources include one of your pages. Consulted means the pages the API reports the answer searched or opened, which is often more than it cites. Each unbranded intent gets its own rate over its wordings and repeats, and the overall figure is the mean over those intents, with a Student t interval over intents.

**Cited.** The headline: of the answers that searched, the share that cite one of your pages, averaged over intents with an interval as for read. The report also lists your pages that were read but never cited, the hosts cited instead, and the placebo floor: the cited rate on placebo intents, questions your pages cannot answer. An intent whose interval overlaps the floor is indistinguishable from placebo.

**FootnoteOne.** Of the answers that searched, the share whose first citation is one of your pages, averaged over intents with an interval as for cited. Being the first footnote is what the product is named after.

**Verdicts.** `footnote diff` asks, engine by engine, whether the cited rate moved between two windows of bursts, with a paired sign-flip test on the per-intent cited rates, Holm-corrected across engines. Moved means the adjusted p is below 0.05; No change means the 95% interval of the change has some width and lies inside plus or minus 10 points; anything else is Can't tell yet, with the number of intents needed. There is no verdict under 8 shared unbranded intents (intents with a searched answer in both windows). The intents needed to see a 10-point move are often more than a hundred at creator budgets; `footnote plan` prints the number for your design, and METRICS 0.2.0's power formula is unpaired, so the paired verdict usually needs fewer. Most early reports will say Can't tell yet.

## Principles

- Creators, not agencies. Pages, not brands. Measurement, not monitoring.
- Every number carries its numerator, denominator, exclusions and interval.
- Consulted and cited sources are stored separately; read-but-not-cited is a first-class result.
- No scraping, no Gemini, no vendors, no telemetry, no hosted tier.

## Licence

MIT. Contributions are signed off under the Developer Certificate of Origin; there is no CLA.
