from datetime import UTC, date, datetime

import pytest
import typer
from typer.testing import CliRunner

from footnoteone.adapters.anthropic import AnthropicAdapter
from footnoteone.adapters.openai import OpenAIAdapter
from footnoteone.adapters.perplexity import PerplexityAdapter
from footnoteone.commands import doctor as doctor_module
from footnoteone.commands.doctor import checks, register
from footnoteone.config import write_templates
from footnoteone.library import write_pages
from footnoteone.pricing import PriceTable
from footnoteone.schema import Manifest, Page, Run, SourceRecord
from footnoteone.store import JsonlStore, RawStore

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
KEY_NAMES = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PERPLEXITY_API_KEY")
OPENAI_ONLY = """\
[site]
url = "https://example.org"

[[engines]]
provider = "openai"
model = "gpt-5-mini"

[design]
budget_usd_per_burst = 20.0
"""


def text_of(lines):
    return "\n".join(f"{level} {msg}" for level, msg in lines)


def doctor_app():
    app = typer.Typer()
    register(app)
    return app


def edit(root, name, old, new):
    path = root / name
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def eight_unbranded_intents():
    entries = "".join(
        f'  - id: topic-{n}\n    label: "Topic {n}"\n    prompts:\n      - "Question {n}"\n' for n in range(8)
    )
    return f"version: 1\nintents:\n{entries}"


def make_manifest(status):
    return Manifest(
        id="m1", code_version="0.0.1", config_sha="c", price_table_version="v", budget_usd=1.0, status=status
    )


def make_run(run_id, parser_version, status="ok"):
    return Run(
        id=run_id,
        manifest_id="m1",
        intent_id="topic",
        prompt_id="p1",
        engine_config_id="e1",
        status=status,
        model_requested="gpt-5-mini",
        parser_version=parser_version,
        started_at=T0,
        finished_at=T0,
    )


def make_source(run_id):
    return SourceRecord(
        run_id=run_id,
        role="cited",
        url="https://a.example/x",
        canonical_url="https://a.example/x",
        rank=1,
        provider_field="annotations",
    )


@pytest.fixture
def all_keys(monkeypatch):
    for name in KEY_NAMES:
        monkeypatch.setenv(name, "sk-secret-value")


def test_doctor_reports_each_check(tmp_path, monkeypatch):
    write_templates(tmp_path, "https://example.org")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    lines = checks(tmp_path, today=date(2026, 10, 10))
    text = "\n".join(f"{level} {msg}" for level, msg in lines)
    assert "ok footnote.toml" in text and "ok intents.yaml" in text and "2 intents" in text
    assert "ok key OPENAI_API_KEY set" in text and "warn key ANTHROPIC_API_KEY missing" in text
    assert "sk-secret-value" not in text
    assert "pricing.yaml 2026-10-05" in text and "planning.yaml" in text
    assert "warn" in text and "pages" in text  # no library yet
    assert all(level in ("ok", "warn", "fail") for level, _ in lines)


def test_doctor_fails_on_unpriced_engine_and_old_prices(tmp_path):
    write_templates(tmp_path, "https://example.org")
    toml = (tmp_path / "footnote.toml").read_text().replace('model = "gpt-5-mini"', 'model = "gpt-999"')
    (tmp_path / "footnote.toml").write_text(toml)
    lines = checks(tmp_path, today=date(2027, 6, 1))
    text = "\n".join(f"{level} {msg}" for level, msg in lines)
    assert "fail" in text and "gpt-999" in text and "not priced" in text
    assert "warn pricing.yaml" in text and "days old" in text
    app = typer.Typer()
    register(app)
    result = CliRunner().invoke(app, ["--root", str(tmp_path)])
    assert result.exit_code == 1 and "fail" in result.output


def test_doctor_without_config_is_a_fail_line(tmp_path):
    lines = checks(tmp_path)
    assert lines[0][0] == "fail" and "footnote.toml" in lines[0][1]


def test_doctor_checks_run_in_the_documented_order(tmp_path, all_keys):
    write_templates(tmp_path, "https://example.org")
    messages = [msg for _, msg in checks(tmp_path, today=date(2026, 10, 10))]
    assert [msg.split(" ")[0].rstrip(":") for msg in messages] == [
        *("footnote.toml", "intents.yaml", "pages", "key", "key", "key", "pricing.yaml", "planning.yaml"),
        *("engine", "engine", "engine", "budget", "unbranded", "store", "parsers"),
    ]
    assert [msg.split(" ")[1] for msg in messages[3:6]] == list(KEY_NAMES)


