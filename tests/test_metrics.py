import json
from pathlib import Path

import pytest
from helpers import OTHER, OWN, build_store, shifted_project

from footnoteone.adapters.base import RawResponse
from footnoteone.adapters.openai import OpenAIAdapter
from footnoteone.canon import OwnedSet, canonicalize
from footnoteone.config import ProjectConfig, load_config, load_intents
from footnoteone.metrics import (
    compute,
    effective_view_status,
    load_manifests,
    load_runs,
    run_views,
    select_manifests,
    t_value,
    wilson_value,
)
from footnoteone.schema import EngineConfig, Intent, Prompt, Run, SourceRecord
from footnoteone.store import JsonlStore, RawStore

E1 = EngineConfig(provider="openai", model_requested="gpt-5-mini")
E2 = EngineConfig(provider="anthropic", model_requested="claude-sonnet-4-5")
CFG = ProjectConfig.model_validate(
    {
        "site": {"url": "https://example.org"},
        "engines": [{"provider": "openai", "model": "gpt-5-mini"}],
        "design": {"budget_usd_per_burst": 1.0},
    }
)
OWNED = OwnedSet.build(["example.org"], [])


def intents():
    def mk(prefix, n, kind):
        return [
            Intent(
                id=f"{prefix}{k}",
                label=f"{prefix}{k}",
                kind=kind,
                prompts=[Prompt(id=f"{prefix}{k}p{j}", text="q", paraphrase_idx=j) for j in range(2)],
            )
            for k in range(n)
        ]

    return mk("u", 4, "unbranded") + mk("b", 1, "branded") + mk("p", 1, "placebo")


def pattern_cited_half(label, intent, prompt, engine, rep):
    """Unbranded intents u0 and u1 cite the owned page in every run; u2 and u3 never; branded always; placebo
    never. Every run consulted the owned page; rep 1 of u3 is an error; engine E2 answers from memory
    (activated no)."""
    if engine == E2:
        return ("ok", "no", [], [])
    if intent.id == "u3" and rep == 1:
        return ("error", "unknown", [], [])
    cites = intent.id in ("u0", "u1", "b0")
    return ("ok", "yes", ["http://www.example.org/guide/", OTHER], [OWN, OTHER] if cites else [OTHER])


def test_wilson_and_t_values_carry_denominators():
    v = wilson_value(3, 10, excluded={"unknown": 2})
    assert (v.numerator, v.denominator, v.excluded, v.method) == (3, 10, {"unknown": 2}, "Wilson 95%")
    assert 0.1 < v.lo < 0.3 < v.hi < 0.7
    assert wilson_value(0, 0).value is None and wilson_value(0, 0).note == "no data"
    t = t_value({"a": [1, 1], "b": [0, 0], "c": [1, 0]})
    assert t.value == pytest.approx(0.5) and t.n_intents == 3 and t.numerator == 3 and t.denominator == 6
    assert "bootstrap" in t.note
    assert t_value({"a": [1.0]}).lo is None and t_value({}).value is None


def test_compute_funnel_matches_hand_counts(tmp_path):
    build_store(tmp_path, ["w1"], [E1, E2], intents(), reps=2, pattern=pattern_cited_half)
    report = compute(tmp_path, CFG, intents(), [E1, E2], pages=[], adapters={}, labels=["w1"])
    by = {e.surface: e for e in report.engines}
    e1 = by["api:openai"]
    # 6 intents x 2 prompts x 2 reps = 24 runs; 2 errors (u3 rep 1 on both prompts)
    assert e1.runs_total == 24 and e1.failures == {"error": 2}
    assert (e1.activation.numerator, e1.activation.denominator) == (22, 22) and e1.unknown_activation == 0
    # headline: unbranded activated runs = u0,u1,u2 (4 each) + u3 (2) = 14; cited in u0,u1 = 8
    assert (e1.cited.numerator, e1.cited.denominator, e1.cited.n_intents) == (8, 14, 4)
    assert e1.cited.value == pytest.approx((1 + 1 + 0 + 0) / 4)
    assert e1.read.value == pytest.approx(1.0) and e1.footnote_one.value == pytest.approx(0.5)
    # Ruling B28: unconditional cited counts unbranded intents only, so b0's citing runs stay out of both
    # sides: u0 and u1 cite in 8 of the 14 unbranded ok runs (u3's two errors are left out).
    unconditional = e1.unconditional_cited
    assert (unconditional.numerator, unconditional.denominator, unconditional.n_intents) == (8, 14, 4)
    assert unconditional.method == "Wilson 95%, unbranded intents" and unconditional.excluded == {"error": 2}
    assert e1.conversion.value is None and "suppressed" in e1.conversion.note  # 14 < 20
    assert e1.placebo_floor.value == pytest.approx(0.0)
    assert [r.canonical_url for r in e1.read_never_cited] == []  # the owned page is cited by u0 and u1
    assert e1.cited_instead and e1.cited_instead[0].host == "other.net"
    # u0, u1, u2: 2 pairs each; u3 has no pair
    assert e1.noise.flip_rate.denominator == 6 and e1.noise.flip_rate.numerator == 0
    e2 = by["api:anthropic"]
    assert e2.activation.value == 0.0 and e2.cited.value is None and e2.cited.note == "no data"


