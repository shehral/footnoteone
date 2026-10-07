"""Before and after: did the cited rate move between two windows of bursts? (METRICS 0.2.0 change verdict)"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from footnoteone.adapters.base import Adapter
from footnoteone.canon import OwnedSet
from footnoteone.config import ProjectConfig
from footnoteone.library import owned_set_for
from footnoteone.metrics import RunView, load_manifests, run_views, select_manifests
from footnoteone.plan import answers_per_intent, intents_needed
from footnoteone.schema import EngineConfig, Intent, Manifest, Page, first_difference, utcnow
from footnoteone.stats import holm, paired_sign_flip_p, t_interval, verdict
from footnoteone.store import JsonlStore


@dataclass
class EngineDiff:
    """One engine config's change between the two windows.

    Rates, the mean difference and its interval are proportions (0.25 is 25 points) over the shared intents,
    None when there are none (or, for the interval, fewer than 2). per_intent holds (intent id, rate before,
    rate after) for each shared intent, in id order. p_adj and note are set only for an engine in the Holm
    family, and intents_needed only when its verdict is "cant_tell".
    """

    engine: EngineConfig
    surface: str
    shared_intents: int
    before_rate: float | None
    after_rate: float | None
    mean_diff: float | None
    diff_lo: float | None
    diff_hi: float | None
    p_raw: float | None
    p_adj: float | None
    verdict: str
    intents_needed: int | None
    note: str
    per_intent: list[tuple[str, float, float]] = field(default_factory=list)
    # One sentence per window where this config has no runs but a burst ran the same provider and model
    # under another config, naming that config and the first param that differs; the verdict line ends with
    # them.
    config_notes: list[str] = field(default_factory=list)


@dataclass
class DiffReport:
    """The change verdict for each engine; verdict_lines holds one plain-word sentence per engine, in the
    order of engines."""

    before_labels: list[str]
    after_labels: list[str]
    engines: list[EngineDiff]
    min_shared_intents: int
    generated_at: datetime
    verdict_lines: list[str] = field(default_factory=list)


def intent_rates(views: list[RunView]) -> dict[str, float]:
    """Per unbranded intent: owned-cited runs / activated ok runs; intents with no activated run are
    absent."""
    hits: dict[str, list[int]] = defaultdict(list)
    for v in views:
        searched = v.status == "ok" and v.activated == "yes"
        if searched and v.intent is not None and v.intent.kind == "unbranded":
            hits[v.intent.id].append(1 if v.owned_cited else 0)
    return {intent_id: sum(h) / len(h) for intent_id, h in hits.items()}


def _name(e: EngineConfig) -> str:
    """An engine as verdict lines and headings name it: surface, model and the first 8 characters of its
    config sha, so two configs of one model that differ only in params stay apart."""
    return f"{e.surface} {e.model_requested} ({e.config_sha[:8]})"


def _p(p: float) -> str:
    """A Holm-adjusted p as the verdict prints it: three decimals, or "p < 0.001" below 0.0005, where three
    decimals would read 0.000 (Ruling B22)."""
    return "p < 0.001" if p < 0.0005 else f"p {p:.3f}"


def _inconsistent(d: EngineDiff) -> str:
    """When the adjusted p, not the interval, holds a Can't tell yet back (the interval of the mean change
    clears zero, yet the sign-flip test is not significant), the plain reason from the per-intent changes:
    the change is not consistent across intents. Empty otherwise."""
    if d.p_adj is None or d.p_adj < 0.05 or d.diff_lo is None or d.diff_hi is None:
        return ""
    if not (d.diff_lo > 0 or d.diff_hi < 0):
        return ""
    up = sum(after > before for _, before, after in d.per_intent)
    down = sum(after < before for _, before, after in d.per_intent)
    k = d.shared_intents
    moved = f"{up} of {k} moved up and {down} down" if up and down else f"{up + down} of {k} moved"
    return f"the change is not consistent across intents: {moved}; "


def _needed(d: EngineDiff) -> str:
    """The clause METRICS 0.2.0 shows with every Can't tell yet verdict: the intents needed to see a
    10-point move at this design, or that no size up to plan.intents_needed's cap reaches one."""
    if d.intents_needed is None:
        return "a 10-point move is out of reach below 1000 intents at this design"
    return f"about {d.intents_needed} intents would be needed to see a 10-point move at this design"