def test_doctor_prints_one_leveled_line_per_check_and_exits_0_without_a_failure(tmp_path, all_keys):
    write_templates(tmp_path, "https://example.org")
    result = CliRunner().invoke(doctor_app(), ["--root", str(tmp_path)])
    lines = result.output.splitlines()
    assert result.exit_code == 0, result.output
    assert lines[0].startswith("ok   footnote.toml") and lines[1].startswith("ok   intents.yaml")
    assert all(line.split(" ")[0] in ("ok", "warn", "fail") for line in lines)
    assert any(line.startswith("warn ") for line in lines)
    assert "sk-secret-value" not in result.output and "\u2014" not in result.output


def test_doctor_reads_the_current_folder_by_default(tmp_path, monkeypatch, all_keys):
    write_templates(tmp_path, "https://example.org")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(doctor_app(), [])
    assert result.exit_code == 0 and result.output.startswith("ok   footnote.toml")


def test_doctor_counts_intents_by_kind(tmp_path):
    write_templates(tmp_path, "https://example.org")
    lines = checks(tmp_path)
    assert ("ok", "intents.yaml loads: 2 intents (1 unbranded, 0 branded, 1 placebo)") in lines


def test_doctor_names_only_the_providers_in_use(tmp_path, all_keys):
    (tmp_path / "footnote.toml").write_text(OPENAI_ONLY)
    (tmp_path / "intents.yaml").write_text(eight_unbranded_intents())
    text = text_of(checks(tmp_path))
    assert "key OPENAI_API_KEY set" in text and "ANTHROPIC" not in text and "PERPLEXITY" not in text


def test_doctor_treats_an_empty_key_variable_as_missing(tmp_path, monkeypatch):
    write_templates(tmp_path, "https://example.org")
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    assert "warn key OPENAI_API_KEY missing" in text_of(checks(tmp_path))


def test_doctor_uses_the_variable_names_from_the_config(tmp_path, monkeypatch):
    write_templates(tmp_path, "https://example.org")
    edit(tmp_path, "footnote.toml", 'openai = "OPENAI_API_KEY"', 'openai = "WORK_OPENAI_KEY"')
    monkeypatch.setenv("WORK_OPENAI_KEY", "sk-secret-value")
    text = text_of(checks(tmp_path))
    assert "ok key WORK_OPENAI_KEY set" in text and "sk-secret-value" not in text