@pytest.mark.parametrize(
    ("placebo_cites", "flags", "band"),
    [
        ((), (False, False, False), None),  # no placebo intent: no floor, so no flag
        ((0,), (True, False, True), "(0 of 4 cited): 0.000 to 0.490"),  # one placebo intent: no interval
        ((0, 0), (True, False, True), "(0 of 8 cited): 0.000 to 0.324"),  # same rate: zero width
        ((0, 1), (True, True, True), None),  # rates differ: the floor's own t interval, -1.46 to 1.71
    ],
    ids=["no-placebo", "one-placebo", "zero-width", "t-interval"],
)
def test_indistinguishable_from_placebo_falls_back_to_wilson_over_placebo_runs(
    tmp_path, placebo_cites, flags, band
):
    """u0 is cited in 1 of its 4 runs (Wilson 0.046 to 0.699), u1 in all 4 (0.510 to 1), u2 in none (0 to
    0.490). A floor with no interval or a zero-width one is not a point: intents are judged against the
    Wilson interval of the placebo runs, which u0 overlaps and u1 clears. A floor with a real interval is
    used as it is (Wilson over its 1 of 8 runs, 0.022 to 0.471, would have cleared u1)."""
    placebos = [
        Intent(
            id=f"p{k}",
            label=f"p{k}",
            kind="placebo",
            prompts=[Prompt(id=f"p{k}p{j}", text="q", paraphrase_idx=j) for j in range(2)],
        )
        for k in range(len(placebo_cites))
    ]
    cast = intents()[:3] + placebos
    cited_runs = {"u0": 1, "u1": 4} | {p.id: n for p, n in zip(placebos, placebo_cites, strict=True)}

    def pattern(label, intent, prompt, engine, rep):
        cites = 2 * prompt.paraphrase_idx + rep < cited_runs.get(intent.id, 0)
        return ("ok", "yes", [OWN, OTHER], [OWN] if cites else [OTHER])

    build_store(tmp_path, ["w1"], [E1], cast, reps=2, pattern=pattern)
    e1 = compute(tmp_path, CFG, cast, [E1], pages=[], adapters={}, labels=["w1"]).engines[0]
    got = {row.intent_id: row.indistinguishable_from_placebo for row in e1.per_intent}
    assert got == dict(zip(["u0", "u1", "u2"], flags, strict=True)) | {p.id: False for p in placebos}
    floor = e1.placebo_floor
    assert floor.method == "Student t over intents"  # the floor itself stays the brief's t_value
    if band is None:
        assert "Wilson" not in floor.note
    else:
        assert floor.value == 0.0 and f"Wilson 95% over the placebo runs {band}" in floor.note


def test_last_record_per_run_id_wins(tmp_path):
    build_store(
        tmp_path, ["w1"], [E1], intents()[:1], reps=1, pattern=lambda *a: ("error", "unknown", [], [])
    )
    store = JsonlStore(tmp_path / ".footnote")
    runs = list(store.iter("runs", Run))
    store.append("runs", runs[0].model_copy(update={"status": "ok", "activated": "yes"}))
    assert load_runs(store)[runs[0].id].status == "ok"


