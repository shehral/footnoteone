import re
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from types import SimpleNamespace

from helpers import OTHER, OWN, build_store, shifted_project

from footnoteone.config import ProjectConfig, load_config, load_intents
from footnoteone.metrics import (
    EngineMetrics,
    HostRow,
    IntentRow,
    MetricValue,
    NoiseFloor,
    PageRow,
    Report,
    compute,
)
from footnoteone.report import fmt_count, fmt_pct, pts, render_html, render_markdown, write_report
from footnoteone.schema import EngineConfig, Intent, Prompt

E1 = EngineConfig(provider="openai", model_requested="gpt-5-mini")
E2 = EngineConfig(provider="anthropic", model_requested="claude-sonnet-4-5")
CFG = ProjectConfig.model_validate(
    {
        "site": {"url": "https://example.org"},
        "engines": [{"provider": "openai", "model": "gpt-5-mini"}],
        "design": {"budget_usd_per_burst": 1.0},
    }
)


def intents():
    return [
        Intent(id=f"u{k}", label=f"topic {k}", prompts=[Prompt(id=f"u{k}p0", text="q")]) for k in range(8)
    ] + [Intent(id="p0", label="placebo", kind="placebo", prompts=[Prompt(id="p0p0", text="q")])]


def pattern(label, intent, prompt, engine, rep):
    if engine == E2:
        return None
    cites = intent.id in ("u0", "u1", "u2")
    return ("ok", "yes", [OWN, OTHER], [OWN] if cites else [OTHER])


def test_formatters():
    assert fmt_pct(MetricValue(0.32, 0.18, 0.46, 8, 25, {}, 5, "Student t over intents")) == "32% (18 to 46)"
    assert fmt_pct(MetricValue.no_data("Wilson 95%")) == "no data"
    note = "suppressed: fewer than 20 runs read an owned page"
    suppressed = MetricValue(None, None, None, 3, 10, {}, None, "Wilson 95%", note=note)
    assert fmt_pct(suppressed).startswith("suppressed")
    assert fmt_count(MetricValue(0.32, 0.18, 0.46, 8, 25, {}, 5, "m")) == "8 of 25 runs, 5 intents"


def test_markdown_and_html_contain_required_sections(tmp_path):
    build_store(tmp_path, ["w1"], [E1, E2], intents(), reps=2, pattern=pattern)
    report = compute(tmp_path, CFG, intents(), [E1, E2], pages=[], adapters={}, labels=["w1"])
    md, html = render_markdown(report), render_html(report)
    for text, heading in ((md, "\n## "), (html, "<h2>")):
        assert "FootnoteOne report" in text and "API answers, not the consumer apps" in text
        assert "api:openai" in text and "gpt-5-mini" in text and "api:anthropic" in text
        assert "no data for this engine" in text
        assert "Read but never cited" in text and "Cited instead" in text and "other.net" in text
        assert "Placebo floor" in text and "Noise floor" in text and "flip rate" in text.lower()
        assert "topic 0" in text and "indistinguishable" in text
        assert "\u2014" not in text
        # The brief's bare "0%" check guards empty denominators, but this fixture also has real zeros (the
        # flip rate is 0 of 8 pairs, the placebo floor 0 of 2 runs). So it checks the empty denominators
        # themselves: the engine with no runs (the last section) shows no percentage at all, and the
        # between-burst Jaccard, which has no pairs with one burst, reads "no data".
        assert "%" not in text.split(f"{heading}api:anthropic", 1)[1]
    assert md_rows(md)["Between-burst Jaccard"] == html_value(html, "Between-burst Jaccard") == "no data"
    assert "<details>" in html and "statistics" in html and "<script" not in html
    assert "http" not in html.split("<style")[1].split("</style>")[0]
    assert "(" in md and "intents" in md


def test_write_report_paths(tmp_path):
    build_store(tmp_path, ["w1"], [E1], intents(), reps=1, pattern=pattern)
    report = compute(tmp_path, CFG, intents(), [E1], pages=[], adapters={}, labels=["w1"])
    md_path, html_path = write_report(tmp_path, report)
    assert md_path == tmp_path / "reports" / "w1" / "report.md" and html_path.exists()


# Behaviour lines of the brief, and the two rendering rules from the metrics review, that the tests above
# leave unexercised.