def _line(d: EngineDiff, min_intents: int) -> str:
    return " ".join([_verdict(d, min_intents), *d.config_notes])


def _verdict(d: EngineDiff, min_intents: int) -> str:
    name = _name(d.engine)
    k = d.shared_intents
    if d.verdict == "insufficient":
        return f"{name}: Not enough shared intents ({k} of {min_intents}); no verdict."
    b, a = (round(100 * x) for x in (d.before_rate or 0.0, d.after_rate or 0.0))
    if d.mean_diff is None or d.diff_lo is None or d.diff_hi is None:
        return (
            f"{name}: Can't tell yet. Fewer than 2 shared intents have spread; cited rate {b}% before and "
            f"{a}% after across {k} shared intents; {_needed(d)}."
        )
    m, lo, hi = (round(100 * x) for x in (d.mean_diff, d.diff_lo, d.diff_hi))
    span = f"mean change {m:+d} points, 95% interval {lo:+d} to {hi:+d}"
    if d.verdict == "moved":
        return (
            f"{name}: Moved. Cited rate went from {b}% to {a}% across {k} shared intents ({span}; "
            f"Holm-adjusted {_p(d.p_adj)})."
        )
    if d.verdict == "no_change":
        return (
            f"{name}: No change. The 95% interval of the change, {lo:+d} to {hi:+d} points, lies inside plus "
            f"or minus 10 points across {k} shared intents."
        )
    if d.diff_lo == d.diff_hi:
        return (
            f"{name}: Can't tell yet. The interval has no width (every shared intent changed by the same "
            f"amount, {m:+d} points) across {k} shared intents, which is no evidence of no change; "
            f"{_needed(d)}."
        )
    return (
        f"{name}: Can't tell yet. {span[0].upper() + span[1:]}, across {k} shared intents; {_inconsistent(d)}"
        f"{_needed(d)}."
    )


def _window(
    root: Path, adapters: Mapping[str, Adapter], manifests: list[Manifest], labels: list[str], owned: OwnedSet
) -> tuple[dict[str, list[RunView]], list[Manifest]]:
    """The runs of the bursts labeled `labels`, grouped by engine config sha, and those bursts' manifests.
    select_manifests keeps every burst for an empty label list, so a window with no labels holds no burst
    instead."""
    chosen = select_manifests(manifests, labels=labels) if labels else []
    grouped: dict[str, list[RunView]] = defaultdict(list)
    for v in run_views(root, adapters, chosen, owned):
        grouped[v.run.engine_config_id].append(v)
    return grouped, chosen


def _ran_instead(
    engine: EngineConfig, window: str, views: dict[str, list[RunView]], chosen: list[Manifest]
) -> str | None:
    """When `engine` has no runs in the window but a burst there ran its provider and model under another
    config, the sentence that says so and names the first param that differs; else None."""
    if views.get(engine.config_sha):
        return None
    model = (engine.provider, engine.model_requested)
    others = {
        e.config_sha: e
        for m in chosen
        for e in m.engines
        if (e.provider, e.model_requested) == model and e.config_sha != engine.config_sha
    }
    if not others:
        return None
    other = next(iter(others.values()))
    return (
        f"The {window} window ran this model as config {other.config_sha[:8]}, with "
        f"{first_difference(other, engine)}."
    )


