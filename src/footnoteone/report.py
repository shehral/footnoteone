"""The report: metrics.compute's funnel per engine, rendered as Markdown and as one static HTML page.

Every value is shown in plain words first: a percentage, "no data", "suppressed: <why>", "not exposed" or
"cannot judge". Its statistics (method, numerator of denominator, intents, interval, what the denominator
left out and the metric's note) sit one click behind it in the HTML, inside
`<details><summary>statistics</summary>`, and after it in parentheses in the Markdown. Nothing here computes
a metric: the templates format what metrics.compute returned, and a value that is None never shows as 0.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from footnoteone.metrics import EngineMetrics, HostRow, IntentRow, MetricValue, Report
from footnoteone.schema import first_difference

_REPORTS_DIR = "reports"
_NOT_EXPOSED = "not exposed"  # the note a read rate carries when the engine exposes no consulted list
_UNSAFE_IN_FOLDER = re.compile(r"[^A-Za-z0-9._-]+")


def _pct(x: float) -> str:
    """A proportion as whole percent, rounded half to even. A Student t bound outside 0 to 1 keeps its sign:
    METRICS 0.2.0 shows t intervals unclipped."""
    return str(round(x * 100))


def _percent(x: float) -> str:
    """A rate or share as whole percent; one strictly between 0 and 1 never reads as 0% or 100%."""
    shown = round(x * 100)
    if shown == 0 < x:
        return "<1%"
    if shown == 100 and x < 1:
        return ">99%"
    return f"{shown}%"


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _number(x: float) -> str:
    """A numerator: a count as an integer, a sum of Jaccards to two decimals."""
    if isinstance(x, float) and not x.is_integer():
        return f"{x:.2f}"
    return str(int(x))


def _missing(mv: MetricValue) -> str:
    """The words for a value that is None: "not exposed" when the engine gives nothing to count, the reason a
    value counted but not shown is suppressed, else "no data"."""
    note = mv.note.strip()
    if note.startswith(_NOT_EXPOSED):
        return _NOT_EXPOSED
    if mv.denominator:  # counted, yet no value: suppressed, and the note says why
        if note.startswith("suppressed"):
            return note
        return f"suppressed: {note}" if note else "suppressed"
    return "no data"


def _plain(mv: MetricValue) -> str:
    """The value in plain words without its interval, as the HTML shows it beside its statistics.

    A Student t interval of zero width (every intent gave the same value) is "cannot judge" (METRICS 0.2.0),
    never a precise value; a value with no interval at all (one intent) says so.
    """
    if mv.value is None:
        return _missing(mv)
    value = _percent(mv.value)
    if mv.lo is None or mv.hi is None:
        return f"{value} (no interval)"
    if mv.lo == mv.hi:
        return f"cannot judge (every intent at {value})"
    return value


def fmt_pct(mv: MetricValue) -> str:
    """The value in plain words with its 95% interval in points: "32% (18 to 46)". Otherwise "no data",
    "suppressed: <why>", "not exposed", "cannot judge (every intent at 100%)" for a zero-width interval, or
    "38% (no interval)"."""
    words = _plain(mv)
    if mv.value is None or mv.lo is None or mv.hi is None or mv.lo == mv.hi:
        return words
    return f"{words} ({_pct(mv.lo)} to {_pct(mv.hi)})"


def fmt_count(mv: MetricValue, unit: str = "run") -> str:
    """What a value counted: numerator of denominator, then the intents behind it when known, as in
    "8 of 14 runs, 4 intents"; "no runs counted" when nothing was. `unit` is what the denominator counts:
    runs, or pairs for the flip rate."""
    if not mv.denominator or mv.numerator is None:
        return f"no {unit}s counted"
    text = f"{_number(mv.numerator)} of {_plural(mv.denominator, unit)}"
    if mv.n_intents is not None:
        text += f", {_plural(mv.n_intents, 'intent')}"
    return text


def pts(x: float | None) -> str:
    """A difference of two proportions in percentage points with its sign: 0.4 is "+40 points"."""
    if x is None:
        return "no data"
    n = round(x * 100)
    return f"{n:+d} {'point' if abs(n) == 1 else 'points'}" if n else "0 points"


def _jaccard_count(mv: MetricValue) -> str:
    """A mean Jaccard's numerator and denominator: the sum of the pairs' Jaccards over the pairs."""
    if not mv.denominator or mv.numerator is None:
        return "no pairs counted"
    text = f"Jaccard sum {mv.numerator:.2f} over {_plural(mv.denominator, 'pair')}"
    if mv.n_intents is not None:
        text += f", {_plural(mv.n_intents, 'intent')}"
    return text


def _stats(
    mv: MetricValue, unit: str = "run", jaccard: bool = False, interval: bool = True, note: bool = True
) -> str:
    """The statistics behind a value: method; numerator of denominator and intents; the 95% interval (left
    out with `interval=False`, where fmt_pct already shows it); what the denominator left out, by reason;
    and the metric's note unless `note=False` or _plain() already says it."""
    parts = [mv.method, _jaccard_count(mv) if jaccard else fmt_count(mv, unit)]
    if interval and mv.value is not None and mv.lo is not None and mv.hi is not None:
        bounds = f"95% interval {_pct(mv.lo)} to {_pct(mv.hi)}"
        parts.append(f"{bounds}: zero width, so cannot judge" if mv.lo == mv.hi else bounds)
    if mv.excluded:
        reasons = ", ".join(f"{n} {reason.replace('_', ' ')}" for reason, n in mv.excluded.items())
        parts.append(f"left out: {reasons}")
    if note and mv.note and mv.note != _plain(mv):
        parts.append(mv.note)
    return "; ".join(parts)


