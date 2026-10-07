"""The call list of one burst: every intent x wording x engine x repeat, in budget-fair order."""

from __future__ import annotations

from dataclasses import dataclass

from footnoteone.schema import EngineConfig, Intent, Prompt, run_key


@dataclass(frozen=True)
class Call:
    intent: Intent
    prompt: Prompt
    engine: EngineConfig
    rep_idx: int

    def key(self, label: str) -> str:
        return run_key(label, self.intent.id, self.prompt.id, self.engine.config_sha, self.rep_idx)


def enumerate_calls(intents: list[Intent], engines: list[EngineConfig], reps: int) -> list[Call]:
    """Repeat-major, then intent, wording, engine: if the budget runs out, every intent has some answers
    from every engine rather than some intents having none."""
    return [
        Call(intent, prompt, engine, rep)
        for rep in range(reps)
        for intent in intents
        for prompt in intent.prompts
        for engine in engines
    ]


def headline_intents(intents: list[Intent]) -> list[Intent]:
    return [i for i in intents if i.kind == "unbranded"]