def fixture(root, engines=(E1, E2), labels=("w1",), cast=None, pat=pattern, reps=2):
    cast = cast or intents()
    build_store(root, list(labels), list(engines), cast, reps=reps, pattern=pat)
    return compute(root, CFG, cast, list(engines), pages=[], adapters={})


def md_table(md):
    """Every Markdown table row by its first cell, as the list of its other cells. A funnel or noise row,
    whose first cell reads "Name: meaning", is keyed by the name."""
    rows = {}
    for line in md.splitlines():
        if line.startswith("| "):
            cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
            rows[cells[0].split(":")[0]] = cells[1:]
    return rows


def md_rows(md):
    """The value cell (the second cell) of every Markdown table row, by name."""
    return {name: cells[0] for name, cells in md_table(md).items() if cells}


class TableCells(HTMLParser):
    """The th and td cells of a page, row by row. Each cell is [words, statistics]: its text outside any
    details element, and the text of its details element less the summary. A row header's meaning span is
    left out."""

    def __init__(self):
        super().__init__()
        self.rows, self.open, self.in_cell = [], [], False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        elif tag in ("th", "td"):
            self.rows[-1].append(["", ""])
            self.in_cell = True
        elif tag in ("details", "summary") or ("class", "meaning") in attrs:
            self.open.append(tag)

    def handle_endtag(self, tag):
        if tag in ("th", "td"):
            self.in_cell = False
        elif self.open and self.open[-1] == tag:
            self.open.pop()

    def handle_data(self, data):
        if self.in_cell and "summary" not in self.open and "span" not in self.open:
            self.rows[-1][-1][1 if "details" in self.open else 0] += data


def html_rows(html):
    """Every HTML table row by its header cell's words, as the list of its other cells, each a (words,
    statistics) pair."""
    parser = TableCells()
    parser.feed(html)
    return {row[0][0].strip(): [(w.strip(), s.strip()) for w, s in row[1:]] for row in parser.rows if row}


def html_value(html, name):
    """The plain-word value the HTML shows beside the row header `name`."""
    return html_rows(html)[name][0][0]


def test_fmt_pct_never_shows_a_missing_or_collapsed_value_as_a_number():
    t = "Student t over intents"
    assert fmt_pct(MetricValue(1.0, 1.0, 1.0, 16, 16, {}, 8, t)) == "cannot judge (every intent at 100%)"
    one = MetricValue(0.5, None, None, 3, 6, {}, 1, t, "one intent only, so no interval")
    assert fmt_pct(one) == "50% (no interval)"
    assert fmt_pct(MetricValue(0.375, -0.058, 0.808, 6, 16, {}, 8, t)) == "38% (-6 to 81)"  # t is unclipped
    assert fmt_pct(MetricValue(0.004, 0.001, 0.022, 1, 250, {}, None, "Wilson 95%")) == "<1% (0 to 2)"
    assert fmt_pct(MetricValue(0.996, 0.978, 0.999, 249, 250, {}, None, "Wilson 95%")) == ">99% (98 to 100)"
    assert fmt_pct(MetricValue.no_data(t, note="not exposed")) == "not exposed"
    assert fmt_pct(MetricValue(None, None, None, 4, 9, {}, 2, "Wilson 95%", note="few")) == "suppressed: few"
    assert fmt_count(MetricValue.no_data("Wilson 95%")) == "no runs counted"
    flips = MetricValue(0.0, 0.0, 0.32, 0, 8, {}, 8, "Wilson 95%")
    assert fmt_count(flips, "pair") == "0 of 8 pairs, 8 intents"
    assert fmt_count(MetricValue(1.0, 0.21, 1.0, 1, 1, {}, None, "Wilson 95%")) == "1 of 1 run"
    assert (pts(0.4), pts(-0.01), pts(0.0), pts(None)) == ("+40 points", "-1 point", "0 points", "no data")


def test_zero_width_reads_cannot_judge_and_the_placebo_note_sits_beside_the_floor(tmp_path):
    report = fixture(tmp_path)
    md, html = render_markdown(report), render_html(report)
    assert md_rows(md)["Read"] == "cannot judge (every intent at 100%)"  # every intent read an owned page
    assert md_rows(md)["Within-burst Jaccard"] == "cannot judge (every intent at 100%)"
    assert html_value(html, "Read") == "cannot judge (every intent at 100%)" and "100% (100 to 100)" not in md
    band = "intents are judged against Wilson 95% over the placebo runs (0 of 2 cited): 0.000 to 0.658"
    for text in (md, html):
        floor = text[text.index("Placebo floor") : text.index("Noise floor")]
        assert band in floor and band not in "".join(re.findall(r"<details>.*?</details>", floor))


