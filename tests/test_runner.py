import asyncio
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from footnoteone.adapters.anthropic import AnthropicAdapter
from footnoteone.adapters.base import RawResponse
from footnoteone.adapters.openai import OpenAIAdapter
from footnoteone.config import ProjectConfig, engine_configs
from footnoteone.design import enumerate_calls
from footnoteone.planning import PlanningAssumptions, worst_case_usd
from footnoteone.pricing import PriceTable
from footnoteone.runner import RunnerError, keys_from_env, run_design
from footnoteone.schema import (
    ERROR_KINDS,
    Intent,
    Manifest,
    Observation,
    Prompt,
    Run,
    SourceRecord,
    SourceRef,
)
from footnoteone.store import JsonlStore, RawStore

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


class FakeAdapter:
    provider = "openai"
    version = "fake@1"

    def __init__(self, script):
        self.script, self.calls = list(script), 0

    def build_request(self, prompt_text, engine):
        return {"input": prompt_text}

    async def call(self, client, prompt_text, engine, api_key):
        self.calls += 1
        action = self.script.pop(0)
        req = httpx.Request("POST", "https://api.example")
        if action == "slow":
            await asyncio.sleep(10)
        if action == "500":
            raise httpx.HTTPStatusError("boom", request=req, response=httpx.Response(500, request=req))
        if action == "403":
            raise httpx.HTTPStatusError("no", request=req, response=httpx.Response(403, request=req))
        if action == "transport":
            raise httpx.ConnectError("down")
        if action == "interrupt":
            raise KeyboardInterrupt
        body = {"explode": True} if action == "badparse" else {"failed": 2 if action == "allfailed" else 0}
        if action in ("refused", "truncated", "failed"):  # the status the parser reads off the response
            body["status"] = "error" if action == "failed" else action
        return RawResponse(
            provider="openai",
            model_requested=engine.model_requested,
            request={"k": api_key[:0]},
            response=body,
        )

    def parse(self, raw):
        if raw.response.get("explode"):
            raise RuntimeError("cannot parse")
        return Observation(
            activated="yes", activation_evidence="fake", answer_text="t",
            consulted=[SourceRef(url="http://www.example.org/a/", rank=1, provider_field="f")],
            cited=[SourceRef(url="https://example.org/a", rank=1, provider_field="c")],
            model_requested=raw.model_requested, search_calls=2,
            failed_searches=raw.response.get("failed", 0), input_tokens=10, output_tokens=5,
            status=raw.response.get("status", "ok"),
        )

    def price(self, obs, table):
        return 0.01


def project(budget=5.0, politeness=0.0, model="gpt-5-mini"):
    return ProjectConfig.model_validate(
        {
            "site": {"url": "https://example.org"},
            "engines": [{"provider": "openai", "model": model}],
            "design": {
                "paraphrases": 2, "reps": 1, "budget_usd_per_burst": budget, "politeness_s": politeness
            },
        }
    )


INTENTS = [
    Intent(
        id="i1",
        label="i1",
        prompts=[Prompt(id="p0", text="q0"), Prompt(id="p1", text="q1", paraphrase_idx=1)],
    )
]


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


async def go(
    tmp_path, adapter, budget=5.0, dry_run=False, keys=None, label="b1", politeness=0.0,
    model="gpt-5-mini", **kw,
):
    cfg, table, a = project(budget, politeness, model), PriceTable.load(), PlanningAssumptions.load()
    engines = engine_configs(cfg, a)
    delays = []

    async def sleep(s):
        delays.append(s)

    result = await run_design(
        tmp_path, cfg, INTENTS, engines, label, budget, dry_run, {"openai": adapter},
        {"openai": "sk-test"} if keys is None else keys, table, a, clock=Clock(), sleep=sleep, **kw,
    )
    return result, engines, delays


def records(tmp_path):
    store = JsonlStore(tmp_path / ".footnote")
    return (
        list(store.iter("manifests", Manifest)),
        list(store.iter("runs", Run)),
        list(store.iter("sources", SourceRecord)),
    )


