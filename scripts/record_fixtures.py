"""Record one real response per provider for the test fixtures; needs the provider keys in the environment
and spends a few cents.

Run it from the repository root with `uv run python scripts/record_fixtures.py`.

For each provider whose key is set, in the environment variable footnote.toml names by default
(OPENAI_API_KEY, ANTHROPIC_API_KEY, PERPLEXITY_API_KEY), the provider's default engine is asked one prompt
that needs a search and one that needs none. The default engine is the default model in pricing.yaml with no
params of its own, so the planning limits are its only params; OpenAI therefore runs without force_search,
which would make the no-search prompt search too. The searched prompt is asked again with the engine
`footnote init` writes for the provider (config.template_engine_specs: OpenAI with force_search on) when that
engine is not the default one, and saved as <provider>_searched_template.json. Each RawResponse is written to
tests/fixtures/recorded/<provider>_<searched|no_search>.json under a _doc_note naming the date and the
adapter version, and the Observation its adapter parses is summarized, with "unexpected for this prompt" when
the answer searched although the prompt asked for none, or did not search although it asked for one.

A key is read from the environment and sent in a request header, nowhere else: the stored request is the
body, which holds no key, and nothing printed contains one. A failed call prints its HTTP status and the
response body with the exact key replaced by "***" (an error body can echo the key), or, for any other
failure, its exception type alone (a transport error's text can quote a request header). The tests load the
script by path; it is not part of the package, and the CLI does not import it.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

import httpx

from footnoteone.config import EngineSpec, KeysConfig, template_engine_specs
from footnoteone.planning import PlanningAssumptions
from footnoteone.pricing import PriceTable
from footnoteone.runner import PROVIDERS, default_adapters

RECORDED = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "recorded"
# (file name suffix, prompt, the activation the prompt asks for): one question that needs current sources,
# one that needs none.
PROMPTS = (
    (
        "searched",
        "Search the web and cite your sources: what is the latest stable release of Python, "
        "and what changed in it?",
        "yes",
    ),
    ("no_search", "What is 2 + 2? Answer from what you know, without searching the web.", "no"),
)
BODY_SHOWN = 2000  # characters of a failed call's (masked) response body that are printed


def _failure(exc: Exception, api_key: str) -> str:
    """What a failed call prints after "failed with": its HTTP status and response body with the exact key
    replaced by "***" (masked before it is cut, so no part of the key survives the cut), else its exception
    type alone."""
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text.replace(api_key, "***")[:BODY_SHOWN]
        return f"HTTP {exc.response.status_code}; nothing written. Response body (key masked): {body}"
    return f"{type(exc).__name__}; nothing written"


async def record_all(environ: Mapping[str, str], out_dir: Path, client: httpx.AsyncClient) -> int:
    """Record both prompts for every provider whose key is set, and the searched prompt with the template
    engine where it differs from the default one; 0 when every call made was recorded and at least one
    was, else 1."""
    table, assumptions = PriceTable.load(), PlanningAssumptions.load()
    adapters, names = default_adapters(), KeysConfig()
    specs = template_engine_specs(assumptions)
    templates = {spec.provider: spec.to_engine_config(assumptions) for spec in specs}
    recorded = failed = 0
    for provider in PROVIDERS:
        name = names.env_name(provider)
        api_key = environ.get(name, "").strip()
        if not api_key:
            print(f"{provider}: skipped, {name} is not set")
            continue
        adapter = adapters[provider]
        default = EngineSpec(provider=provider, model=table.providers[provider]["default_model"])
        engine = default.to_engine_config(assumptions)
        # (engine, prompt, expected activation, file name stem, what a failure line calls the call)
        jobs = [(engine, prompt, expected, kind, kind) for kind, prompt, expected in PROMPTS]
        template = templates.get(provider)
        if template is not None and template.config_sha != engine.config_sha:
            kind, prompt, expected = PROMPTS[0]
            jobs.append((template, prompt, expected, f"{kind}_template", f"{kind} with the template engine"))
        for job_engine, prompt, expected, stem, what in jobs:
            try:
                raw = await adapter.call(client, prompt, job_engine, api_key)
            except Exception as exc:  # never its text: see _failure
                print(f"{provider} {what}: failed with {_failure(exc, api_key)}")
                failed += 1
                continue
            note = f"recorded {raw.fetched_at.date().isoformat()} with {adapter.version}"
            blob = {"_doc_note": note, **raw.as_blob()}
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{provider}_{stem}.json"
            # ASCII only, like the hand-written fixtures: a live answer's dashes and curly quotes are written
            # as JSON escapes and read back unchanged.
            path.write_text(json.dumps(blob, indent=2) + "\n", encoding="utf-8")
            obs = adapter.parse(raw)
            print(f"Wrote {path}")
            print(
                f"  activated {obs.activated}; {len(obs.consulted)} consulted, {len(obs.cited)} cited; "
                f"status {obs.status}; cost {adapter.price(obs, table):.4f} USD"
            )
            if obs.activated != expected:
                asked = "a search was asked for" if expected == "yes" else "no search was asked for"
                print(f"  unexpected for this prompt: activated {obs.activated} where {asked}")
            recorded += 1
    if not recorded and not failed:
        print("Nothing recorded: set at least one of " + ", ".join(names.env_name(p) for p in PROVIDERS))
    return 0 if recorded and not failed else 1


async def _main() -> int:
    async with httpx.AsyncClient() as client:
        return await record_all(os.environ, RECORDED, client)


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
