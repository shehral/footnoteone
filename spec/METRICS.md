# FootnoteOne metric definitions (normative)

Version: 0.1.0 (2026-10-05). A change to any definition bumps this version through an RFC issue.

Scope rules for every metric:
- A run counts only when `status = ok`. Errors, refusals, timeouts and budget skips are excluded and reported as a failure rate.
- Branded and placebo intents never enter a headline.
- Engines and channels are never averaged together. Every value is labeled with its channel (for example `api:openai`).
- Every stored value carries numerator, denominator, exclusion counts, number of intents and the interval method.

| Metric | Numerator | Denominator | Interval | Notes |
|---|---|---|---|---|
| Activation rate | ok runs with activated = yes | ok runs with activated in {yes, no} | Wilson 95% | `unknown` counted and shown separately |
| Read rate | activated runs whose consulted set contains an owned URL | activated runs | intent-cluster bootstrap | "not exposed" when the engine config exposes no consulted list; never 0 |
| Cited rate (headline) | activated runs whose cited set contains an owned URL | activated runs | intent-cluster bootstrap; Wilson for single cells | intent level = mean over paraphrases x reps; library level = mean over unbranded, non-placebo intents |
| FootnoteOne rate | activated runs where an owned URL is the first citation | activated runs | intent-cluster bootstrap | the headline named after the product |
| Unconditional cited rate | runs citing an owned URL | ok runs with known activation | Wilson 95% | comparable to trackers' "visibility" |
| Conversion (cited given read) | activated runs where an owned URL is consulted and cited | activated runs where an owned URL is consulted | Wilson 95% | suppressed under 20 in the denominator |
| Placebo floor | cited rate on placebo intents | as cited rate | as cited rate | intents whose interval overlaps it are "indistinguishable from placebo" |
| Noise floor | within-burst and between-burst Jaccard of cited canonical URL sets; flip rate of owned-cited yes/no between replicate pairs | replicate pairs | bootstrap | per engine config |
| Change verdict | exact paired sign-flip test on per-intent cited rates between two windows | shared intents | Holm across engines | Moved if adjusted p < 0.05; No change if the 95% CI of the difference lies inside +/- 10 points; else Can't tell yet; no verdict under 6 shared intents |
| MDE | (z_0.975 + z_0.80) x sqrt(2 p (1 - p) DEFF / N), DEFF = 1 + (m - 1) ICC | | | ICC defaults to 0.3 until measured on the user's own data |
