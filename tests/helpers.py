"""Build a synthetic .footnote store for metrics, report and diff tests. Deterministic; no network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from footnoteone.canon import canonicalize
from footnoteone.config import engine_configs, load_config, load_intents, write_templates
from footnoteone.design import enumerate_calls
from footnoteone.planning import OUTPUT_LIMIT_KEYS as OUTPUT_LIMITS
from footnoteone.planning import PlanningAssumptions
from footnoteone.schema import EngineConfig, Intent, Manifest, Run, SourceRecord
from footnoteone.store import JsonlStore

OWN = "https://example.org/guide"
OTHER = "https://other.net/page"


def build_store(
    root: Path,
    labels: list[str],
    engines: list[EngineConfig],
    intents: list[Intent],
    reps: int,
    pattern,
    start=datetime(2026, 10, 1, tzinfo=UTC),
) -> list[Manifest]:
    """pattern(label, intent, prompt, engine, rep) -> (status, activated, consulted_urls, cited_urls) or None
    for no record."""
    store = JsonlStore(root / ".footnote")
    manifests = []
    for day, label in enumerate(labels):
        t = start + timedelta(days=7 * day)
        m = Manifest(
            code_version="0.0.1",
            adapter_versions={e.provider: "fake@1" for e in engines},
            config_sha="c" * 64,
            price_table_version="2026-10-05",
            budget_usd=10.0,
            label=label,
            engines=engines,
            intents=intents,
            canon_version=2,
            paraphrases=max(len(i.prompts) for i in intents),
            reps=reps,
            started_at=t,
            status="done",
            finished_at=t,
        )
        store.append("manifests", m)
        manifests.append(m)
        for call in enumerate_calls(intents, engines, reps):
            got = pattern(label, call.intent, call.prompt, call.engine, call.rep_idx)
            if got is None:
                continue
            status, activated, consulted, cited = got
            key = call.key(label)
            for role, urls in (("consulted", consulted), ("cited", cited)):
                for rank, url in enumerate(urls, start=1):
                    store.append(
                        "sources",
                        SourceRecord(
                            run_id=key,
                            role=role,
                            url=url,
                            canonical_url=canonicalize(url),
                            rank=rank,
                            provider_field="f",
                        ),
                    )
            store.append(
                "runs",
                Run(
                    id=key,
                    manifest_id=m.id,
                    intent_id=call.intent.id,
                    prompt_id=call.prompt.id,
                    engine_config_id=call.engine.config_sha,
                    rep_idx=call.rep_idx,
                    status=status,
                    model_requested=call.engine.model_requested,
                    activated=activated,
                    activation_evidence="synthetic",
                    raw_sha256=None,
                    parser_version="fake@1",
                    search_calls=1 if activated == "yes" else 0,
                    cost_usd=0.01,
                    started_at=t,
                    finished_at=t,
                ),
            )
    return manifests


def shifted_project(root: Path) -> tuple[list[EngineConfig], list[EngineConfig]]:
    """A project from the init templates with ten unbranded intents of three wordings, and two bursts of two
    repeats: w1 stored under engine configs whose output limit is 1000 (as if planning.yaml or footnote.toml
    said so then), w2 a week later under the configs footnote.toml gives now (output limit 1200). Intents
    topic-0 to topic-4 cite the owned page in every run, the rest never. Returns (w1's configs, the current
    configs), in footnote.toml order."""
    write_templates(root, "https://example.org")
    lines = ["version: 1", "intents:"]
    for k in range(10):
        lines += [f"  - id: topic-{k}", f'    label: "Topic {k}"', "    prompts:"]
        lines += [f'      - "question {k} wording {j}"' for j in range(3)]
    (root / "intents.yaml").write_text("\n".join(lines) + "\n")
    config, intents = load_config(root), load_intents(root)
    current = engine_configs(config, PlanningAssumptions.load())
    old = [
        e.model_copy(update={"params": {**e.params, **{k: 1000 for k in e.params if k in OUTPUT_LIMITS}}})
        for e in current
    ]

    def pattern(label, intent, prompt, engine, rep):
        return ("ok", "yes", [OWN], [OWN] if int(intent.id[-1]) < 5 else [OTHER])

    build_store(root, ["w1"], old, intents, reps=2, pattern=pattern)
    week_later = datetime(2026, 10, 8, tzinfo=UTC)
    build_store(root, ["w2"], current, intents, reps=2, pattern=pattern, start=week_later)
    return old, current
