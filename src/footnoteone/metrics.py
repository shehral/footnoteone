"""Funnel metrics from stored runs, per METRICS.md 0.2.0. Every value carries its numerator, denominator,
exclusion counts, number of intents and interval method.

Runs are read from `.footnote/`, the last record per id winning. A run whose parser version differs from the
current adapter's is re-read from its raw blob with the current parser (a replay); a replay whose blob cannot
be read or whose parser raises keeps the stored reading and is counted. Every other run takes its sources
from `sources.jsonl`, only those parsed from the raw response its last record names. Either way canonical
URLs are recomputed with the current rule set (`canon.CANON_VERSION`), because the stored canonical_url is a
cache.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from itertools import combinations
from pathlib import Path
from statistics import mean

from footnoteone.adapters.base import Adapter, RawResponse
from footnoteone.canon import CANON_VERSION, OwnedSet, canonicalize, classify_owner, host_of
from footnoteone.config import ProjectConfig
from footnoteone.library import owned_set_for
from footnoteone.schema import (
    Activation,
    EngineConfig,
    Intent,
    Manifest,
    Observation,
    Page,
    Run,
    RunStatus,
    SourceRecord,
    effective_status,
    parsed_error_kind,
    utcnow,
)
from footnoteone.stats import cluster_bootstrap_mean, cluster_t_interval, jaccard, wilson
from footnoteone.store import JsonlStore, RawStore

WILSON = "Wilson 95%"
UNCONDITIONAL = "Wilson 95%, unbranded intents"  # Ruling B28: unconditional cited counts unbranded intents
STUDENT_T = "Student t over intents"
CONVERSION_MIN = 20  # conversion is suppressed under this many runs in its denominator
READ_NEVER_CITED_TOP = 20
CITED_INSTEAD_TOP = 10


@dataclass
class MetricValue:
    """One metric value with what it was computed from.

    value, lo and hi are proportions (a mean Jaccard for the noise floor) and are None when there is nothing
    to count, never 0. numerator and denominator are what was counted: runs meeting the condition over runs
    for a rate, the sum of pair Jaccards over pairs for a Jaccard mean. excluded counts the runs (or pairs)
    in the metric's scope that the denominator leaves out, by reason. n_intents is the number of intents
    behind the value.
    """

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
    def no_data(cls, method: str, note: str = "no data") -> MetricValue:
        return cls(None, None, None, None, None, {}, None, method, note)


def wilson_value(k: int, n: int, excluded: dict[str, int] | None = None, method: str = WILSON) -> MetricValue:
    """k of n runs as a single-cell rate with its Wilson 95% interval; no data when n is 0."""
    if n == 0:
        return replace(MetricValue.no_data(method), excluded=dict(excluded or {}))
    lo, hi = wilson(k, n)
    return MetricValue(k / n, lo, hi, k, n, dict(excluded or {}), None, method)


def t_value(
    groups: dict[str, list[float]], excluded: dict[str, int] | None = None, method: str = STUDENT_T
) -> MetricValue:
    """An intent-clustered value from one list of run values per intent (1 or 0 per run for a rate).

    value = mean over intents of each intent's mean; lo and hi = cluster_t_interval (Student t over intents,
    df = intents - 1, not clipped to [0, 1]), None under two intents. numerator = sum of every run value,
    denominator = runs, n_intents = intents with at least one run. note gives the percentile intent-cluster
    bootstrap as a sensitivity figure only. No data when no intent has a run.
    """
    clusters = [list(values) for values in groups.values() if values]
    if not clusters:
        return replace(MetricValue.no_data(method), excluded=dict(excluded or {}))
    interval = cluster_t_interval(clusters)
    if interval is None:
        value, lo, hi = float(mean(mean(c) for c in clusters)), None, None
        note = "one intent only, so no interval"
    else:
        value, lo, hi = (float(bound) for bound in interval)
        _, boot_lo, boot_hi = cluster_bootstrap_mean(clusters)
        note = f"sensitivity: bootstrap {boot_lo:.3f} to {boot_hi:.3f}"
    numerator = sum(sum(c) for c in clusters)
    denominator = sum(len(c) for c in clusters)
    excluded = dict(excluded or {})
    return MetricValue(value, lo, hi, numerator, denominator, excluded, len(clusters), method, note)


def effective_view_status(status: RunStatus, search_calls: int, failed_searches: int) -> RunStatus:
    """The status a run counts with: a run whose every search failed is an error (METRICS 0.2.0)."""
    return effective_status(status, search_calls, failed_searches)[0]


@dataclass
class RunView:
    """One run as the metrics see it: joined to its burst's engine and intent, with status, error kind and
    activation re-derived on replay and URLs canonicalized with the current rule set. consulted is a set;
    cited keeps rank order (the first citation first) and repeats."""

    run: Run
    label: str
    engine: EngineConfig
    intent: Intent | None
    status: RunStatus
    activated: Activation
    consulted: set[str]
    cited: list[str]
    owned_consulted: bool
    owned_cited: bool
    owned_first: bool
    replayed: bool
    error_kind: str | None = None  # Run.error_kind, or on replay the kind of the fresh reading
    # Why a run due for a replay kept its stored reading: "replay_failed" (the current parser raised) or
    # "unreadable" (its raw blob could not be read). None otherwise.
    replay_problem: str | None = None


@dataclass
class IntentRow:
    """One intent for one engine. read, cited and footnote_one are single cells (Wilson 95%) over the
    intent's activated runs. indistinguishable_from_placebo: an unbranded intent whose cited interval
    overlaps the placebo band (see _placebo_floor)."""

    intent_id: str
    label: str
    kind: str
    runs_ok: int
    activated: int
    read: MetricValue
    cited: MetricValue
    footnote_one: MetricValue
    indistinguishable_from_placebo: bool


@dataclass
class PageRow:
    canonical_url: str
    consulted_runs: int
    cited_runs: int


@dataclass
class HostRow:
    host: str
    cited_runs: int
    share: float


@dataclass
class NoiseFloor:
    within_burst_jaccard: MetricValue
    between_burst_jaccard: MetricValue
    flip_rate: MetricValue


@dataclass
class EngineMetrics:
    engine: EngineConfig
    surface: str
    tool_version: str | None
    runs_total: int
    failures: dict[str, int]
    activation: MetricValue
    unknown_activation: int
    read: MetricValue
    cited: MetricValue
    footnote_one: MetricValue
    unconditional_cited: MetricValue
    conversion: MetricValue
    placebo_floor: MetricValue
    noise: NoiseFloor
    per_intent: list[IntentRow]
    read_never_cited: list[PageRow]
    cited_instead: list[HostRow]
    replayed: int
    # The runs with status error by error_kind ("kind not recorded" for records older than the field).
    error_kinds: dict[str, int] = field(default_factory=dict)
    # Whether footnote.toml configures this engine config now. A burst run under params since changed keeps
    # its own config (config_sha) and is reported under it, marked as no longer configured.
    in_config: bool = True
    # Runs due for a replay that kept their stored reading: the current parser raised on them, or their raw
    # blob could not be read.
    replay_failed: int = 0
    unreadable_blobs: int = 0
    # The activated runs of unbranded intents that cited any page: the denominator of the cited_instead
    # shares, so 0 means those shares have no data.
    answered_runs: int = 0


@dataclass
class BurstRow:
    """One selected burst invocation as its manifest's last record gives it; calls counts the runs it
    recorded other than budget skips."""

    label: str
    started_at: datetime
    status: str
    calls: int
    spent_usd: float
    budget_usd: float
    code_version: str
    price_table_version: str


@dataclass
class Report:
    """`selection` says how the bursts were chosen when the reader did not name them (empty otherwise), and
    `bursts` holds one row per selected manifest."""

    generated_at: datetime
    labels: list[str]
    manifests: list[Manifest]
    engines: list[EngineMetrics]
    canon_version: int
    intents_total: int
    owned_domains: list[str]
    note: str
    selection: str = ""
    bursts: list[BurstRow] = field(default_factory=list)


def load_manifests(store: JsonlStore) -> dict[str, Manifest]:
    """Every manifest by id. The runner appends a burst's manifest again when the burst ends, so the last
    record per id wins."""
    return {m.id: m for m in store.iter("manifests", Manifest)}


def latest_label(manifests: Iterable[Manifest]) -> str | None:
    """The label of the burst started last, or None when there is none."""
    latest = max(manifests, key=lambda m: m.started_at, default=None)
    return latest.label if latest is not None else None


def load_runs(store: JsonlStore) -> dict[str, Run]:
    """Every run by id. A retried call appends a new record under the same id, so the last record wins."""
    return {r.id: r for r in store.iter("runs", Run)}


def load_sources(store: JsonlStore) -> dict[str, list[SourceRecord]]:
    """Every source record grouped by run id, in file order. A call tried again appends its new sources under
    the same run id, so a group can hold several attempts' sources; run_views keeps the ones whose
    raw_sha256 is the run's."""
    grouped: dict[str, list[SourceRecord]] = defaultdict(list)
    for source in store.iter("sources", SourceRecord):
        grouped[source.run_id].append(source)
    return dict(grouped)


