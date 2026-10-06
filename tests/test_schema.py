import hashlib
import json
from datetime import UTC, datetime

from footnoteone.schema import (
    EngineConfig,
    Intent,
    Manifest,
    Observation,
    Prompt,
    Run,
    SourceRef,
    canonical_json,
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