def _engine_title(e: EngineMetrics) -> str:
    """Surface and model, then the tool version and the first 8 characters of the engine's config sha."""
    tool = e.tool_version or "default"
    return f"{e.surface} {e.engine.model_requested} (tool version {tool}, config {e.engine.config_sha[:8]})"


def _same_model(e: EngineMetrics, report: Report) -> list[EngineMetrics]:
    """The report's other engine configs of the same provider and model."""
    model = (e.engine.provider, e.engine.model_requested)
    return [
        other
        for other in report.engines
        if other is not e and (other.engine.provider, other.engine.model_requested) == model
    ]


def _engine_notes(e: EngineMetrics, report: Report) -> list[str]:
    """What a reader must know before an engine's numbers: that footnote.toml no longer gives this config
    (and the config it gives instead), and that a config has no runs in the selected bursts (and which config
    of the same model has them). Each config's runs stay under its own config sha, so a params change starts
    a new section rather than hiding the old runs."""
    notes = []
    if not e.in_config:
        now = next((other.engine for other in _same_model(e, report) if other.in_config), None)
        if now is None:
            text = "Not in footnote.toml now: footnote.toml no longer runs this engine"
        else:
            text = (
                f"Not in footnote.toml now: footnote.toml runs {now.surface} {now.model_requested} as config "
                f"{now.config_sha[:8]}, with {first_difference(now, e.engine)}"
            )
        shown = "; the runs below are this config's, shown as they were." if e.runs_total else "."
        notes.append(text + shown)
    if not e.runs_total:
        text = "There is no data for this engine: it has no runs in the selected bursts."
        ran = next((other.engine for other in _same_model(e, report) if other.runs_total), None)
        if ran is not None:
            text += (
                f" They ran {ran.surface} {ran.model_requested} as config {ran.config_sha[:8]}, with "
                f"{first_difference(ran, e.engine)}, shown in its own section."
            )
        notes.append(text)
    return notes


def _summary(e: EngineMetrics) -> str:
    """One engine's lead sentence: its cited rate with the 95% interval and the intents behind it, or no
    data and the intents behind it (none)."""
    title = _engine_title(e) + ("" if e.in_config else ", not in footnote.toml now")
    intents = _plural(e.cited.n_intents or 0, "intent")
    if e.cited.value is None:
        return f"{title}: no data ({intents})."
    return f"{title}: cited rate {fmt_pct(e.cited)} over {intents}."


def _money(x: float, budget: float) -> str:
    """An amount in USD: four decimals under a budget below 1 USD, where cents matter, else two."""
    return f"{x:.4f} USD" if budget < 1 else f"{x:.2f} USD"


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def _failures(e: EngineMetrics) -> str:
    """The runs that did not count, by reason, over every run of the engine, the errors broken down by their
    error kind when the runs recorded one: "2 error (1 http, 1 parse), 1 timeout (of 24 runs)"."""
    if not e.failures:
        return "none"
    kinds = ", ".join(f"{n} {kind.replace('_', ' ')}" for kind, n in e.error_kinds.items())
    recorded = any(kind != "kind not recorded" for kind in e.error_kinds)
    reasons = ", ".join(
        f"{n} {reason.replace('_', ' ')}" + (f" ({kinds})" if reason == "error" and recorded else "")
        for reason, n in e.failures.items()
    )
    return f"{reasons} (of {_plural(e.runs_total, 'run')})"


def _funnel(e: EngineMetrics) -> list[tuple[str, str, MetricValue]]:
    """The funnel rows in order, each as (step, what it counts, value). Read, cited, FootnoteOne and
    conversion count searched answers to unbranded intents; activation counts every answer whose activation
    is known, whatever the intent, and unconditional cited every such answer to an unbranded intent (the
    templates' reading guide says so)."""
    unconditional = "answers to unbranded intents that cited one of your pages, searched or not"
    conversion = "searched answers that cited one of your pages, among those that read one"
    return [
        ("Activation", "answers that searched the web", e.activation),
        ("Read", "searched answers that consulted one of your pages", e.read),
        ("Cited", "searched answers that cited one of your pages", e.cited),
        ("FootnoteOne", "searched answers whose first citation is one of your pages", e.footnote_one),
        ("Unconditional cited", unconditional, e.unconditional_cited),
        ("Conversion", conversion, e.conversion),
    ]