async def test_happy_path_writes_manifest_sources_and_runs(tmp_path):
    adapter = FakeAdapter(["ok", "ok"])
    result, engines, _ = await go(tmp_path, adapter)
    manifests, runs, sources = records(tmp_path)
    assert (
        result.counts == {"ok": 2} and result.spent_usd == pytest.approx(0.02) and result.planned_calls == 2
    )
    assert (
        [m.status for m in manifests] == ["running", "done"] and manifests[1].spent_usd == pytest.approx(0.02)
    )
    assert (
        manifests[0].label == "b1"
        and manifests[0].engines == engines
        and [i.id for i in manifests[0].intents] == ["i1"]
    )
    assert manifests[1].finished_at == T0 and manifests[0].canon_version == 2
    assert {r.id for r in runs} == {c.key("b1") for c in enumerate_calls(INTENTS, engines, 1)}
    assert all(
        r.status == "ok" and r.raw_sha256 and r.parser_version == "fake@1" and r.cost_usd == 0.01
        for r in runs
    )
    assert sorted(s.role for s in sources) == ["cited", "cited", "consulted", "consulted"]
    assert {s.canonical_url for s in sources} == {"https://example.org/a"}
    assert all(RawStore(tmp_path / ".footnote").exists(r.raw_sha256) for r in runs)


async def test_resume_skips_ok_runs(tmp_path):
    adapter = FakeAdapter(["ok", "ok", "ok", "ok"])
    await go(tmp_path, adapter)
    result, _, _ = await go(tmp_path, adapter)
    assert result.skipped_existing == 2 and result.counts == {} and adapter.calls == 2


async def test_budget_below_worst_case_makes_no_call(tmp_path):
    adapter = FakeAdapter(["ok", "ok"])
    result, engines, _ = await go(tmp_path, adapter, budget=0.001)
    _, runs, _ = records(tmp_path)
    assert adapter.calls == 0 and result.counts == {"budget_skip": 2}
    assert all(
        r.status == "budget_skip" and r.cost_usd == 0 and "budget" in r.activation_evidence for r in runs
    )


async def test_budget_runs_out_midway(tmp_path):
    adapter = FakeAdapter(["ok", "ok"])
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    worst = worst_case_usd(engine_configs(cfg, a)[0], table, a)
    result, _, _ = await go(tmp_path, adapter, budget=worst + 0.005)
    assert result.counts == {"ok": 1, "budget_skip": 1} and adapter.calls == 1


async def test_timeout_is_charged_at_worst_case(tmp_path):
    adapter = FakeAdapter(["slow", "ok"])
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    worst = worst_case_usd(engine_configs(cfg, a)[0], table, a)
    result, _, _ = await go(tmp_path, adapter, timeout_s=0.01)
    _, runs, _ = records(tmp_path)
    timed = next(r for r in runs if r.status == "timeout")
    assert (
        timed.cost_usd == pytest.approx(worst)
        and timed.raw_sha256 is None
        and result.counts == {"timeout": 1, "ok": 1}
    )


async def test_transient_errors_retry_then_fail(tmp_path):
    adapter = FakeAdapter(["500", "ok", "transport", "transport", "transport"])
    result, _, delays = await go(tmp_path, adapter)
    _, runs, _ = records(tmp_path)
    assert result.counts == {"ok": 1, "error": 1} and delays[:1] == [2.0] and delays.count(8.0) == 1
    assert adapter.calls == 5 and all(r.cost_usd == 0 for r in runs if r.status == "error")


async def test_non_transient_http_error_is_immediate(tmp_path):
    adapter = FakeAdapter(["403", "ok"])
    result, _, delays = await go(tmp_path, adapter)
    assert result.counts == {"error": 1, "ok": 1} and delays == [] and adapter.calls == 2


async def test_parse_failure_keeps_raw_and_charges_worst_case(tmp_path):
    adapter = FakeAdapter(["badparse", "ok"])
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    worst = worst_case_usd(engine_configs(cfg, a)[0], table, a)
    await go(tmp_path, adapter)
    _, runs, _ = records(tmp_path)
    bad = next(r for r in runs if r.status == "error")
    assert (
        bad.raw_sha256
        and bad.parser_version == "fake@1"
        and bad.cost_usd == pytest.approx(worst)
        and "parse failed" in bad.activation_evidence
    )


async def test_all_searches_failed_is_an_error(tmp_path):
    adapter = FakeAdapter(["allfailed", "ok"])
    result, _, _ = await go(tmp_path, adapter)
    _, runs, _ = records(tmp_path)
    assert result.counts == {"error": 1, "ok": 1}
    assert any("all searches failed" in r.activation_evidence for r in runs)