def test_doctor_never_prints_a_key_pasted_where_a_variable_name_belongs(tmp_path, monkeypatch):
    """F1: footnote.toml no longer loads with a key pasted into [keys], so doctor fails its first line,
    naming the field and never the value, and skips the checks that need the config."""
    write_templates(tmp_path, "https://example.org")
    edit(tmp_path, "footnote.toml", 'openai = "OPENAI_API_KEY"', 'openai = "sk-proj-abc123-pasted"')
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    lines = checks(tmp_path)
    assert lines[0][0] == "fail" and "keys.openai" in lines[0][1] and "environment variable" in lines[0][1]
    assert not any(msg.startswith("key") for _, msg in lines)
    assert "sk-proj-abc123-pasted" not in text_of(lines)
    result = CliRunner().invoke(doctor_app(), ["--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "sk-proj-abc123-pasted" not in result.output and "sk-secret-value" not in result.output


def test_doctor_pricing_age_warns_only_past_90_days(tmp_path):
    write_templates(tmp_path, "https://example.org")
    version = PriceTable.load().version
    priced_on = date.fromisoformat(version)

    def pricing_line(days):
        today = date.fromordinal(priced_on.toordinal() + days)
        return next(line for line in checks(tmp_path, today=today) if line[1].startswith("pricing.yaml"))

    assert pricing_line(0) == ("ok", f"pricing.yaml {version}, 0 days old")
    assert pricing_line(1)[0] == "ok" and "1 day old" in pricing_line(1)[1]
    assert pricing_line(90)[0] == "ok"
    assert pricing_line(91)[0] == "warn" and "91 days old" in pricing_line(91)[1]


def test_doctor_warns_when_the_price_version_is_not_a_date(tmp_path, monkeypatch):
    write_templates(tmp_path, "https://example.org")
    real = PriceTable.load()
    monkeypatch.setattr(PriceTable, "load", classmethod(lambda cls: cls("autumn", real.providers)))
    assert "warn pricing.yaml autumn" in text_of(checks(tmp_path))


def test_doctor_prices_each_engine(tmp_path):
    write_templates(tmp_path, "https://example.org")
    priced = [msg for level, msg in checks(tmp_path) if level == "ok" and msg.startswith("engine ")]
    assert [msg.split(" ")[1] for msg in priced] == [
        "openai/gpt-5-mini",
        "anthropic/claude-sonnet-4-5",
        "perplexity/fast",
    ]


def test_doctor_warns_when_the_budget_is_below_the_worst_case_of_one_burst(tmp_path):
    write_templates(tmp_path, "https://example.org")
    assert "ok budget 20.00 USD per burst covers the worst case" in text_of(checks(tmp_path))
    edit(tmp_path, "footnote.toml", "budget_usd_per_burst = 20.0", "budget_usd_per_burst = 0.5")
    text = text_of(checks(tmp_path))
    assert "warn budget 0.50 USD per burst is below the worst case" in text


def test_doctor_warns_when_unbranded_intents_are_below_min_shared_intents(tmp_path):
    write_templates(tmp_path, "https://example.org")
    assert "warn unbranded intents: 1, below min_shared_intents = 8" in text_of(checks(tmp_path))
    (tmp_path / "intents.yaml").write_text(eight_unbranded_intents())
    assert "ok unbranded intents: 8, enough for a verdict" in text_of(checks(tmp_path))


def test_doctor_counts_the_pages_in_the_library(tmp_path):
    write_templates(tmp_path, "https://example.org")
    urls = [f"https://example.org/p{i}" for i in range(3)]
    pages = [Page(id=Page.id_for(url), url=url, canonical_url=url, source="sitemap") for url in urls]
    write_pages(tmp_path, pages)
    text = text_of(checks(tmp_path))
    assert "ok pages: 3 pages in the library" in text and "warn pages" not in text


def pages_line(root):
    return next(line for line in checks(root) if line[1].startswith("pages"))


def test_doctor_sends_an_empty_library_to_a_pages_only_init(tmp_path):
    write_templates(tmp_path, "https://example.org")
    write_pages(tmp_path, [])  # init ran, and the site had no sitemap or feed to read
    level, message = pages_line(tmp_path)
    assert level == "warn" and "library is empty" in message and "youtube_channels" in message
    # A plain init refuses to write over footnote.toml and --force would overwrite the user's edits.
    assert message.endswith("run footnote init https://example.org --pages-only") and "--force" not in message


def test_doctor_points_to_init_when_there_is_no_library_or_store(tmp_path):
    write_templates(tmp_path, "https://example.org")
    text = text_of(checks(tmp_path))
    assert "warn pages: no library yet; run footnote init" in text
    assert "warn store: no .footnote/ folder yet" in text
    assert not (tmp_path / ".footnote").exists()  # a check never creates the store


def test_doctor_points_to_a_pages_only_init_once_footnote_toml_exists(tmp_path):
    assert pages_line(tmp_path) == ("warn", "pages: no library yet; run footnote init")  # nothing to keep
    write_templates(tmp_path, "https://example.org")
    assert pages_line(tmp_path) == (
        "warn",
        "pages: no library yet; run footnote init https://example.org --pages-only, "
        "which keeps footnote.toml and intents.yaml",
    )
    (tmp_path / "footnote.toml").write_text('[site]\nurl = "example.org"\n')  # exists, does not load
    assert "run footnote init <site url> --pages-only" in pages_line(tmp_path)[1]


def test_doctor_counts_the_store_and_the_runs_a_newer_parser_will_replay(tmp_path):
    """M6: a run counts as replayable only when an older parser read it and its raw response is stored."""
    write_templates(tmp_path, "https://example.org")
    store, raw = JsonlStore(tmp_path / ".footnote"), RawStore(tmp_path / ".footnote")
    for status in ("running", "done"):  # the runner records a manifest at the start and again at the end
        store.append("manifests", make_manifest(status))
    blobs = [raw.put(blob) for blob in ({"a": 1}, {"b": 2}, {"c": 3})]
    records = [
        ("r1", "openai@0.0.1", blobs[0]),
        ("r2", "anthropic@0.0.9", blobs[1]),
        ("r5", "openai@0.0.1", blobs[2]),
        ("r1", OpenAIAdapter().version, blobs[0]),  # r1 again: the last record wins, and it is current
        ("r3", AnthropicAdapter().version, None),
        ("r6", PerplexityAdapter().version, None),
        ("r7", "openai@0.0.1", "ab" * 32),  # an older parser read it, but its blob is not stored
        ("r8", "perplexity@0.0.1", None),  # an older parser, and no blob named at all
    ]
    for run_id, version, sha in records:
        store.append("runs", make_run(run_id, version).model_copy(update={"raw_sha256": sha}))
    store.append("runs", make_run("r4", None, status="error"))  # no response body: nothing to replay
    for run_id in ("r1", "r2"):
        store.append("sources", make_source(run_id))
    text = text_of(checks(tmp_path))
    assert "ok store .footnote/: 1 manifest, 8 runs, 2 sources, 3 raw responses" in text
    assert "ok parsers: 2 runs will be replayed with the current parser" in text


def test_doctor_reads_keys_and_parser_versions_through_the_runner(tmp_path, monkeypatch):
    """F2: doctor asks runner.keys_from_env whether a key is set and runner.default_adapters which parser
    versions are current, so it agrees with footnote run."""
    write_templates(tmp_path, "https://example.org")
    for name in KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        doctor_module, "keys_from_env", lambda config: {"openai": "x", "anthropic": None, "perplexity": None}
    )

    class Newer:
        def __init__(self, version):
            self.version = version

    newer = {p: Newer(f"{p}@9.9.9") for p in ("openai", "anthropic", "perplexity")}
    monkeypatch.setattr(doctor_module, "default_adapters", lambda: newer)
    sha = RawStore(tmp_path / ".footnote").put({"a": 1})
    current = make_run("r1", OpenAIAdapter().version).model_copy(update={"raw_sha256": sha})
    JsonlStore(tmp_path / ".footnote").append("runs", current)
    text = text_of(checks(tmp_path))
    assert "ok key OPENAI_API_KEY set" in text and "warn key ANTHROPIC_API_KEY missing" in text
    assert "ok parsers: 1 run will be replayed with the current parser" in text


def test_doctor_says_when_no_stored_run_needs_replaying(tmp_path):
    write_templates(tmp_path, "https://example.org")
    JsonlStore(tmp_path / ".footnote").append("runs", make_run("r1", OpenAIAdapter().version))
    text = text_of(checks(tmp_path))
    assert "ok store .footnote/: 0 manifests, 1 run, 0 sources, 0 raw responses" in text
    assert "ok parsers: no stored run needs replaying" in text


def test_doctor_reports_a_damaged_record_file_as_a_failure(tmp_path):
    write_templates(tmp_path, "https://example.org")
    directory = tmp_path / ".footnote"
    directory.mkdir()
    (directory / "runs.jsonl").write_text('{"id": "r1"}\n{"id": "r2"}\n', encoding="utf-8")
    (directory / "pages.jsonl").write_text('{"id": 1}\n{"id": 2}\n', encoding="utf-8")
    lines = checks(tmp_path)
    levels = {msg.split(":")[0]: level for level, msg in lines}
    assert levels["store"] == "fail" and levels["pages"] == "fail"
    assert all("\n" not in msg for _, msg in lines)
    assert "runs.jsonl" in text_of(lines) and "pages.jsonl" in text_of(lines)


def test_doctor_keeps_going_when_a_file_does_not_load(tmp_path):
    (tmp_path / "footnote.toml").write_text('[site]\nurl = "example.org"\n')  # no scheme, engines or design
    (tmp_path / "intents.yaml").write_text("intents: [\n")
    lines = checks(tmp_path, today=date(2026, 10, 10))
    levels = {msg.split(":")[0]: level for level, msg in lines}
    assert levels["footnote.toml"] == "fail" and levels["intents.yaml"] == "fail"
    assert all("\n" not in msg for _, msg in lines)
    heads = [msg.split(" ")[0].rstrip(":") for _, msg in lines]
    assert "pricing.yaml" in heads and "store" in heads  # the checks that need no config still run
    assert not {"key", "engine", "budget", "unbranded"} & set(heads)  # the ones that need it are skipped
    assert "site.url" in lines[0][1] and "full http or https URL" in lines[0][1]


def test_doctor_reports_a_config_file_that_cannot_be_read(tmp_path):
    write_templates(tmp_path, "https://example.org")
    (tmp_path / "footnote.toml").write_bytes(b"\xff\xfe not text")
    lines = checks(tmp_path)
    assert lines[0][0] == "fail" and lines[0][1].startswith("footnote.toml:")


# E5 (Ruling B27): a site on a shared platform owns no domain, and doctor says what counts instead.


def test_doctor_warns_that_only_the_prefixes_count_on_a_shared_platform(tmp_path):
    write_templates(tmp_path, "https://medium.com/@ali")
    lines = checks(tmp_path)
    expected = (
        "warn",
        "site: your site is on a shared platform; only the listed prefixes count as yours: "
        "https://medium.com/@ali",
    )
    assert lines[1] == expected  # right after the footnote.toml line
    own = tmp_path / "own"
    own.mkdir()
    write_templates(own, "https://example.org")
    assert not any(msg.startswith("site:") for _, msg in checks(own))


def test_doctor_warns_when_own_domains_hands_a_shared_platform_to_the_user(tmp_path):
    write_templates(tmp_path, "https://example.org")
    both = 'own_domains = ["example.org", "medium.com"]'
    edit(tmp_path, "footnote.toml", 'own_domains = ["example.org"]', both)
    [line] = [line for line in checks(tmp_path) if line[1].startswith("site:")]
    assert line == (
        "warn",
        "site: own_domains lists medium.com, a platform shared by many creators, so every page on it counts "
        "as yours; list your profile there in offsite_prefixes instead",
    )