def test_empty_denominators_show_no_data_beside_real_zeros(tmp_path):
    report = fixture(tmp_path, engines=(E1,), pat=lambda *a: ("ok", "no", [], []))  # answers never search
    md, html = render_markdown(report), render_html(report)
    rows = md_rows(md)
    # Activation counts all 18 runs; unconditional cited the 16 runs of unbranded intents (Ruling B28).
    assert rows["Activation"] == "0% (0 to 18)" and rows["Unconditional cited"] == "0% (0 to 19)"
    for step in ("Read", "Cited", "FootnoteOne", "Conversion", "Within-burst Jaccard", "Flip rate"):
        assert rows[step] == "no data"
    assert "left out: 16 not activated" in md and html.count('<span class="value">no data</span>') >= 6


def test_diff_section_prints_the_verdict_lines_verbatim(tmp_path):
    report = fixture(tmp_path)
    line = "api:openai gpt-5-mini (config ea5eb3a0): Can't tell yet <with> 10 shared intents"
    diff = SimpleNamespace(verdict_lines=[line])
    md, html = render_markdown(report, diff), render_html(report, diff)
    assert f"- {line}" in md and line in unescape(html) and "<with>" not in html
    assert "Change between windows" in html and "Change between windows" not in render_markdown(report)


def test_html_escapes_labels_and_markdown_keeps_table_rows_whole(tmp_path):
    cast = [Intent(id="u0", label="<script>alert(1)</script> | x", prompts=[Prompt(id="u0p0", text="q")])]
    report = fixture(tmp_path, engines=(E1,), cast=cast)
    html, md = render_html(report), render_markdown(report)
    assert "<script" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "| <script>alert(1)</script> \\| x | unbranded |" in md


def test_write_report_folder_is_the_date_for_several_labels_and_a_safe_name_for_one(tmp_path):
    both = fixture(tmp_path / "a", engines=(E1,), labels=("w1", "w2"), reps=1)
    md_path, _ = write_report(tmp_path / "a", both)
    assert md_path.parent == tmp_path / "a" / "reports" / both.generated_at.date().isoformat()
    odd = fixture(tmp_path / "b", engines=(E1,), labels=("../up",), reps=1)
    assert write_report(tmp_path / "b", odd)[0] == tmp_path / "b" / "reports" / "up" / "report.md"
    md_path, html_path = write_report(tmp_path / "b", odd, out_dir=tmp_path / "out")
    assert md_path == tmp_path / "out" / "report.md"
    assert html_path.read_text(encoding="utf-8").startswith("<!doctype html>")


def lists(text):
    """The "Read but never cited" and "Cited instead" sections of a report with one engine, in either
    format."""
    read, cited = text.index("Read but never cited"), text.index("Cited instead")
    return text[read:cited], text[cited:]


def test_lists_read_no_data_when_no_searched_unbranded_answer_was_counted(tmp_path):
    """Both lists draw on searched answers to unbranded intents. With none counted (every run failed, or no
    answer searched) they read no data, as the funnel does, and never claim "None"; with answers counted,
    "None" is a finding and stays."""
    gone = "No data: no searched answer to an unbranded intent was counted."
    claims = ("None: no page of yours was read", "None: no other host was cited")
    failed, silent = (lambda *a: ("error", "unknown", [], [])), (lambda *a: ("ok", "no", [], []))
    for name, pat in (("failed", failed), ("silent", silent)):
        report = fixture(tmp_path / name, engines=(E1,), pat=pat)
        for text in (render_markdown(report), render_html(report)):
            read, cited = lists(text)
            assert gone in read and gone in cited, name
            assert not any(claim in text for claim in claims), name
    own_only = fixture(tmp_path / "own", engines=(E1,), pat=lambda *a: ("ok", "yes", [OWN], [OWN]))
    for text in (render_markdown(own_only), render_html(own_only)):
        assert all(claim in text for claim in claims) and gone not in text