async def test_missing_key_and_unpriced_model_refuse_before_writing(tmp_path):
    with pytest.raises(RunnerError, match="OPENAI_API_KEY"):
        await go(tmp_path, FakeAdapter([]), keys={"openai": None})
    with pytest.raises(RunnerError, match="gpt-999"):
        await go(tmp_path, FakeAdapter([]), model="gpt-999")
    assert not (tmp_path / ".footnote" / "manifests.jsonl").exists()


async def test_dry_run_counts_without_keys_or_writes(tmp_path):
    result, _, _ = await go(tmp_path, FakeAdapter([]), dry_run=True, keys={"openai": None})
    assert result.dry_run and result.planned_calls == 2 and result.manifest is None
    assert not (tmp_path / ".footnote" / "manifests.jsonl").exists()


async def test_write_order_is_raw_sources_run(tmp_path, monkeypatch):
    order = []
    real_append, real_put = JsonlStore.append, RawStore.put
    monkeypatch.setattr(
        JsonlStore, "append", lambda self, name, rec: (order.append(name), real_append(self, name, rec))[1]
    )
    monkeypatch.setattr(RawStore, "put", lambda self, obj: (order.append("raw"), real_put(self, obj))[1])
    await go(tmp_path, FakeAdapter(["ok", "ok"]))
    assert order == [
        "manifests", "raw", "sources", "sources", "runs", "raw", "sources", "sources", "runs", "manifests"
    ]


async def test_interrupt_marks_manifest_aborted(tmp_path):
    with pytest.raises(KeyboardInterrupt):
        await go(tmp_path, FakeAdapter(["ok", "interrupt"]))
    manifests, runs, _ = records(tmp_path)
    assert manifests[-1].status == "aborted" and len(runs) == 1


async def test_politeness_sleep_between_calls(tmp_path):
    _, _, delays = await go(tmp_path, FakeAdapter(["ok", "ok"]), politeness=0.5)
    assert delays == [0.5, 0.5]


def test_keys_from_env_reads_configured_names():
    cfg = project()
    assert keys_from_env(cfg, {"OPENAI_API_KEY": "sk-x"}) == {
        "openai": "sk-x", "anthropic": None, "perplexity": None
    }


# The tests below pin behaviour lines of the brief that the tests above leave unexercised.


class PriceFails(FakeAdapter):
    def price(self, obs, table):
        raise ValueError("no row")


async def test_price_failure_charges_the_worst_case_and_notes_it(tmp_path):
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    worst = worst_case_usd(engine_configs(cfg, a)[0], table, a)
    result, _, _ = await go(tmp_path, PriceFails(["ok", "ok"]))
    _, runs, _ = records(tmp_path)
    assert result.counts == {"ok": 2} and result.spent_usd == pytest.approx(2 * worst) and len(runs) == 2
    assert all(r.cost_usd == pytest.approx(worst) and "price failed" in r.activation_evidence for r in runs)


async def test_resume_retries_a_call_whose_last_record_is_not_ok(tmp_path):
    await go(tmp_path, FakeAdapter(["403", "ok"]))
    adapter = FakeAdapter(["ok"])
    result, _, _ = await go(tmp_path, adapter)
    _, runs, _ = records(tmp_path)
    last = {r.id: r.status for r in runs}
    assert result.skipped_existing == 1 and result.counts == {"ok": 1} and adapter.calls == 1
    assert len(runs) == 3 and len(last) == 2 and set(last.values()) == {"ok"}


async def test_end_manifest_record_keeps_the_start_record_id(tmp_path):
    await go(tmp_path, FakeAdapter(["ok", "ok"]))
    with pytest.raises(KeyboardInterrupt):
        await go(tmp_path, FakeAdapter(["interrupt"]), label="b2")
    manifests, _, _ = records(tmp_path)
    assert [m.status for m in manifests] == ["running", "done", "running", "aborted"]
    assert manifests[0].id == manifests[1].id != manifests[2].id == manifests[3].id


# The tests below pin the review fixes. Each source names the raw response it was parsed from; the budget
# counts what earlier runs of the burst spent; a transport failure that may follow a sent request is charged
# and not retried; a success answer whose body is not a JSON object is recorded instead of ending the burst.

STALE = "https://stale.example/x"


def worst_case():
    cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
    return worst_case_usd(engine_configs(cfg, a)[0], table, a)


