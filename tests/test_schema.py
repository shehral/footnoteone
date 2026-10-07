import hashlib
import json
from datetime import UTC, datetime

from footnoteone.schema import (
    EngineConfig,
    Intent,
    Manifest,
    Observation,
    Page,
    Prompt,
    Run,
    SourceRecord,
    SourceRef,
    canonical_json,
    first_difference,
    run_key,
    sha256_of,
    utcnow,
)


def test_engine_config_surface_defaults_to_provider_and_sha_is_stable():
    a = EngineConfig(provider="openai", model_requested="gpt-5-mini", tool_version="web_search")
    b = EngineConfig(provider="openai", model_requested="gpt-5-mini", tool_version="web_search")
    assert a.surface == "api:openai"
    assert a.config_sha == b.config_sha
    assert len(a.config_sha) == 64


def test_engine_config_sha_changes_with_params():
    a = EngineConfig(provider="anthropic", model_requested="m", params={"max_uses": 2})
    b = EngineConfig(provider="anthropic", model_requested="m", params={"max_uses": 5})
    assert a.config_sha != b.config_sha


def test_intent_defaults_and_prompts():
    prompts = [Prompt(text="q1"), Prompt(text="q2", paraphrase_idx=1)]
    intent = Intent(label="attribution patching vs activation patching", prompts=prompts)
    assert intent.kind == "unbranded"
    assert [p.paraphrase_idx for p in intent.prompts] == [0, 1]
    assert intent.id


def test_observation_rejects_bad_activation():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Observation(activated="maybe", activation_evidence="", answer_text="")


def test_observation_model_requested_is_optional():
    assert Observation(activated="no", activation_evidence="", answer_text="").model_requested is None
    obs = Observation(activated="no", activation_evidence="", answer_text="", model_requested="fast")
    assert obs.model_requested == "fast"


def test_run_round_trips_through_json_and_ignores_unknown_fields():
    run = Run(
        manifest_id="m1", intent_id="i1", prompt_id="p1", engine_config_id="e" * 64, rep_idx=0,
        status="ok", model_requested="gpt-5-mini", activated="yes",
        activation_evidence="web_search_call present", raw_sha256="a" * 64, parser_version="openai@0.1.0",
        cost_usd=0.012, started_at=utcnow(), finished_at=utcnow(),
    )
    data = json.loads(run.model_dump_json())
    data["future_field"] = 1
    again = Run.model_validate(data)
    assert again.id == run.id and again.cost_usd == 0.012


def test_source_ref_rank_starts_at_one():
    import pytest
    from pydantic import ValidationError

    SourceRef(url="https://a.example/x", rank=1, provider_field="sources")
    with pytest.raises(ValidationError):
        SourceRef(url="https://a.example/x", rank=0, provider_field="sources")


def test_sha256_of_hashes_the_canonical_json_form():
    x = {"b": [1, {"d": None, "c": 2.5}], "a": {"z": "caf\u00e9", "y": True}}
    assert sha256_of(x) == hashlib.sha256(canonical_json(x).encode()).hexdigest()


def test_canonical_json_sorts_keys_drops_spaces_and_stringifies_other_values():
    # Golden string: content addresses and config_sha are hashes of this form, so it must not drift.
    when = datetime(2026, 10, 5, tzinfo=UTC)
    assert canonical_json({"b": 1, "a": {"d": [1, 2], "c": None}, "t": when}) == (
        '{"a":{"c":null,"d":[1,2]},"b":1,"t":"2026-10-05 00:00:00+00:00"}'
    )


def test_run_and_manifest_reject_naive_datetimes():
    import pytest
    from pydantic import ValidationError

    naive = datetime(2026, 10, 5, 12, 0)
    run_fields = dict(
        manifest_id="m1", intent_id="i1", prompt_id="p1", engine_config_id="e" * 64, status="ok",
        model_requested="gpt-5-mini", raw_sha256="a" * 64, parser_version="openai@0.1.0",
        started_at=utcnow(), finished_at=utcnow(),
    )
    Run(**run_fields)
    for field in ("started_at", "finished_at"):
        with pytest.raises(ValidationError):
            Run(**{**run_fields, field: naive})
    manifest_fields = dict(
        code_version="0.0.1", config_sha="c" * 64, price_table_version="v1", budget_usd=5.0
    )
    Manifest(**manifest_fields)
    with pytest.raises(ValidationError):
        Manifest(**manifest_fields, started_at=naive)