# The brief's required content, line by line. The report below is built by hand so that failures, unknown
# activation and replays are non-zero and the numbers differ from one another: each assertion can match
# only its own line, so dropping a line, a number or a statistic from either template fails a test.

SONNET = EngineConfig(
    provider="anthropic", model_requested="claude-sonnet-4-5", tool_version="web_search_20250305"
)
T, W = "Student t over intents", "Wilson 95%"


def mv(value, lo, hi, k, n, n_intents, method=W, excluded=None, note=""):
    return MetricValue(value, lo, hi, k, n, excluded or {}, n_intents, method, note)


def stated():
    """One engine with failures, unknown activation, replays, a suppressed conversion, a page read but never
    cited and two hosts cited instead. The numbers are chosen to differ from one another, not to add up."""
    failed, idle = {"error": 2, "timeout": 1, "unknown": 3}, {"not activated": 2}
    full, none, three = mv(1.0, 0.51, 1.0, 4, 4, 1), mv(0.0, 0.0, 0.49, 0, 4, 1), mv(0.75, 0.3, 0.95, 3, 4, 1)
    nothing = MetricValue(None, None, None, None, None, {"error": 2}, None, W, "no data")
    engine = EngineMetrics(
        engine=SONNET,
        surface=SONNET.surface,
        tool_version=SONNET.tool_version,
        runs_total=24,
        failures={"error": 2, "timeout": 1},
        activation=mv(0.75, 0.53, 0.89, 12, 16, 6, excluded=failed),
        unknown_activation=3,
        read=mv(0.6, 0.27, 0.93, 6, 10, 4, T, idle, "sensitivity: bootstrap 0.375 to 0.875"),
        cited=mv(0.45, 0.12, 0.78, 4, 10, 4, T, idle),
        footnote_one=mv(0.3, 0.05, 0.55, 3, 10, 4, T, idle),
        unconditional_cited=mv(0.3125, 0.14, 0.56, 5, 16, 6, excluded=failed),
        conversion=mv(
            None, None, None, 4, 6, 3, note="suppressed: fewer than 20 runs read an owned page (6)"
        ),
        placebo_floor=mv(0.25, 0.1, 0.4, 1, 4, 2, T),
        noise=NoiseFloor(
            mv(0.72, 0.55, 0.89, 5.04, 7, 4, T),
            mv(0.5, 0.31, 0.69, 4.0, 8, 4, T, {"both cited sets empty": 1}),
            mv(0.2, 0.08, 0.42, 3, 15, 4),
        ),
        per_intent=[
            IntentRow("u0", "pricing pages", "unbranded", 4, 4, full, full, three, False),
            IntentRow("u1", "setup guide", "unbranded", 4, 4, full, none, none, True),
            IntentRow("u2", "refunds", "unbranded", 0, 0, nothing, nothing, nothing, False),
            IntentRow("b0", "brand name", "branded", 4, 4, full, full, full, False),
            IntentRow("p0", "weather", "placebo", 4, 4, none, none, none, False),
        ],
        read_never_cited=[PageRow("https://example.org/faq", 5, 0)],
        cited_instead=[HostRow("other.net", 3, 0.3), HostRow("wiki.example.com", 2, 0.2)],
        replayed=4,
    )
    generated = datetime(2026, 10, 5, 14, 30, tzinfo=UTC)
    return Report(generated, ["w1", "w2"], [], [engine], 7, 5, ["example.org"], "")