async def no_sleep(s):
    return None


class BillsWorstCase(FakeAdapter):
    def price(self, obs, table):
        return worst_case()


class Scripted(FakeAdapter):
    """FakeAdapter whose script may also hold exceptions, each raised in turn."""

    async def call(self, client, prompt_text, engine, api_key):
        if isinstance(self.script[0], BaseException):
            self.calls += 1
            raise self.script.pop(0)
        return await super().call(client, prompt_text, engine, api_key)


class CitesStaleWhenSearchesFail(FakeAdapter):
    """FakeAdapter whose all-searches-failed answer cites another page first."""

    def parse(self, raw):
        obs = super().parse(raw)
        if obs.failed_searches:
            obs = obs.model_copy(update={"cited": [SourceRef(url=STALE, rank=1, provider_field="c")]})
        return obs


async def test_sources_of_a_superseded_attempt_are_told_apart_by_raw_sha(tmp_path):
    await go(tmp_path, CitesStaleWhenSearchesFail(["allfailed", "ok"]))
    await go(tmp_path, CitesStaleWhenSearchesFail(["ok"]))  # the resume retries the call that failed
    _, runs, sources = records(tmp_path)
    superseded, final = runs[0], {r.id: r for r in runs}[runs[0].id]
    mine = [s for s in sources if s.run_id == final.id]
    kept = [s for s in mine if s.raw_sha256 == final.raw_sha256]
    assert superseded.status == "error" and final.status == "ok" and superseded.raw_sha256 != final.raw_sha256
    assert len(mine) == 4 and {s.raw_sha256 for s in mine if s.url == STALE} == {superseded.raw_sha256}
    assert sorted((s.role, s.url) for s in kept) == [
        ("cited", "https://example.org/a"), ("consulted", "http://www.example.org/a/")
    ]


async def test_sources_written_before_a_crash_are_told_apart_by_raw_sha(tmp_path, monkeypatch):
    real_append = JsonlStore.append

    def crash_before_the_run(self, name, rec):
        if name == "runs":
            raise KeyboardInterrupt  # the raw blob and the sources are on disk, the run is not
        real_append(self, name, rec)

    monkeypatch.setattr(JsonlStore, "append", crash_before_the_run)
    with pytest.raises(KeyboardInterrupt):
        await go(tmp_path, FakeAdapter(["ok"]))
    monkeypatch.setattr(JsonlStore, "append", real_append)
    await go(tmp_path, FakeAdapter(["ok", "ok"]))
    _, runs, sources = records(tmp_path)
    final = runs[0]
    mine = [s for s in sources if s.run_id == final.id]
    kept = [s for s in mine if s.raw_sha256 == final.raw_sha256]
    assert len(runs) == 2 and all(r.status == "ok" for r in runs)
    assert len(mine) == 4 and len(kept) == 2 and len({s.raw_sha256 for s in mine}) == 2


async def test_a_rerun_of_a_burst_does_not_get_its_budget_again(tmp_path):
    w = worst_case()
    first, _, _ = await go(tmp_path, BillsWorstCase(["ok", "ok"]), budget=1.5 * w)
    adapter = BillsWorstCase(["ok"])
    again, _, _ = await go(tmp_path, adapter, budget=1.5 * w)
    _, runs, _ = records(tmp_path)
    skip = runs[-1].activation_evidence
    assert first.counts == {"ok": 1, "budget_skip": 1} and first.spent_usd == pytest.approx(w)
    assert again.counts == {"budget_skip": 1} and again.skipped_existing == 1 and adapter.calls == 0
    assert again.spent_usd == 0 and sum(r.cost_usd for r in runs) <= 1.5 * w
    assert f"spent {w:.4f}" in skip and "earlier runs of label b1" in skip and "openai/gpt-5-mini" in skip


async def test_superseded_attempts_count_against_the_burst_budget(tmp_path):
    w = worst_case()
    await go(tmp_path, FakeAdapter(["slow", "403"]), timeout_s=0.01)  # p0 times out, charged w
    await go(tmp_path, FakeAdapter(["ok", "403"]))  # p0 ok supersedes the timeout; p1 fails again
    adapter = FakeAdapter(["ok"])
    result, _, _ = await go(tmp_path, adapter, budget=w + 0.02)
    # The burst has spent w + 0.01, the superseded timeout included, so p1's worst case no longer fits.
    assert adapter.calls == 0 and result.counts == {"budget_skip": 1} and result.skipped_existing == 1