def test_sources_join_their_run_only_from_its_last_raw_response(tmp_path):
    ms = build_store(
        tmp_path, ["w1"], [E1], intents()[:1], reps=1, pattern=lambda *a: ("ok", "yes", [OTHER], [OTHER])
    )
    store = JsonlStore(tmp_path / ".footnote")
    plain, retried = list(store.iter("runs", Run))

    def owned(run_id, role, sha):
        return SourceRecord(
            run_id=run_id, role=role, url=OWN, canonical_url=canonicalize(OWN), rank=1, provider_field="f",
            raw_sha256=sha,
        )

    # an earlier attempt of `plain` read and cited the owned page, but from another raw response
    store.append("sources", owned(plain.id, "consulted", "ab" * 32))
    store.append("sources", owned(plain.id, "cited", "ab" * 32))
    # `retried` ends with a record naming a new raw response, so only that response's sources join it
    store.append("sources", owned(retried.id, "consulted", "cd" * 32))
    store.append("sources", owned(retried.id, "cited", "cd" * 32))
    store.append("runs", retried.model_copy(update={"raw_sha256": "cd" * 32}))
    views = {v.run.id: v for v in run_views(tmp_path, {}, ms, OwnedSet.build(["example.org"], []))}
    other, own = canonicalize(OTHER), canonicalize(OWN)
    # None equals None: the synthetic sources, like their run, carry no raw_sha256 and still join it
    p, r = views[plain.id], views[retried.id]
    assert (p.consulted, p.cited, p.owned_consulted, p.owned_cited) == ({other}, [other], False, False)
    assert (r.consulted, r.cited, r.owned_consulted, r.owned_first) == ({own}, [own], True, True)


def test_select_manifests_by_label_and_date(tmp_path):
    ms = build_store(
        tmp_path, ["w1", "w2", "w3"], [E1], intents()[:1], reps=1, pattern=lambda *a: ("ok", "yes", [], [])
    )
    assert [m.label for m in select_manifests(ms, labels=["w2"])] == ["w2"]
    assert [m.label for m in select_manifests(ms, since=ms[1].started_at)] == ["w2", "w3"]
    assert [m.label for m in select_manifests(ms, until=ms[1].started_at)] == ["w1", "w2"]


def test_replay_uses_current_parser_when_version_differs(tmp_path):
    build_store(tmp_path, ["w1"], [E1], intents()[:1], reps=1, pattern=lambda *a: ("ok", "no", [], []))
    fixture = json.loads((Path(__file__).parent / "fixtures" / "openai_web_search_searched.json").read_text())
    fixture.pop("_doc_note", None)
    raw = RawResponse.model_validate(fixture)
    store, raw_store = JsonlStore(tmp_path / ".footnote"), RawStore(tmp_path / ".footnote")
    sha = raw_store.put(raw.as_blob())
    run = list(store.iter("runs", Run))[0]
    stale = {"raw_sha256": sha, "parser_version": "openai@0.0.0", "activated": "no"}
    store.append("runs", run.model_copy(update=stale))
    report = compute(
        tmp_path, CFG, intents()[:1], [E1], pages=[], adapters={"openai": OpenAIAdapter()}, labels=["w1"]
    )
    e1 = report.engines[0]
    # the fixture searched, whatever the stale record said
    assert e1.replayed == 1 and e1.activation.numerator == 1
    # the fixture consulted https://example.org/..., which is owned
    assert e1.read.value == pytest.approx(1.0)


def test_effective_view_status_all_failed():
    assert effective_view_status("ok", 2, 2) == "error"
    assert effective_view_status("ok", 2, 1) == "ok" and effective_view_status("ok", 0, 0) == "ok"