def test_records_reject_non_finite_floats():
    import pytest
    from pydantic import ValidationError

    run_fields = dict(
        manifest_id="m1", intent_id="i1", prompt_id="p1", engine_config_id="e" * 64, status="ok",
        model_requested="gpt-5-mini", raw_sha256="a" * 64, parser_version="openai@0.1.0",
        started_at=utcnow(), finished_at=utcnow(),
    )
    manifest_fields = dict(code_version="0.0.1", config_sha="c" * 64, price_table_version="v1")
    with pytest.raises(ValidationError):
        Run(**run_fields, cost_usd=float("inf"))
    with pytest.raises(ValidationError):
        Manifest(**manifest_fields, budget_usd=float("nan"))
    assert Run(**run_fields, cost_usd=0.012).cost_usd == 0.012
    assert Manifest(**manifest_fields, budget_usd=5.0).budget_usd == 5.0


def test_run_without_a_response_body_round_trips_through_the_store(tmp_path):
    from footnoteone.store import JsonlStore

    run = Run(
        manifest_id="m1", intent_id="i1", prompt_id="p1", engine_config_id="e" * 64, status="timeout",
        model_requested="gpt-5-mini", raw_sha256=None, parser_version=None,
        started_at=utcnow(), finished_at=utcnow(),
    )
    store = JsonlStore(tmp_path / ".footnote")
    store.append("runs", run)
    [again] = store.iter("runs", Run)
    assert again == run and again.raw_sha256 is None and again.parser_version is None


def test_page_id_is_sixteen_hex_of_canonical_url():
    page = Page(
        id=Page.id_for("https://example.org/a"), url="https://example.org/a/",
        canonical_url="https://example.org/a", source="sitemap",
    )
    assert len(page.id) == 16 and page.id == Page.id_for("https://example.org/a")
    assert page.discovered_at.tzinfo is not None


def test_run_key_is_deterministic_and_label_scoped():
    a = run_key("2026-10-07", "i1", "p1", "e" * 64, 0)
    assert a == run_key("2026-10-07", "i1", "p1", "e" * 64, 0) and len(a) == 32
    assert a != run_key("2026-10-14", "i1", "p1", "e" * 64, 0)


def test_effective_status_all_searches_failed_is_error():
    from footnoteone.schema import effective_status

    assert effective_status("ok", 2, 2) == ("error", "all searches failed")
    assert effective_status("ok", 2, 1) == ("ok", None)
    assert effective_status("ok", 0, 0) == ("ok", None)
    assert effective_status("truncated", 1, 1) == ("error", "all searches failed")


def test_manifest_new_fields_default_and_round_trip():
    m = Manifest(code_version="0.0.1", config_sha="c" * 64, price_table_version="v", budget_usd=5.0)
    assert m.label == "" and m.engines == [] and m.intents == [] and m.finished_at is None
    assert Manifest.model_validate_json(m.model_dump_json()) == m


def test_source_record_names_its_raw_response_and_older_lines_still_load():
    older = (
        '{"run_id":"r","role":"cited","url":"https://a.org/","canonical_url":"https://a.org","rank":1,'
        '"provider_field":"f"}'
    )
    assert SourceRecord.model_validate_json(older).raw_sha256 is None
    rec = SourceRecord.model_validate_json(older).model_copy(update={"raw_sha256": "ab" * 32})
    assert SourceRecord.model_validate_json(rec.model_dump_json()) == rec


def test_first_difference_names_what_sets_two_configs_of_one_model_apart():
    base = EngineConfig(provider="openai", model_requested="gpt-5-mini", params={"max_output_tokens": 1200})
    lower = base.model_copy(update={"params": {"max_output_tokens": 1000}})
    forced = base.model_copy(update={"params": {"force_search": True, "max_output_tokens": 1200}})
    tool = base.model_copy(update={"tool_version": "web_search_2026"})
    assert first_difference(lower, base) == "max_output_tokens 1000 instead of 1200"
    assert first_difference(forced, base) == "force_search true instead of no force_search"
    assert first_difference(base, forced) == "no force_search instead of force_search true"
    assert first_difference(tool, base) == "tool version web_search_2026 instead of the default"
    assert first_difference(base, base) is None