async def test_runs_of_the_label_count_after_the_design_changes(tmp_path):
    w = worst_case()
    await go(tmp_path, BillsWorstCase(["ok", "ok"]))  # spends 2w under label b1
    cfg, table, a = project(budget=2.5 * w), PriceTable.load(), PlanningAssumptions.load()
    edited = [Intent(id="i2", label="i2", prompts=[Prompt(id="p9", text="q9")])]
    adapter = BillsWorstCase(["ok"])
    result = await run_design(
        tmp_path, cfg, edited, engine_configs(cfg, a), "b1", 2.5 * w, False, {"openai": adapter},
        {"openai": "sk-test"}, table, a, clock=Clock(), sleep=no_sleep,
    )
    assert adapter.calls == 0 and result.counts == {"budget_skip": 1}


async def test_a_failure_after_the_request_may_have_arrived_is_charged_once(tmp_path):
    w, seen = worst_case(), []

    def no_answer(request):
        seen.append(request)
        raise httpx.ReadTimeout("no response", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_answer)) as client:
        result, _, delays = await go(tmp_path, OpenAIAdapter(), client=client)
    _, runs, _ = records(tmp_path)
    assert len(seen) == 2 and delays == [] and result.spent_usd == pytest.approx(2 * w) and len(runs) == 2
    assert all(
        r.status == "error" and r.cost_usd == pytest.approx(w) and "ReadTimeout" in r.activation_evidence
        for r in runs
    )


MAY_HAVE_ARRIVED = [
    httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.WriteError, httpx.CloseError,
    httpx.RemoteProtocolError,
]
NEVER_SENT = [
    httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ProxyError, httpx.LocalProtocolError,
    httpx.UnsupportedProtocol,
]


@pytest.mark.parametrize("cls", MAY_HAVE_ARRIVED, ids=lambda c: c.__name__)
async def test_transport_failures_that_may_follow_a_sent_request_are_charged(tmp_path, cls):
    adapter = Scripted([cls("x"), "ok"])
    result, _, delays = await go(tmp_path, adapter)
    _, runs, _ = records(tmp_path)
    assert adapter.calls == 2 and delays == [] and result.counts == {"error": 1, "ok": 1}
    assert runs[0].cost_usd == pytest.approx(worst_case()) and cls.__name__ in runs[0].activation_evidence


@pytest.mark.parametrize("cls", NEVER_SENT, ids=lambda c: c.__name__)
async def test_transport_failures_before_sending_retry_at_no_cost(tmp_path, cls):
    adapter = Scripted([cls("x"), cls("x"), cls("x"), "ok"])
    result, _, delays = await go(tmp_path, adapter)
    _, runs, _ = records(tmp_path)
    assert adapter.calls == 4 and delays == [2.0, 8.0] and result.counts == {"error": 1, "ok": 1}
    assert runs[0].cost_usd == 0 and result.spent_usd == pytest.approx(0.01)


UNREADABLE_BODIES = {
    "JSONDecodeError": lambda: httpx.Response(200, text="<html>gateway</html>"),
    "ValidationError": lambda: httpx.Response(200, json=[1, 2]),  # JSON, but not an object
    "UnicodeDecodeError": lambda: httpx.Response(200, content=b'{"a": "\xe9"}'),  # Latin-1, not UTF-8
    # A stream, not content: content is decoded when the Response is built, a stream when the client reads it.
    "DecodingError": lambda: httpx.Response(
        200, headers={"content-encoding": "gzip"}, stream=httpx.ByteStream(b"not gzip")
    ),
}


@pytest.mark.parametrize("name", UNREADABLE_BODIES)
async def test_a_200_that_is_not_a_json_object_is_charged_and_the_burst_goes_on(tmp_path, name):
    answers = [UNREADABLE_BODIES[name](), httpx.Response(200, json={"status": "completed", "output": []})]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: answers.pop(0))) as client:
        result, _, delays = await go(tmp_path, OpenAIAdapter(), client=client)
    manifests, runs, _ = records(tmp_path)
    assert result.counts == {"error": 1, "ok": 1} and manifests[-1].status == "done" and delays == []
    assert runs[0].cost_usd == pytest.approx(worst_case()) and runs[0].raw_sha256 is None
    assert name in runs[0].activation_evidence