# Each value row of the stated report in Markdown: the value with its interval, then its statistics in
# parentheses (method; numerator of denominator, intents; what was left out; the metric's note).
STATED_MD = {
    "Activation": [
        "75% (53 to 89)",
        "(Wilson 95%; 12 of 16 runs, 6 intents; left out: 2 error, 1 timeout, 3 unknown)",
    ],
    "Read": [
        "60% (27 to 93)",
        "(Student t over intents; 6 of 10 runs, 4 intents; left out: 2 not activated;"
        " sensitivity: bootstrap 0.375 to 0.875)",
    ],
    "Cited": [
        "45% (12 to 78)",
        "(Student t over intents; 4 of 10 runs, 4 intents; left out: 2 not activated)",
    ],
    "FootnoteOne": [
        "30% (5 to 55)",
        "(Student t over intents; 3 of 10 runs, 4 intents; left out: 2 not activated)",
    ],
    "Unconditional cited": [
        "31% (14 to 56)",
        "(Wilson 95%; 5 of 16 runs, 6 intents; left out: 2 error, 1 timeout, 3 unknown)",
    ],
    "Conversion": [
        "suppressed: fewer than 20 runs read an owned page (6)",
        "(Wilson 95%; 4 of 6 runs, 3 intents)",
    ],
    "Placebo floor": ["25% (10 to 40)", "(Student t over intents; 1 of 4 runs, 2 intents)"],
    "Within-burst Jaccard": [
        "72% (55 to 89)",
        "(Student t over intents; Jaccard sum 5.04 over 7 pairs, 4 intents)",
    ],
    "Between-burst Jaccard": [
        "50% (31 to 69)",
        "(Student t over intents; Jaccard sum 4.00 over 8 pairs, 4 intents;"
        " left out: 1 both cited sets empty)",
    ],
    "Flip rate": ["20% (8 to 42)", "(Wilson 95%; 3 of 15 pairs, 4 intents)"],
}


def as_html(value, stats):
    """The brief's rule for the HTML form of a Markdown value row: the plain words stay beside the value, and
    the interval moves into the statistics, after what was counted."""
    words, interval = re.fullmatch(r"(.*?)(?: \((-?\d+ to -?\d+)\))?", value).groups()
    parts = stats.removeprefix("(").removesuffix(")").split("; ")
    if interval:
        parts.insert(2, f"95% interval {interval}")
    return words, "; ".join(parts)


def test_header_engine_heading_and_run_counts():
    md, html = render_markdown(stated()), render_html(stated())
    assert "- Generated: 2026-10-05 14:30 UTC\n- Bursts: w1, w2\n- Canonical URL rules: version 7\n" in md
    meta = (("Generated", "2026-10-05 14:30 UTC"), ("Bursts", "w1, w2"), ("Canonical URL rules", "version 7"))
    assert all(f"<dt>{term}</dt><dd>{value}</dd>" in html for term, value in meta)
    sha = SONNET.config_sha[:8]
    title = f"api:anthropic claude-sonnet-4-5 (tool version web_search_20250305, config {sha})"
    assert f"\n## {title}\n" in md and f"<h2>{title}</h2>" in html
    counts = (
        "Failures by reason: 2 error, 1 timeout (of 24 runs). Unknown activation: 3 runs, counted here and"
        " never folded into yes or no. Replayed: 4 runs re-read with the current parser. Replay failed: 0"
        " runs; unreadable raw response: 0 runs; each keeps its stored reading."
    )
    assert f"\nRuns: 24. {counts}\n" in md and f'<p>Runs: <span class="num">24</span>. {counts}</p>' in html


def test_the_run_counts_show_replays_that_failed_and_raw_responses_that_could_not_be_read():
    """F4: both counts sit beside the replayed count, in both formats."""
    report = stated()
    report.engines[0].replay_failed, report.engines[0].unreadable_blobs = 1, 2
    line = "Replay failed: 1 run; unreadable raw response: 2 runs; each keeps its stored reading."
    assert line in render_markdown(report) and line in render_html(report)


def test_the_failures_line_breaks_errors_down_by_kind():
    report = stated()
    report.engines[0].error_kinds = {"all_searches_failed": 1, "http": 1}
    line = "Failures by reason: 2 error (1 all searches failed, 1 http), 1 timeout (of 24 runs)."
    assert line in render_markdown(report) and line in render_html(report)


def test_every_value_has_its_statistics_after_it_in_markdown_and_one_click_behind_it_in_html():
    md, page = render_markdown(stated()), render_html(stated())
    md_cells, html_cells = md_table(md), html_rows(page)
    for name, (value, stats) in STATED_MD.items():
        assert md_cells[name] == [value, stats], name
        assert html_cells[name] == [as_html(value, stats)], name
    activation = (
        "Wilson 95%; 12 of 16 runs, 6 intents; 95% interval 53 to 89;"
        " left out: 2 error, 1 timeout, 3 unknown"
    )
    assert html_cells["Activation"] == [("75%", activation)]  # as_html, spelled out once
    for row in html_cells.values():  # outside its details, a value is plain words only
        for words, stats in row:
            assert not stats or not re.search(r"\d of \d|interval|Wilson|Student|Jaccard", words), words
    assert "\n| Placebo floor | 25% (10 to 40) |" in md and "Note on the placebo floor" not in md + page
    assert "\u2014" not in md + page
    for asset in ("<script", "<link", " src=", "href=", "@import", "url("):  # one self-contained page
        assert asset not in page, asset