def select_manifests(
    manifests: Iterable[Manifest],
    labels: list[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Manifest]:
    """The manifests whose label is in `labels` (None or empty keeps every label) and whose started_at is
    within [since, until] (both bounds inclusive and optional), sorted by started_at."""
    chosen = [
        m
        for m in manifests
        if (not labels or m.label in labels)
        and (since is None or m.started_at >= since)
        and (until is None or m.started_at <= until)
    ]
    return sorted(chosen, key=lambda m: m.started_at)


def _owner_test(owned: OwnedSet) -> Callable[[str], bool]:
    """`classify_owner(url, owned) != "other"`, memoized: the same URLs recur across runs."""
    cache: dict[str, bool] = {}

    def is_owned(url: str) -> bool:
        if url not in cache:
            cache[url] = classify_owner(url, owned) != "other"
        return cache[url]

    return is_owned


def _replay(
    run: Run, adapter: Adapter | None, raw: RawStore | None
) -> tuple[Observation | None, str | None]:
    """The current parser's reading of the run's stored response when another parser version read it and its
    raw blob is stored, else no reading; with it, why a replay that was due gave none. A blob that cannot be
    read (not JSON, not a RawResponse) is "unreadable", and a parser that raises is "replay_failed": parsers
    should never raise, but one bad blob must not stop a report, so the run keeps its stored reading."""
    if adapter is None or raw is None or not run.raw_sha256 or run.parser_version == adapter.version:
        return None, None
    if not raw.exists(run.raw_sha256):
        return None, None
    try:
        response = RawResponse.model_validate(raw.get(run.raw_sha256))
    except (OSError, ValueError, RecursionError):
        return None, "unreadable"
    try:
        return adapter.parse(response), None
    except Exception:
        return None, "replay_failed"


def run_views(
    root: Path, adapters: Mapping[str, Adapter], manifests: list[Manifest], owned: OwnedSet
) -> list[RunView]:
    """The runs of `manifests` (last record per id), in runs.jsonl order.

    Each run is joined to its own manifest's engine (by engine_config_id) and intent (by id); a run whose
    manifest does not list its engine is skipped, and one whose intent is not listed gets intent None. A run
    is replayed when `adapters` has its provider, its raw blob is stored and its parser_version differs from
    the adapter's version: consulted and cited then come from the fresh Observation, the status from
    schema.effective_status (as effective_view_status gives it) and the error kind from that status and its
    reason, so a replay into "every search failed" reads all_searches_failed. Every other run keeps its
    stored status and error kind (the runner already made a run whose every search failed an error) and
    takes its sources from sources.jsonl, keeping only those whose
    raw_sha256 equals the run's (both None counts as equal), so the sources of a superseded or crashed
    attempt never join the final run. URLs are canonicalized afresh; owned means classify_owner says
    anything but "other". A missing `.footnote/` gives [] and is not created.
    """
    directory = Path(root) / ".footnote"
    if not manifests or not directory.is_dir():
        return []
    store = JsonlStore(directory)
    by_id = {m.id: m for m in manifests}
    engines = {m.id: {e.config_sha: e for e in m.engines} for m in manifests}
    intents = {m.id: {i.id: i for i in m.intents} for m in manifests}
    sources = load_sources(store)
    raw = RawStore(directory) if (directory / "raw").is_dir() else None
    is_owned = _owner_test(owned)
    views: list[RunView] = []
    for run in load_runs(store).values():
        manifest = by_id.get(run.manifest_id)
        if manifest is None:
            continue
        engine = engines[manifest.id].get(run.engine_config_id)
        if engine is None:
            continue
        obs, replay_problem = _replay(run, adapters.get(engine.provider), raw)
        if obs is not None:
            status, reason = effective_status(obs.status, obs.search_calls, obs.failed_searches)
            error_kind = parsed_error_kind(status, reason)
            activated = obs.activated
            consulted = {canonicalize(ref.url) for ref in obs.consulted}
            cited = [canonicalize(ref.url) for ref in sorted(obs.cited, key=lambda ref: ref.rank)]
        else:
            records = [s for s in sources.get(run.id, []) if s.raw_sha256 == run.raw_sha256]
            status, activated, error_kind = run.status, run.activated, run.error_kind
            consulted = {canonicalize(s.url) for s in records if s.role == "consulted"}
            cited = [canonicalize(s.url) for s in sorted(records, key=lambda s: s.rank) if s.role == "cited"]
        views.append(
            RunView(
                run=run,
                label=manifest.label,
                engine=engine,
                intent=intents[manifest.id].get(run.intent_id),
                status=status,
                activated=activated,
                consulted=consulted,
                cited=cited,
                owned_consulted=any(is_owned(url) for url in consulted),
                owned_cited=any(is_owned(url) for url in cited),
                owned_first=bool(cited) and is_owned(cited[0]),
                replayed=obs is not None,
                error_kind=error_kind,
                replay_problem=replay_problem,
            )
        )
    return views


def _kind(view: RunView) -> str | None:
    return view.intent.kind if view.intent is not None else None


def _activated(views: Iterable[RunView]) -> list[RunView]:
    """The ok runs that searched: the denominator of every rate over activated runs."""
    return [v for v in views if v.status == "ok" and v.activated == "yes"]


def _excluded(scope: Iterable[RunView], activated_only: bool) -> dict[str, int]:
    """The runs in scope that a denominator leaves out, by reason: a failure by its status, an ok run with
    unknown activation as "unknown", and, when the denominator holds activated runs only, an ok run that did
    not search as "not activated"."""
    counts: Counter[str] = Counter()
    for v in scope:
        if v.status != "ok":
            counts[v.status] += 1
        elif v.activated == "unknown":
            counts["unknown"] += 1
        elif v.activated == "no" and activated_only:
            counts["not activated"] += 1
    return dict(sorted(counts.items()))


def _groups(views: Iterable[RunView], hit: Callable[[RunView], bool]) -> dict[str, list[float]]:
    """One value per run, 1 when `hit` holds and 0 otherwise, grouped by intent id. Integers, so a rate's
    numerator stays a count of runs."""
    grouped: dict[str, list[float]] = defaultdict(list)
    for v in views:
        grouped[v.run.intent_id].append(1 if hit(v) else 0)
    return dict(grouped)


def _cell(k: int, n: int, excluded: dict[str, int], n_intents: int, method: str = WILSON) -> MetricValue:
    """wilson_value carrying the number of intents behind it (METRICS: every value carries it)."""
    value = wilson_value(k, n, excluded, method)
    return replace(value, n_intents=n_intents) if n else value


def _conversion(headline: list[RunView], excluded: dict[str, int]) -> MetricValue:
    """Activated headline runs that consulted and cited an owned URL, over those that consulted one; Wilson
    95%, and suppressed (no value, counts kept) under CONVERSION_MIN in the denominator."""
    read = [v for v in headline if v.owned_consulted]
    k, n = sum(v.owned_cited for v in read), len(read)
    if len(headline) > n:
        excluded = {**excluded, "owned page not read": len(headline) - n}
    n_intents = len({v.run.intent_id for v in read})
    if 0 < n < CONVERSION_MIN:
        note = f"suppressed: fewer than {CONVERSION_MIN} runs read an owned page ({n})"
        return MetricValue(None, None, None, k, n, excluded, n_intents, WILSON, note)
    return _cell(k, n, excluded, n_intents)


def _jaccard_value(pairs: list[tuple[RunView, RunView]]) -> MetricValue:
    """Mean Jaccard of the pairs' cited URL sets, clustered by intent (Student t over intents). A pair whose
    cited sets are both empty has no Jaccard and is counted as excluded."""
    grouped: dict[str, list[float]] = defaultdict(list)
    skipped = 0
    for a, b in pairs:
        similarity = jaccard(set(a.cited), set(b.cited))
        if similarity is None:
            skipped += 1
        else:
            grouped[a.run.intent_id].append(similarity)
    return t_value(dict(grouped), {"both cited sets empty": skipped} if skipped else None)


def _noise(headline: list[RunView]) -> NoiseFloor:
    """Replicate pairs among the activated headline runs. Within a burst: every pair of distinct reps with the
    same (label, intent, prompt). Between bursts: every pair with the same (intent, prompt) and different
    labels. Flip rate: pairs whose owned-cited yes or no differ, Wilson 95% over all pairs of both kinds."""
    same_burst: dict[tuple[str, str, str], list[RunView]] = defaultdict(list)
    same_prompt: dict[tuple[str, str], list[RunView]] = defaultdict(list)
    for v in headline:
        same_burst[(v.label, v.run.intent_id, v.run.prompt_id)].append(v)
        same_prompt[(v.run.intent_id, v.run.prompt_id)].append(v)
    within = [
        (a, b) for g in same_burst.values() for a, b in combinations(g, 2) if a.run.rep_idx != b.run.rep_idx
    ]
    between = [(a, b) for g in same_prompt.values() for a, b in combinations(g, 2) if a.label != b.label]
    pairs = within + between
    flips = sum(a.owned_cited != b.owned_cited for a, b in pairs)
    flip_rate = _cell(flips, len(pairs), {}, len({a.run.intent_id for a, _ in pairs}))
    return NoiseFloor(_jaccard_value(within), _jaccard_value(between), flip_rate)


def _read_never_cited(headline: list[RunView], is_owned: Callable[[str], bool]) -> list[PageRow]:
    """Owned URLs consulted in activated headline runs and cited in none of them, by consulted runs
    descending (then URL), at most READ_NEVER_CITED_TOP."""
    consulted: Counter[str] = Counter()
    cited: Counter[str] = Counter()
    for v in headline:
        consulted.update(url for url in v.consulted if is_owned(url))
        cited.update(url for url in set(v.cited) if is_owned(url))
    rows = [PageRow(url, n, 0) for url, n in consulted.items() if not cited[url]]
    rows.sort(key=lambda row: (-row.consulted_runs, row.canonical_url))
    return rows[:READ_NEVER_CITED_TOP]


def _cited_instead(headline: list[RunView]) -> list[HostRow]:
    """Per host, the activated headline runs that cited it while citing no owned URL; share = that count over
    the activated headline runs with any citation. Most runs first (then host), at most CITED_INSTEAD_TOP."""
    answered = [v for v in headline if v.cited]
    hosts: Counter[str] = Counter()
    for v in answered:
        if not v.owned_cited:
            hosts.update({host_of(url) for url in v.cited} - {""})
    ranked = sorted(hosts.items(), key=lambda item: (-item[1], item[0]))[:CITED_INSTEAD_TOP]
    return [HostRow(host, n, n / len(answered)) for host, n in ranked]


def _placebo_floor(placebo_scope: list[RunView]) -> tuple[MetricValue, tuple[float, float] | None]:
    """The placebo floor (t_value over the placebo intents' activated runs, 1 when the run cites an owned
    URL) and the interval an intent's cited interval is judged against for "indistinguishable from placebo",
    None when the floor has no data.

    That interval is the floor's own when it has width. A floor from one placebo intent has no interval, and
    one whose intents all share a rate has zero width, which METRICS calls "cannot judge"; read as a point,
    either would make every intent cited even once distinguishable from placebo. Both are judged instead
    against the Wilson 95% interval of the placebo's activated runs taken together, the interval METRICS
    gives a single cell of cited rate, and the floor's note says so. The floor's value is unchanged.
    """
    runs = _activated(placebo_scope)
    floor = t_value(_groups(runs, lambda v: v.owned_cited), _excluded(placebo_scope, activated_only=True))
    if floor.value is None:
        return floor, None
    if floor.lo is not None and floor.hi is not None and floor.lo != floor.hi:
        return floor, (floor.lo, floor.hi)
    k, n = sum(v.owned_cited for v in runs), len(runs)
    lo, hi = wilson(k, n)  # n >= 1: a floor with a value has an activated run
    reason = "" if floor.lo is None else "the t interval has zero width, so "
    judged = f"{reason}intents are judged against Wilson 95% over the placebo runs ({k} of {n} cited)"
    return replace(floor, note=f"{floor.note}; {judged}: {lo:.3f} to {hi:.3f}"), (lo, hi)


def _overlaps(cell: MetricValue, band: tuple[float, float] | None) -> bool:
    """Whether an intent's cited interval overlaps the placebo band from _placebo_floor. False when either
    side has no data."""
    if band is None or cell.lo is None or cell.hi is None:
        return False
    return cell.lo <= band[1] and band[0] <= cell.hi


def _intent_rows(
    views: list[RunView], intents: list[Intent], placebo_band: tuple[float, float] | None
) -> list[IntentRow]:
    """One row per intent in `intents`, in that order, from the views with that intent id."""
    by_intent: dict[str, list[RunView]] = defaultdict(list)
    for v in views:
        by_intent[v.run.intent_id].append(v)
    rows: list[IntentRow] = []
    for intent in intents:
        scope = by_intent.get(intent.id, [])
        searched = _activated(scope)
        excluded = _excluded(scope, activated_only=True)
        n = len(searched)
        read, cited, first = (
            _cell(sum(hits), n, excluded, 1)
            for hits in (
                [v.owned_consulted for v in searched],
                [v.owned_cited for v in searched],
                [v.owned_first for v in searched],
            )
        )
        rows.append(
            IntentRow(
                intent_id=intent.id,
                label=intent.label,
                kind=intent.kind,
                runs_ok=sum(v.status == "ok" for v in scope),
                activated=n,
                read=read,
                cited=cited,
                footnote_one=first,
                indistinguishable_from_placebo=intent.kind == "unbranded" and _overlaps(cited, placebo_band),
            )
        )
    return rows


def _engine_metrics(
    engine: EngineConfig, views: list[RunView], intents: list[Intent], is_owned: Callable[[str], bool]
) -> EngineMetrics:
    """The funnel for one engine config. Headline rates (read, cited, FootnoteOne) are over the activated
    runs of unbranded intents, one mean per intent; the placebo floor is the cited rate over placebo intents
    (see _placebo_floor for the interval intents are judged against); activation is over every ok run with
    known activation, whatever the intent, and unconditional cited over those of unbranded intents (Ruling
    B28: a branded question names the creator, so its citations say nothing about being found), numerator
    and denominator alike."""
    ok = [v for v in views if v.status == "ok"]
    known = [v for v in ok if v.activated in ("yes", "no")]
    known_excluded = _excluded(views, activated_only=False)
    known_intents = len({v.run.intent_id for v in known})
    headline_scope = [v for v in views if _kind(v) == "unbranded"]
    unbranded_known = [v for v in known if _kind(v) == "unbranded"]
    unconditional = _cell(
        sum(v.owned_cited for v in unbranded_known),
        len(unbranded_known),
        _excluded(headline_scope, activated_only=False),
        len({v.run.intent_id for v in unbranded_known}),
        UNCONDITIONAL,
    )
    headline = _activated(headline_scope)
    headline_excluded = _excluded(headline_scope, activated_only=True)
    placebo_floor, placebo_band = _placebo_floor([v for v in views if _kind(v) == "placebo"])
    return EngineMetrics(
        engine=engine,
        surface=engine.surface,
        tool_version=engine.tool_version,
        runs_total=len(views),
        failures=dict(sorted(Counter(v.status for v in views if v.status != "ok").items())),
        activation=_cell(sum(v.activated == "yes" for v in known), len(known), known_excluded, known_intents),
        unknown_activation=len(ok) - len(known),
        read=t_value(_groups(headline, lambda v: v.owned_consulted), headline_excluded),
        cited=t_value(_groups(headline, lambda v: v.owned_cited), headline_excluded),
        footnote_one=t_value(_groups(headline, lambda v: v.owned_first), headline_excluded),
        unconditional_cited=unconditional,
        conversion=_conversion(headline, headline_excluded),
        placebo_floor=placebo_floor,
        noise=_noise(headline),
        per_intent=_intent_rows(views, intents, placebo_band),
        read_never_cited=_read_never_cited(headline, is_owned),
        cited_instead=_cited_instead(headline),
        answered_runs=sum(1 for v in headline if v.cited),
        replayed=sum(v.replayed for v in views),
        replay_failed=sum(v.replay_problem == "replay_failed" for v in views),
        unreadable_blobs=sum(v.replay_problem == "unreadable" for v in views),
        error_kinds=dict(
            sorted(Counter(v.error_kind or "kind not recorded" for v in views if v.status == "error").items())
        ),
    )


def compute(
    root: Path,
    config: ProjectConfig,
    intents: list[Intent],
    engines: list[EngineConfig],
    pages: list[Page],
    adapters: Mapping[str, Adapter],
    labels: list[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Report:
    """The funnel per engine config over the bursts select_manifests picks.

    Engines are every engine config the selected manifests ran (by config_sha, first seen first, the
    manifests in started_at order), then each engine of `engines` (the configs footnote.toml gives now) that
    none of them ran. A config's runs stay under it after its params change, so in_config tells the two
    apart. The owned set comes from the config and the page library. An engine with no runs in the window
    gets no data for every value and runs_total 0; per_intent has one row per intent in `intents`. Reads
    only: a missing `.footnote/` gives an empty report and is not created.
    """
    directory = Path(root) / ".footnote"
    stored = load_manifests(JsonlStore(directory)) if directory.is_dir() else {}
    manifests = select_manifests(stored.values(), labels, since, until)
    owned = owned_set_for(config, pages)
    views = run_views(root, adapters, manifests, owned)
    by_engine: dict[str, list[RunView]] = defaultdict(list)
    for view in views:
        by_engine[view.run.engine_config_id].append(view)
    is_owned = _owner_test(owned)
    ran: dict[str, EngineConfig] = {}
    for engine in [e for m in manifests for e in m.engines] + list(engines):
        ran.setdefault(engine.config_sha, engine)
    configured = {e.config_sha for e in engines}
    # A record per call each invocation made (a resume writes its own records); budget skips are no calls.
    calls: Counter[str] = Counter()
    if manifests:
        records = JsonlStore(directory).iter("runs", Run)
        calls.update(r.manifest_id for r in records if r.status != "budget_skip")
    bursts = [
        BurstRow(
            m.label, m.started_at, m.status, calls[m.id], m.spent_usd, m.budget_usd, m.code_version,
            m.price_table_version,
        )
        for m in manifests
    ]
    return Report(
        generated_at=utcnow(),
        labels=list(dict.fromkeys(m.label for m in manifests)),
        manifests=manifests,
        engines=[
            replace(
                _engine_metrics(e, by_engine.get(sha, []), intents, is_owned), in_config=sha in configured
            )
            for sha, e in ran.items()
        ],
        canon_version=CANON_VERSION,
        intents_total=len(intents),
        owned_domains=list(owned.domains),
        note="" if manifests else "no bursts match the selection",
        bursts=bursts,
    )