# A key an HTTP header cannot carry: an ellipsis or curly quote pasted with it, a line break, a delete
# character, or spaces around it (h11 refuses those). The run stops before .footnote/ exists, naming the
# variable and never the value.
UNSENDABLE_KEYS = {
    "ellipsis": "sk-test" + chr(0x2026),
    "curly quote": chr(0x201C) + "sk-test",
    "line break": "sk-test\n",
    "delete": "sk-te" + chr(0x7F) + "st",
    "leading space": " sk-test",
    "trailing space": "sk-test ",
}


@pytest.mark.parametrize("key", UNSENDABLE_KEYS.values(), ids=UNSENDABLE_KEYS.keys())
async def test_a_key_a_header_cannot_carry_stops_the_run_before_anything_is_written(tmp_path, key):
    seen = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(seen.append)) as client:
        with pytest.raises(RunnerError) as info:
            await go(tmp_path, OpenAIAdapter(), keys={"openai": key}, client=client)
    assert "OPENAI_API_KEY" in str(info.value) and "sk-te" not in str(info.value)
    assert seen == [] and not (tmp_path / ".footnote").exists()


@pytest.mark.parametrize("key", ["", "   ", "\t"])
async def test_a_key_that_is_empty_after_strip_is_missing(tmp_path, key):
    with pytest.raises(RunnerError, match="no API key found in environment variable"):
        await go(tmp_path, FakeAdapter([]), keys={"openai": key})
    assert not (tmp_path / ".footnote").exists()


# Money: an answer that reports no token usage is charged at least its worst case (Ruling B26), and a 200
# whose body the JSON reader cannot even hold (an integer past 4300 digits, nesting past the recursion limit)
# is recorded as an error at the worst case instead of ending the burst.

FIXTURES = Path(__file__).parent / "fixtures"


def anthropic_project(budget=5.0):
    return ProjectConfig.model_validate(
        {
            "site": {"url": "https://example.org"},
            "engines": [{"provider": "anthropic", "model": "claude-sonnet-4-5"}],
            "design": {"paraphrases": 2, "reps": 1, "budget_usd_per_burst": budget},
        }
    )


async def run_anthropic(tmp_path, answer):
    cfg, table, a = anthropic_project(), PriceTable.load(), PlanningAssumptions.load()
    engines = engine_configs(cfg, a)
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        result = await run_design(
            tmp_path, cfg, INTENTS, engines, "b1", 5.0, False, {"anthropic": AnthropicAdapter()},
            {"anthropic": "sk-test"}, table, a, client=client, clock=Clock(), sleep=no_sleep,
        )
    return result, worst_case_usd(engines[0], table, a)


def anthropic_body():
    return json.loads((FIXTURES / "anthropic_web_search_searched.json").read_text())["response"]


async def test_an_answer_with_its_token_usage_missing_is_charged_the_worst_case(tmp_path):
    body = anthropic_body()
    body["usage"] = {f"renamed_{key}": value for key, value in body["usage"].items()}
    result, worst = await run_anthropic(tmp_path, lambda request: httpx.Response(200, json=body))
    _, runs, _ = records(tmp_path)
    assert result.counts == {"ok": 2} and result.spent_usd == pytest.approx(2 * worst)
    assert all(r.cost_usd == pytest.approx(worst) and r.input_tokens is None for r in runs)
    assert all("token usage missing; charged the worst case" in r.activation_evidence for r in runs)


async def test_an_answer_with_its_token_usage_is_charged_its_price(tmp_path):
    body = anthropic_body()
    result, worst = await run_anthropic(tmp_path, lambda request: httpx.Response(200, json=body))
    _, runs, _ = records(tmp_path)
    price = AnthropicAdapter().price(AnthropicAdapter().parse(RawResponse(
        provider="anthropic", model_requested="claude-sonnet-4-5", request={}, response=body
    )), PriceTable.load())
    assert 0 < price < worst and all(r.cost_usd == pytest.approx(price) for r in runs)
    assert not any("token usage missing" in r.activation_evidence for r in runs)


PATHOLOGICAL_BODIES = {
    "ValueError": b'{"usage": ' + b"1" * 5000 + b"}",  # past int()'s 4300-digit limit
    "RecursionError": b"[" * 100_000 + b"]" * 100_000,
}


