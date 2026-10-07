from footnoteone.design import Call, enumerate_calls, headline_intents
from footnoteone.schema import EngineConfig, Intent, Prompt, run_key


def intents(n_unbranded=2):
    out = [
        Intent(
            id=f"i{k}",
            label=f"i{k}",
            prompts=[Prompt(id=f"i{k}p0", text="a"), Prompt(id=f"i{k}p1", text="b", paraphrase_idx=1)],
        )
        for k in range(n_unbranded)
    ]
    out.append(Intent(id="brand", label="brand", kind="branded", prompts=[Prompt(id="bp0", text="c")]))
    return out


def test_enumerate_order_is_rep_intent_prompt_engine():
    engines = [
        EngineConfig(provider="openai", model_requested="m"),
        EngineConfig(provider="anthropic", model_requested="n"),
    ]
    calls = enumerate_calls(intents(), engines, reps=2)
    assert len(calls) == 2 * (2 * 2 + 1) * 2
    first = [(c.rep_idx, c.intent.id, c.prompt.paraphrase_idx, c.engine.provider) for c in calls[:4]]
    assert first == [
        (0, "i0", 0, "openai"),
        (0, "i0", 0, "anthropic"),
        (0, "i0", 1, "openai"),
        (0, "i0", 1, "anthropic"),
    ]
    assert calls[-1].rep_idx == 1 and calls[-1].intent.id == "brand"


def test_call_key_matches_run_key():
    engine = EngineConfig(provider="openai", model_requested="m")
    call = Call(intents()[0], intents()[0].prompts[1], engine, 1)
    assert call.key("2026-10-07") == run_key("2026-10-07", "i0", "i0p1", engine.config_sha, 1)


def test_headline_intents_are_unbranded_only():
    assert [i.id for i in headline_intents(intents())] == ["i0", "i1"]