def compute_diff(
    root: Path,
    config: ProjectConfig,
    intents: list[Intent],
    engines: list[EngineConfig],
    pages: list[Page],
    adapters: Mapping[str, Adapter],
    before: list[str],
    after: list[str],
) -> DiffReport:
    """The change verdict per engine config, in `engines` order, from the bursts labeled `before` to those
    labeled `after` (METRICS 0.2.0).

    Runs belong to an engine by config sha, so an engine whose params changed between the windows shares no
    intents. Shared intents are the ids in both windows' intent_rates: unbranded intents with an activated ok
    run in each window, so an intent missing from a window, or never activated in it, is left out of every
    figure. Each shared intent's difference is its rate after minus its rate before; before_rate and
    after_rate are means over the shared intents. p_raw = paired_sign_flip_p and the interval = t_interval
    of the differences. Holm runs over the engines with at least design.min_shared_intents shared intents;
    any other engine is "insufficient", outside the family, with no adjusted p. A family member's verdict
    is stats.verdict ("cant_tell" when it has no interval), and a "cant_tell" carries intents_needed at its
    before rate, m = plan.answers_per_intent and the family's size. Reads only: a missing `.footnote/` gives
    no shared intents and is not created.
    """
    directory = Path(root) / ".footnote"
    manifests = list(load_manifests(JsonlStore(directory)).values()) if directory.is_dir() else []
    owned = owned_set_for(config, pages)
    before_views, before_bursts = _window(root, adapters, manifests, before, owned)
    after_views, after_bursts = _window(root, adapters, manifests, after, owned)
    min_k = config.design.min_shared_intents
    m = answers_per_intent(intents, config.design.reps, config.design.paraphrases)
    rows: list[EngineDiff] = []
    for e in engines:
        rb = intent_rates(before_views.get(e.config_sha, []))
        ra = intent_rates(after_views.get(e.config_sha, []))
        shared = sorted(set(rb) & set(ra))
        diffs = [ra[i] - rb[i] for i in shared]
        interval = t_interval(diffs) if len(diffs) >= 2 else None
        mean_diff, diff_lo, diff_hi = interval or (None, None, None)
        rows.append(
            EngineDiff(
                engine=e,
                surface=e.surface,
                shared_intents=len(shared),
                before_rate=sum(rb[i] for i in shared) / len(shared) if shared else None,
                after_rate=sum(ra[i] for i in shared) / len(shared) if shared else None,
                mean_diff=mean_diff,
                diff_lo=diff_lo,
                diff_hi=diff_hi,
                p_raw=paired_sign_flip_p(diffs) if shared else None,
                p_adj=None,
                verdict="insufficient",
                intents_needed=None,
                note="",
                per_intent=[(i, rb[i], ra[i]) for i in shared],
                config_notes=[
                    note
                    for note in (
                        _ran_instead(e, "before", before_views, before_bursts),
                        _ran_instead(e, "after", after_views, after_bursts),
                    )
                    if note
                ],
            )
        )
    family = [r for r in rows if r.shared_intents >= min_k]
    adjusted = holm([r.p_raw for r in family])
    for r, p_adj in zip(family, adjusted, strict=True):
        r.p_adj = p_adj
        if r.diff_lo is None or r.diff_hi is None:
            r.verdict = "cant_tell"
        else:
            r.verdict = verdict(r.diff_lo, r.diff_hi, p_adj, r.shared_intents, min_intents=min_k)
        if r.verdict == "cant_tell":
            r.intents_needed = intents_needed(
                r.before_rate or 0.0, m, config.design.icc, len(family), min_intents=min_k
            )
        r.note = f"family of {len(family)} engine(s) under Holm; m = {m:g} answers per intent per window"
    report = DiffReport(list(before), list(after), rows, min_k, utcnow())
    report.verdict_lines = [_line(r, min_k) for r in rows]
    return report


def render_diff_markdown(d: DiffReport) -> str:
    """The verdict lines, what they rest on, and per engine a table of each shared intent's rates."""
    windows = f"before: {', '.join(d.before_labels)}; after: {', '.join(d.after_labels)}"
    lines = [f"# Change between windows ({windows})", ""]
    lines += d.verdict_lines
    lines += [
        "",
        "Shared intents are unbranded intents with at least one activated answer in both windows; no verdict "
        f"under {d.min_shared_intents}. Statistics: paired sign-flip test per engine, Holm across engines, "
        "Student t interval of the mean change. API answers, not the consumer apps.",
        "",
    ]
    for e in d.engines:
        if e.per_intent:
            lines += [f"## {_name(e.engine)}", "", "| Intent | Before | After |", "|---|---|---|"]
            lines += [f"| {i} | {round(100 * b)}% | {round(100 * a)}% |" for i, b, a in e.per_intent]
            lines.append("")
    return "\n".join(lines)