@pytest.mark.parametrize("name", PATHOLOGICAL_BODIES)
async def test_a_200_the_json_reader_cannot_hold_is_charged_and_the_burst_goes_on(tmp_path, name):
    answers = [
        httpx.Response(200, content=PATHOLOGICAL_BODIES[name]), httpx.Response(200, json=anthropic_body())
    ]
    result, worst = await run_anthropic(tmp_path, lambda request: answers.pop(0))
    manifests, runs, _ = records(tmp_path)
    assert result.counts == {"error": 1, "ok": 1} and manifests[-1].status == "done"
    assert runs[0].cost_usd == pytest.approx(worst) and runs[0].raw_sha256 is None
    assert f"unreadable response body: {name}" in runs[0].activation_evidence


# Ruling B30: every run that did not end ok says why in error_kind, and ok runs carry None. Ruling B23 as
# amended: on resume a run whose last record is refused, truncated, or an error whose kind is "parse" is
# final (a complete billed answer, or a stored response a later parser can replay); every other run that is
# not ok is asked again.

OUTCOME_KINDS = {
    "403": ("error", "http"),
    "transport": ("error", "transport"),  # ConnectError three times: never sent, so retried, then recorded
    "slow": ("timeout", "timeout"),
    "badparse": ("error", "parse"),
    "allfailed": ("error", "all_searches_failed"),
    "failed": ("error", "provider_failed"),  # the response's own status says it failed
    "refused": ("refused", "refused"),
    "truncated": ("truncated", "truncated"),
}


@pytest.mark.parametrize("action", OUTCOME_KINDS)
async def test_every_outcome_that_is_not_ok_records_its_error_kind(tmp_path, action):
    script = [action] * (3 if action == "transport" else 1) + ["ok"]
    await go(tmp_path, FakeAdapter(script), timeout_s=0.01)
    _, runs, _ = records(tmp_path)
    assert [(r.status, r.error_kind) for r in runs] == [OUTCOME_KINDS[action], ("ok", None)]
    assert runs[0].error_kind in ERROR_KINDS


async def test_unreadable_and_budget_outcomes_record_their_error_kind(tmp_path):
    await go(tmp_path / "a", Scripted([ValueError("not json"), "ok"]))
    await go(tmp_path / "b", FakeAdapter([]), budget=0.001)
    assert [(r.status, r.error_kind) for r in records(tmp_path / "a")[1]] == [
        ("error", "unreadable"), ("ok", None)
    ]
    assert {(r.status, r.error_kind) for r in records(tmp_path / "b")[1]} == {("budget_skip", "budget")}
    produced = {kind for _, kind in OUTCOME_KINDS.values()} | {"unreadable", "budget"}
    assert produced == set(ERROR_KINDS)  # every documented kind is one the runner records, and no other


RESUME = [
    ("refused", "refused", True),
    ("truncated", "truncated", True),
    ("error", "parse", True),
    ("error", "http", False),
    ("error", "transport", False),
    ("error", "unreadable", False),
    ("error", "provider_failed", False),
    ("error", "all_searches_failed", False),
    ("error", None, False),  # a record written before error_kind existed
    ("timeout", "timeout", False),
    ("budget_skip", "budget", False),
]


@pytest.mark.parametrize(("status", "kind", "final"), RESUME, ids=lambda x: str(x))
async def test_resume_asks_again_unless_the_last_record_is_final(tmp_path, status, kind, final):
    await go(tmp_path, FakeAdapter(["ok", "ok"]))  # both calls of label b1 ok
    store = JsonlStore(tmp_path / ".footnote")
    first = records(tmp_path)[1][0]
    store.append("runs", first.model_copy(update={"status": status, "error_kind": kind}))  # its last record
    adapter = FakeAdapter(["ok"])
    result, _, _ = await go(tmp_path, adapter)
    assert adapter.calls == (0 if final else 1)
    assert result.skipped_existing == (2 if final else 1)


# D2: the result carries what earlier runs of the label spent and the manifest's final record; a provider
# with no adapter stops the run before .footnote/ exists; keys_from_env reads os.environ when it is called.