def test_per_intent_table_and_lists_print_each_row():
    md, page = render_markdown(stated()), render_html(stated())
    md_cells, html_cells = md_table(md), html_rows(page)
    assert md_cells["Intent"] == [
        "Kind", "Activated runs", "Read", "Cited", "FootnoteOne", "Indistinguishable from placebo"
    ]
    full, none = "100% (51 to 100), 4 of 4", "0% (0 to 49), 0 of 4"
    assert md_cells["pricing pages"] == ["unbranded", "4", full, full, "75% (30 to 95), 3 of 4", "no"]
    assert md_cells["setup guide"] == ["unbranded", "4", full, none, none, "yes"]
    assert md_cells["refunds"] == ["unbranded", "0", "no data", "no data", "no data", "no data"]
    assert md_cells["brand name"] == ["branded", "4", full, full, full, "not judged"]
    assert md_cells["weather"] == ["placebo", "4", none, none, none, "not judged"]
    full = ("100%", "Wilson 95%; 4 of 4 runs, 1 intent; 95% interval 51 to 100")
    none = ("0%", "Wilson 95%; 0 of 4 runs, 1 intent; 95% interval 0 to 49")
    three = ("75%", "Wilson 95%; 3 of 4 runs, 1 intent; 95% interval 30 to 95")
    nothing = ("no data", "Wilson 95%; no runs counted; left out: 2 error")
    assert html_cells["pricing pages"] == [("unbranded", ""), ("4", ""), full, full, three, ("no", "")]
    assert html_cells["setup guide"] == [("unbranded", ""), ("4", ""), full, none, none, ("yes", "")]
    assert html_cells["refunds"] == [("unbranded", ""), ("0", ""), nothing, nothing, nothing, ("no data", "")]
    assert html_cells["brand name"][-1] == html_cells["weather"][-1] == ("not judged", "")
    assert "\n- `https://example.org/faq`: read in 5 runs, cited in none\n" in md
    assert "<li><code>https://example.org/faq</code>: read in 5 runs, cited in none</li>" in page
    assert "\n- other.net: 30% (3 of 10 runs)\n- wiki.example.com: 20% (2 of 10 runs)\n" in md
    share = "a share of the answers that cited anything, with no interval"
    assert html_cells["other.net"] == [("30%", f"3 of 10 runs; {share}")]
    assert html_cells["wiki.example.com"] == [("20%", f"2 of 10 runs; {share}")]


def test_compute_output_renders_pages_shares_and_placebo_flags(tmp_path):
    """Through compute, on the brief's fixture with four repeats and an owned FAQ page that every answer reads
    and none cites: the page is listed, the cited-instead share keeps its runs, and intents are flagged
    against the placebo band both ways (cited 4 of 4 clears Wilson 0 to 0.49 over the placebo runs)."""

    def faq(label, intent, prompt, engine, rep):
        status, activated, consulted, cited = pattern(label, intent, prompt, engine, rep)
        return status, activated, [*consulted, "https://example.org/faq"], cited

    report = fixture(tmp_path, engines=(E1,), pat=faq, reps=4)
    md, page = render_markdown(report), render_html(report)
    assert "\n- `https://example.org/faq`: read in 32 runs, cited in none\n" in md
    assert "<li><code>https://example.org/faq</code>: read in 32 runs, cited in none</li>" in page
    assert "\n- other.net: 62% (20 of 32 runs)\n" in md
    share = "a share of the answers that cited anything, with no interval"
    assert html_rows(page)["other.net"] == [("62%", f"20 of 32 runs; {share}")]
    for name, flag in (("topic 0", "no"), ("topic 3", "yes"), ("placebo", "not judged")):
        assert md_table(md)[name][-1] == flag and html_rows(page)[name][-1] == (flag, ""), name