def _noise(e: EngineMetrics) -> list[tuple[str, str, MetricValue, bool]]:
    """The noise floor rows, each as (measure, what it compares, value, whether it is a mean Jaccard)."""
    return [
        (
            "Within-burst Jaccard",
            "overlap of the URLs cited by repeats of one wording in the same burst",
            e.noise.within_burst_jaccard,
            True,
        ),
        (
            "Between-burst Jaccard",
            "overlap of the URLs cited for one wording in different bursts",
            e.noise.between_burst_jaccard,
            True,
        ),
        (
            "Flip rate",
            "repeat pairs where one answer cited you and the other did not",
            e.noise.flip_rate,
            False,
        ),
    ]


def _flag(row: IntentRow, placebo_floor: MetricValue) -> str:
    """The "indistinguishable from placebo" cell. Only unbranded intents are judged; an intent with no cited
    value, or an engine with no placebo floor, has nothing to judge against."""
    if row.kind != "unbranded":
        return "not judged"
    if row.cited.value is None:
        return "no data"
    if placebo_floor.value is None:
        return "no placebo data"
    return "yes" if row.indistinguishable_from_placebo else "no"


def _answered_runs(hosts: list[HostRow]) -> int | None:
    """The denominator the cited-instead shares share: activated unbranded runs that cited anything."""
    return round(hosts[0].cited_runs / hosts[0].share) if hosts and hosts[0].share else None


def _md_cell(text: object) -> str:
    """Text safe inside a Markdown table cell or list item: a newline would end the row and a pipe the
    cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


_ENV = Environment(
    loader=PackageLoader("footnoteone", "templates"),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
_ENV.filters.update(
    fmt_pct=fmt_pct,
    fmt_count=fmt_count,
    pts=pts,
    plain=_plain,
    stats=_stats,
    percent=_percent,
    plural=_plural,
    engine_title=_engine_title,
    failures=_failures,
    funnel=_funnel,
    noise=_noise,
    engine_notes=_engine_notes,
    summary=_summary,
    money=_money,
    utc=_utc,
    flag=_flag,
    answered_runs=_answered_runs,
    md_cell=_md_cell,
)
# Markdown is plain text, so nothing in it is escaped. The overlay is made before any template is loaded:
# it copies the parent's template cache, and a template compiled with escaping must not reach it.
_TEXT_ENV = _ENV.overlay(autoescape=False)


def _context(report: Report, diff: object | None) -> dict[str, Any]:
    """What both templates read. A diff contributes only its verdict lines (None when no diff is given)."""
    lines = None if diff is None else [str(line) for line in getattr(diff, "verdict_lines", None) or []]
    return {
        "report": report,
        "generated": report.generated_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "diff_lines": lines,
    }


def render_markdown(report: Report, diff: object | None = None) -> str:
    """The report as Markdown: one `##` section per engine, statistics in parentheses after each value."""
    return _TEXT_ENV.get_template("report.md.j2").render(_context(report, diff)).rstrip("\n") + "\n"


def render_html(report: Report, diff: object | None = None) -> str:
    """The report as one self-contained HTML page: inline CSS, no scripts and no external assets, with each
    value's statistics in a <details> block beside it."""
    return _ENV.get_template("report.html.j2").render(_context(report, diff)).rstrip("\n") + "\n"


def _folder_name(report: Report) -> str:
    """The burst label when the report covers exactly one label, else the UTC date it was generated
    (YYYY-MM-DD). A label is made safe as one folder name: characters other than letters, digits, dot,
    underscore and hyphen become a hyphen, leading and trailing dots and hyphens go (so no label names a
    parent or hidden folder), and a label left empty falls back to the date."""
    if len(report.labels) == 1:
        name = _UNSAFE_IN_FOLDER.sub("-", report.labels[0]).strip("-.")
        if name:
            return name
    return report.generated_at.astimezone(UTC).date().isoformat()


def write_report(
    root: Path, report: Report, diff: object | None = None, out_dir: Path | None = None
) -> tuple[Path, Path]:
    """Write report.md and report.html into `out_dir`, by default reports/<label or date> under `root` (see
    _folder_name), creating the folder. Both are rendered before either is written. Returns their paths,
    Markdown first."""
    folder = Path(out_dir) if out_dir is not None else Path(root) / _REPORTS_DIR / _folder_name(report)
    markdown, page = render_markdown(report, diff), render_html(report, diff)
    folder.mkdir(parents=True, exist_ok=True)
    md_path, html_path = folder / "report.md", folder / "report.html"
    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(page, encoding="utf-8")
    return md_path, html_path