def test_run_views_carry_the_error_kind_and_a_replay_keeps_its_reason(tmp_path):
    """A stored run keeps its recorded error_kind; a run replayed into "every search failed" gets the reason
    effective_status gives, as all_searches_failed; the engine counts its errors by kind."""
    build_store(tmp_path, ["w1"], [E1], intents()[:1], reps=1, pattern=lambda *a: ("ok", "yes", [], []))
    store, raw_store = JsonlStore(tmp_path / ".footnote"), RawStore(tmp_path / ".footnote")
    failed_search = {"type": "web_search_call", "status": "failed", "action": {"type": "search"}}
    raw = RawResponse(
        provider="openai", model_requested="gpt-5-mini", request={},
        response={"status": "completed", "output": [failed_search], "usage": {}},
    )
    sha = raw_store.put(raw.as_blob())
    replayed, failed = list(store.iter("runs", Run))
    store.append("runs", replayed.model_copy(update={"raw_sha256": sha, "parser_version": "openai@0.0.0"}))
    store.append("runs", failed.model_copy(update={"status": "error", "error_kind": "http"}))
    manifests = list(select_manifests(load_manifests(store).values()))
    views = {v.run.id: v for v in run_views(tmp_path, {"openai": OpenAIAdapter()}, manifests, OWNED)}
    assert (views[replayed.id].status, views[replayed.id].error_kind) == ("error", "all_searches_failed")
    assert (views[failed.id].status, views[failed.id].error_kind) == ("error", "http")
    report = compute(
        tmp_path, CFG, intents()[:1], [E1], pages=[], adapters={"openai": OpenAIAdapter()}, labels=["w1"]
    )
    assert report.engines[0].error_kinds == {"all_searches_failed": 1, "http": 1}


def test_compute_covers_every_engine_config_the_bursts_ran_then_the_current_ones(tmp_path):
    """C1: engine history survives a param change. w1 ran configs whose output limit was 1000; footnote.toml
    now gives 1200. A report on w1 shows w1's configs with their data, marked not in footnote.toml, then the
    current configs with no runs."""
    old, current = shifted_project(tmp_path)
    config, intents_ = load_config(tmp_path), load_intents(tmp_path)
    w1 = compute(tmp_path, config, intents_, current, pages=[], adapters={}, labels=["w1"])
    assert [e.engine for e in w1.engines] == old + current
    assert [e.in_config for e in w1.engines] == [False, False, False, True, True, True]
    assert all(e.runs_total == 60 and e.cited.value == pytest.approx(0.5) for e in w1.engines[:3])
    assert all(e.runs_total == 0 and e.cited.value is None for e in w1.engines[3:])
    both = compute(tmp_path, config, intents_, current, pages=[], adapters={}, labels=["w1", "w2"])
    assert [e.engine for e in both.engines] == old + current and all(e.runs_total == 60 for e in both.engines)


class ParserThatRaises:
    """A newer openai parser that cannot read what it is given."""

    provider, version = "openai", "openai@9.9.9"

    def parse(self, raw):
        raise RuntimeError("the new parser cannot read this")


def test_a_replay_that_fails_or_cannot_read_its_blob_keeps_the_stored_reading_and_is_counted(tmp_path):
    """F4: a replay is a re-read, never a reason to lose a run. A parser that raises keeps the run's stored
    status, activation and sources (counted as replay_failed); a raw blob that cannot be read does the same
    (counted as unreadable); neither counts as replayed."""
    build_store(tmp_path, ["w1"], [E1], intents()[:1], reps=1, pattern=lambda *a: ("ok", "yes", [OWN], [OWN]))
    store, raw_store = JsonlStore(tmp_path / ".footnote"), RawStore(tmp_path / ".footnote")
    blob = RawResponse(provider="openai", model_requested="gpt-5-mini", request={}, response={}).as_blob()
    good = raw_store.put(blob)
    broken = "ef" * 32
    (tmp_path / ".footnote" / "raw" / f"{broken}.json").write_text("{not json", encoding="utf-8")
    first, second = list(store.iter("runs", Run))
    store.append("runs", first.model_copy(update={"raw_sha256": good, "parser_version": "openai@0.0.0"}))
    store.append("runs", second.model_copy(update={"raw_sha256": broken, "parser_version": "openai@0.0.0"}))
    e1 = compute(
        tmp_path, CFG, intents()[:1], [E1], pages=[], adapters={"openai": ParserThatRaises()}, labels=["w1"]
    ).engines[0]
    assert (e1.replayed, e1.replay_failed, e1.unreadable_blobs) == (0, 1, 1)
    assert e1.activation.numerator == 2 and e1.failures == {}  # both keep their stored status and activation