def test_lists_never_claim_a_finding_about_pages_nobody_read_or_answers_that_cited_nothing(tmp_path):
    """C4: with searched answers counted but none reading one of your pages, "Read but never cited" says no
    page of yours was read; with none citing any page, "Cited instead" has no data, since its shares have no
    denominator. Neither prints its "None:" finding."""
    elsewhere = fixture(tmp_path / "a", engines=(E1,), pat=lambda *a: ("ok", "yes", [OTHER], [OTHER]))
    silent = fixture(tmp_path / "b", engines=(E1,), pat=lambda *a: ("ok", "yes", [OTHER], []))
    unread = "None of your pages was read by a searched answer."
    uncited = "No data: no searched answer to an unbranded intent cited any page."
    for text in (render_markdown(elsewhere), render_html(elsewhere)):
        read, cited = lists(text)
        assert unread in read and "None: no page of yours was read" not in read
        assert "other.net" in cited  # every answer cited another host instead
    for text in (render_markdown(silent), render_html(silent)):
        read, cited = lists(text)
        assert unread in read and uncited in cited and "None: no other host" not in cited


def test_the_report_opens_with_a_summary_per_engine_and_a_table_of_its_bursts(tmp_path):
    """C5: one sentence per engine (its cited rate with the interval, or no data, and the intents behind it)
    and one row per burst from its manifest's last record, before the reading guide."""
    report = fixture(tmp_path, engines=(E1, E2))
    md, html = render_markdown(report), render_html(report)
    openai = (
        f"api:openai gpt-5-mini (tool version default, config {E1.config_sha[:8]}): cited rate 38% "
        "(-6 to 81) over 8 intents."
    )
    anthropic = (
        f"api:anthropic claude-sonnet-4-5 (tool version default, config {E2.config_sha[:8]}): no data"
    )
    assert f"\n- {openai}\n- {anthropic} (0 intents).\n" in md
    assert f"<li>{openai}</li>" in html and f"<li>{anthropic} (0 intents).</li>" in html
    header = ["Started (UTC)", "Status", "Calls", "Spent", "Code version", "Price table"]
    assert md_table(md)["Burst"] == header
    assert md_table(md)["w1"] == ["2026-10-01 00:00", "done", "18", "0.00 USD", "0.0.1", "2026-10-05"]
    assert [words for words, _ in html_rows(html)["w1"]] == md_table(md)["w1"]
    for text, open_, close in ((md, "## ", "\n"), (html, "<h2>", "</h2>")):
        names = ("Summary", "Bursts", "How to read this report")
        heads = [text.index(f"{open_}{name}{close}") for name in names]
        assert heads == sorted(heads)


def test_unconditional_cited_says_it_counts_unbranded_intents(tmp_path):
    report = fixture(tmp_path, engines=(E1,))
    md, html = render_markdown(report), render_html(report)
    meaning = "answers to unbranded intents that cited one of your pages, searched or not"
    assert f"| Unconditional cited: {meaning} |" in md and meaning in html
    guide = (
        "activation counts every answer whose activation is known, whatever the intent, and unconditional "
        "cited every such answer to an unbranded intent"
    )
    assert guide in md and guide in html
    assert "(Wilson 95%, unbranded intents; 6 of 16 runs, 8 intents)" in md


def test_engines_from_an_older_config_are_marked_and_the_current_ones_say_where_the_runs_are(tmp_path):
    """C1: w1 ran each engine with an output limit of 1000; footnote.toml now says 1200. Each old config is
    marked "not in footnote.toml now" and names the difference; each current config says it has no runs in
    the selected bursts and where they are, never that none of its runs is stored."""
    old, current = shifted_project(tmp_path)
    config, intents_ = load_config(tmp_path), load_intents(tmp_path)
    report = compute(tmp_path, config, intents_, current, pages=[], adapters={}, labels=["w1"])
    openai_old = (
        "Not in footnote.toml now: footnote.toml runs api:openai gpt-5-mini as config "
        f"{current[0].config_sha[:8]}, with max_output_tokens 1200 instead of 1000; the runs below are this "
        "config's, shown as they were."
    )
    openai_now = (
        "There is no data for this engine: it has no runs in the selected bursts. They ran api:openai "
        f"gpt-5-mini as config {old[0].config_sha[:8]}, with max_output_tokens 1000 instead of 1200, shown "
        "in its own section."
    )
    for text in (render_markdown(report), unescape(render_html(report))):
        assert openai_old in text and openai_now in text
        assert "max_tokens 1200 instead of 1000" in text  # the anthropic pair names its own limit
        assert "none of its runs is stored" not in text