async def test_the_result_carries_the_prior_spend_and_the_final_manifest_record(tmp_path):
    first, _, _ = await go(tmp_path, FakeAdapter(["403", "ok"]))
    again, _, _ = await go(tmp_path, FakeAdapter(["ok"]))
    manifests, _, _ = records(tmp_path)
    assert first.prior_spent_usd == 0 and again.prior_spent_usd == pytest.approx(first.spent_usd)
    assert again.manifest == manifests[-1] and again.manifest.status == "done"
    assert again.manifest.finished_at == T0 and again.manifest.spent_usd == pytest.approx(again.spent_usd)


async def test_a_provider_without_an_adapter_stops_the_run_before_footnote_exists(tmp_path):
    for dry_run in (False, True):
        with pytest.raises(RunnerError, match="openai"):
            cfg, table, a = project(), PriceTable.load(), PlanningAssumptions.load()
            await run_design(
                tmp_path, cfg, INTENTS, engine_configs(cfg, a), "b1", 5.0, dry_run, {}, {"openai": "sk-test"},
                table, a,
            )
    assert not (tmp_path / ".footnote").exists()


def test_keys_from_env_reads_os_environ_when_called(monkeypatch):
    assert inspect.signature(keys_from_env).parameters["environ"].default is None
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")  # blank after strip: missing, as doctor counts it
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    assert keys_from_env(project()) == {"openai": "sk-from-env", "anthropic": None, "perplexity": None}


# D1: the runner tells its caller when the burst starts and after every call, and a dry run reads what the
# label already holds without writing.


async def test_the_runner_reports_the_start_and_every_call(tmp_path):
    await go(tmp_path, FakeAdapter(["403", "ok"]))  # p1 ok, p0 an HTTP error that the rerun asks again
    starts, calls = [], []
    w = worst_case()
    result, engines, _ = await go(
        tmp_path, FakeAdapter(["ok"]), budget=1.0, on_start=starts.append, on_call=calls.append
    )
    [start] = starts
    assert (start.label, start.planned_calls, start.already_done, start.budget_usd) == ("b1", 2, 1, 1.0)
    assert start.prior_spent_usd == pytest.approx(0.01)
    [done] = calls
    assert (done.index, done.total, done.engine) == (1, 2, engines[0])
    assert (done.status, done.error_kind) == ("ok", None) and done.cost_usd == pytest.approx(0.01)
    [tally] = result.engines
    assert tally.engine == engines[0] and tally.counts == {"ok": 1} and tally.already_done == 1
    assert tally.worst_case_usd == pytest.approx(w)


async def test_a_dry_run_reads_the_label_and_writes_nothing(tmp_path):
    await go(tmp_path, FakeAdapter(["403", "ok"]))
    before = {path: path.read_bytes() for path in (tmp_path / ".footnote").rglob("*") if path.is_file()}
    result, engines, _ = await go(tmp_path, FakeAdapter([]), dry_run=True, keys={"openai": None})
    after = {path: path.read_bytes() for path in (tmp_path / ".footnote").rglob("*") if path.is_file()}
    calls = enumerate_calls(INTENTS, engines, 1)
    assert result.dry_run and result.manifest is None and after == before
    assert result.planned_calls == 2 and result.skipped_existing == 1 and result.to_call == [calls[0]]
    assert result.prior_spent_usd == pytest.approx(0.01) and result.engines[0].already_done == 1


async def test_the_start_names_engines_the_label_ran_under_another_config(tmp_path):
    """C2: an earlier run of the label used gpt-5-mini with another output limit; the burst starts afresh for
    today's config, and the start says which earlier config of the same model the label holds."""
    cfg, a = project(), PlanningAssumptions.load()
    [now] = engine_configs(cfg, a)
    earlier = now.model_copy(update={"params": {**now.params, "max_output_tokens": 1000}})
    other_label = now.model_copy(update={"params": {**now.params, "max_output_tokens": 800}})
    store = JsonlStore(tmp_path / ".footnote")
    for label, engine in (("b1", earlier), ("b1", now), ("b2", other_label)):
        store.append(
            "manifests",
            Manifest(
                code_version="0", config_sha="c", price_table_version="v", budget_usd=1.0, label=label,
                engines=[engine],
            ),
        )
    starts = []
    result, _, _ = await go(tmp_path, FakeAdapter(["ok", "ok"]), on_start=starts.append)
    assert starts[0].changed == [(earlier, now)]  # b2's config is another label's business
    dry, _, _ = await go(tmp_path, FakeAdapter([]), dry_run=True)
    assert dry.changed == [(earlier, now)]
