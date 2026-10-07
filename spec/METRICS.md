# FootnoteOne metric definitions (normative)

Version: 0.2.0 (2026-10-05). A change to any definition bumps this version through an RFC issue. 0.2.0 was approved by the project lead on 2026-10-05 (RFC-001: interval method, verdict floor, failed searches, planning prior, canonical URL recomputation) on the plan A measurements recorded in `docs/superpowers/plans/2026-10-05-footnoteone-instrument-core.md` and its ledger.

Scope rules for every metric:
- A run counts only when its effective status is `ok`. Errors, refusals, truncations, timeouts and budget skips are excluded and reported as a failure rate by reason.
- A run whose every search failed (`failed_searches >= search_calls > 0`) has effective status `error` with the reason "all searches failed": the engine attempted retrieval and got nothing, so it says nothing about the pages. A run with some failed searches keeps its status; its consulted set comes from the searches that succeeded.
- Branded and placebo intents never enter a headline.
- Engines and channels are never averaged together. Every value is labeled with its channel (for example `api:openai`) and the engine's tool version.
- Every stored value carries numerator, denominator, exclusion counts, number of intents and the interval method.
- Canonical URLs are recomputed at report time with the current canonicalization version (`canon.CANON_VERSION`); the stored `canonical_url` is a cache. Owned means own site (domain and subdomains), own off-site page (exact URL, owned YouTube video id, or profile prefix) per the project's owned set.
- Activation `unknown` covers responses with no sign of a search whose status is error or truncated, and Perplexity responses that report neither a search count nor a search item. Unknown runs are counted and shown, never folded into yes or no.

Intervals:
- Single cells (one rate over runs): Wilson score, 95%.
- Intent-clustered rates (read, cited, FootnoteOne) and differences between windows: Student t over per-intent means, df = intents - 1, 95%, not clipped to [0, 1]. A zero-width interval (every intent identical) is "cannot judge", never "no change". The percentile intent-cluster bootstrap is reported only as a sensitivity figure: it under-covers at creator sample sizes (0.86 at six intents against 0.95 for t, measured 2026-10-05).

| Metric | Numerator | Denominator | Interval | Notes |
|---|---|---|---|---|
| Activation rate | ok runs with activated = yes | ok runs with activated in {yes, no} | Wilson 95% | `unknown` counted and shown separately |
| Read rate | activated runs whose consulted set contains an owned URL | activated runs | Student t over intents | "not exposed" when the engine config exposes no consulted list; never 0 |
| Cited rate (headline) | activated runs whose cited set contains an owned URL | activated runs | Student t over intents; Wilson for single cells | intent level = mean over paraphrases x reps; library level = mean over unbranded, non-placebo intents |
| FootnoteOne rate | activated runs where an owned URL is the first citation | activated runs | Student t over intents | the headline named after the product |
| Unconditional cited rate | runs citing an owned URL | ok runs with known activation | Wilson 95% | comparable to trackers' "visibility" |
| Conversion (cited given read) | activated runs where an owned URL is consulted and cited | activated runs where an owned URL is consulted | Wilson 95% | suppressed under 20 in the denominator |
| Placebo floor | cited rate on placebo intents | as cited rate | as cited rate | intents whose interval overlaps it are "indistinguishable from placebo" |
| Noise floor | within-burst and between-burst Jaccard of cited canonical URL sets; flip rate of owned-cited yes/no between replicate pairs | replicate pairs | Wilson for the flip rate; mean with a t interval for Jaccard | per engine config |
| Change verdict | paired sign-flip test on per-intent cited rates between two windows: exact for up to 12 shared intents, Monte Carlo with 20,000 draws (plus-one corrected) above | shared unbranded, non-placebo intents with at least one activated run in each window | Holm across engines; Student t interval of the mean difference | Moved if adjusted p < 0.05; No change if the 95% interval of the difference lies inside +/- 10 points and has non-zero width; else Can't tell yet, with the number of intents needed; no verdict under 8 shared intents (with Holm across three engines, Moved needs at least 7 unanimous intents, so 6 could only ever say Can't tell) |
| MDE | (z_0.975 + z_0.80) x sqrt(2 p (1 - p) DEFF / N), DEFF = 1 + (m - 1) ICC | | | ICC defaults to 0.3 until measured on the user's own data; p is the observed or configured baseline cited rate, floored at 0.05 and capped at 0.95 and then labeled "planning prior" |
| Intents needed | smallest k such that the MDE at N = k x m answers per window is at most 10 points and Moved is reachable under Holm (engines x 2 / 2^k < 0.05) | | | shown with every Can't tell yet verdict and in `footnote plan` |
