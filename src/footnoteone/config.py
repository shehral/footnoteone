"""Project configuration: footnote.toml and intents.yaml, validated, with stable ids.

Keys never live here: the config names the environment variables that hold them.
"""

from __future__ import annotations

import math
import re
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from footnoteone.canon import host_of
from footnoteone.planning import PlanningAssumptions
from footnoteone.schema import EngineConfig, Intent, IntentKind, Prompt, Provider, sha256_of

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")  # what [keys] holds: an environment variable's name
ALLOWED_PARAMS: dict[str, frozenset[str]] = {
    "openai": frozenset({"force_search", "user_location", "max_output_tokens", "max_tool_calls"}),
    "anthropic": frozenset({"max_uses", "allowed_domains", "blocked_domains", "user_location", "max_tokens"}),
    "perplexity": frozenset(
        {"search_domain_filter", "search_recency_filter", "user_location", "max_output_tokens"}
    ),
}


# Engine params with a type of their own, checked when footnote.toml loads, so the planner's worst case and
# the request the adapter sends read the same values (planning.max_searches_for and max_output_for only ever
# see an int). Any other allowed param (force_search) is passed through as written.
LIMIT_PARAMS = frozenset({"max_output_tokens", "max_tool_calls", "max_tokens", "max_uses"})
DOMAIN_LIST_PARAMS = frozenset({"allowed_domains", "blocked_domains"})


# Hosts many creators share (Ruling B27). A site whose address is on one owns no domain: the platform's
# other creators must never count as the user's, so its address becomes an off-site prefix instead.
SHARED_PLATFORMS = frozenset(
    {
        "medium.com", "substack.com", "youtube.com", "github.com", "x.com", "twitter.com", "linkedin.com",
        "dev.to", "hashnode.dev", "wordpress.com", "blogspot.com", "tumblr.com", "notion.site", "bsky.app",
        "mastodon.social",
    }
)


def shared_platform(url: str) -> str | None:
    """The shared platform `url` is on, or None: its host (leading www., m. or mobile. aside) is one of
    SHARED_PLATFORMS. A creator's own subdomain, such as you.substack.com, is not the platform."""
    host = host_of(url)
    for prefix in ("m.", "mobile."):
        host = host.removeprefix(prefix)
    return host if host in SHARED_PLATFORMS else None


class ConfigError(ValueError):
    """A readable configuration problem: file, field and what to change."""


def _param_problem(key: str, value: Any) -> str | None:
    """What is wrong with one engine param's value, or None when its type is right."""
    if key in LIMIT_PARAMS:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return f"{key} must be a whole number of at least 1, got {value!r}"
    elif key == "user_location":
        if not isinstance(value, dict):
            example = '{type = "approximate", country = "GB"}'
            return f"user_location must be a table such as {example}, got {value!r}"
    elif key in DOMAIN_LIST_PARAMS:
        if not isinstance(value, list) or not all(isinstance(d, str) and d.strip() for d in value):
            return f'{key} must be a list of non-empty host names such as ["example.org"], got {value!r}'
    elif key == "search_domain_filter":
        if not isinstance(value, list) or not all(isinstance(d, str) for d in value):
            return f'search_domain_filter must be a list of strings such as ["example.org"], got {value!r}'
    elif key == "search_recency_filter":
        if not isinstance(value, str):
            return f'search_recency_filter must be text such as "week", got {value!r}'
    return None


class Strict(BaseModel):
    """Base of the config models. A validation error never repeats the value it refused
    (hide_input_in_errors): a key pasted into footnote.toml must not reach a message."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)


def _http_url(value: str, what: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(
            f"{what} must be a full http or https URL with a host, got {value!r} (missing scheme?)"
        )
    return value.strip()


class SiteConfig(Strict):
    url: str
    name: str = ""
    own_domains: list[str] = Field(default_factory=list)
    offsite_prefixes: list[str] = Field(default_factory=list)
    youtube_channels: list[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _http_url(v, "site.url")

    @field_validator("offsite_prefixes")
    @classmethod
    def _prefixes(cls, v: list[str]) -> list[str]:
        return [_http_url(p, "site.offsite_prefixes entry") for p in v]

    @model_validator(mode="after")
    def _domains(self) -> SiteConfig:
        """own_domains left out is the site's host. Written out empty, it is allowed only beside an off-site
        prefix: a site on a shared platform owns no domain, and something must count as the user's."""
        cleaned = []
        for d in self.own_domains:
            d = d.strip().lower()
            if not d:
                raise ValueError("site.own_domains has an empty entry")
            if "://" in d or "/" in d:
                raise ValueError(f"site.own_domains entries are bare hosts, not URLs: {d!r}")
            cleaned.append(d.removeprefix("www."))
        if not cleaned and "own_domains" not in self.model_fields_set:
            cleaned = [host_of(self.url)]
        if not cleaned and not self.offsite_prefixes:
            raise ValueError(
                "site.own_domains is empty and so is site.offsite_prefixes: nothing would count as yours; "
                "list your domain, or your profile's address in offsite_prefixes"
            )
        self.own_domains = cleaned
        return self


class EngineSpec(Strict):
    provider: Provider
    model: str = Field(min_length=1)
    tool_version: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _params(self) -> EngineSpec:
        unknown = set(self.params) - ALLOWED_PARAMS[self.provider]
        if unknown:
            raise ValueError(
                f"engine {self.provider}/{self.model}: unknown params {sorted(unknown)}; "
                f"allowed: {sorted(ALLOWED_PARAMS[self.provider])}"
            )
        if {"allowed_domains", "blocked_domains"} <= set(self.params):
            raise ValueError("anthropic takes allowed_domains or blocked_domains, not both")
        for key, value in self.params.items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"param {key} must be finite")
            problem = _param_problem(key, value)
            if problem:
                raise ValueError(f"engine {self.provider}/{self.model}: param {problem}")
        return self

    def to_engine_config(self, assumptions: PlanningAssumptions) -> EngineConfig:
        """Planning limits merged under the user's params so they enter config_sha."""
        params = {**assumptions.providers[self.provider].limits, **self.params}
        return EngineConfig(
            provider=self.provider, model_requested=self.model, tool_version=self.tool_version, params=params
        )


class DesignConfig(Strict):
    paraphrases: int = Field(default=3, ge=1, le=10)
    reps: int = Field(default=2, ge=1, le=10)
    budget_usd_per_burst: float = Field(gt=0)
    bursts_per_month: int = Field(default=4, ge=1)  # `footnote plan` prices a month at this many bursts
    baseline_cited_rate: float = Field(default=0.2, ge=0.0, le=1.0)
    icc: float = Field(default=0.3, ge=0.0, le=1.0)
    min_shared_intents: int = Field(default=8, ge=8)
    call_timeout_s: float = Field(default=120.0, gt=0)
    politeness_s: float = Field(default=0.0, ge=0)


class KeysConfig(Strict):
    """The environment variable each provider's key is read from; names only, never keys."""

    openai: str = "OPENAI_API_KEY"
    anthropic: str = "ANTHROPIC_API_KEY"
    perplexity: str = "PERPLEXITY_API_KEY"

    @field_validator("openai", "anthropic", "perplexity")
    @classmethod
    def _name(cls, value: str, info: ValidationInfo) -> str:
        """A variable's name: capital letters, digits and underscores, not starting with a digit, at most 64.
        The message never repeats the value, which is most likely a key pasted in its variable's place."""
        if not ENV_NAME.fullmatch(value):
            raise ValueError(
                f"keys.{info.field_name} must be the name of an environment variable (capital letters, "
                "digits and underscores, not starting with a digit, at most 64 characters), such as "
                "OPENAI_API_KEY; put the key itself in that variable, never in footnote.toml"
            )
        return value

    def env_name(self, provider: str) -> str:
        return getattr(self, provider)


class ProjectConfig(Strict):
    site: SiteConfig
    engines: list[EngineSpec] = Field(min_length=1)
    design: DesignConfig
    keys: KeysConfig = Field(default_factory=KeysConfig)

    @model_validator(mode="after")
    def _unique_engines(self) -> ProjectConfig:
        seen: set[str] = set()
        for spec in self.engines:
            key = sha256_of([spec.provider, spec.model, spec.tool_version, spec.params])
            if key in seen:
                raise ValueError(f"duplicate engine {spec.provider}/{spec.model} with the same params")
            seen.add(key)
        return self


class IntentSpec(Strict):
    id: str
    label: str = Field(min_length=1)
    kind: IntentKind = "unbranded"
    target_pages: list[str] = Field(default_factory=list)
    prompts: list[str] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG.match(v):
            raise ValueError(
                f"intent id {v!r} must be a slug: lowercase letters, digits and hyphens, 2 to 64 characters"
            )
        return v

    @field_validator("prompts")
    @classmethod
    def _prompts(cls, v: list[str]) -> list[str]:
        if any(not p.strip() for p in v):
            raise ValueError("every prompt must be non-empty text")
        return [p.strip() for p in v]


class IntentsFile(Strict):
    version: int = 1
    intents: list[IntentSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> IntentsFile:
        ids = [i.id for i in self.intents]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate intent ids: {dupes}")
        return self


def _errors(exc: ValidationError, file: str) -> ConfigError:
    lines = [f"{file}: " + ".".join(str(x) for x in e["loc"]) + f": {e['msg']}" for e in exc.errors()]
    return ConfigError("\n".join(lines))


def _read_text(path: Path) -> str:
    """The file's text; ConfigError naming the file when it is not UTF-8 or cannot be read (a folder in its
    place, no permission)."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path}: not UTF-8 text ({exc.reason} at byte {exc.start})") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot be read: {exc.strerror or exc}") from exc


def load_config(root: Path) -> ProjectConfig:
    path = Path(root) / "footnote.toml"
    if not path.exists():
        raise ConfigError(f"{path} not found; run `footnote init <site url>` first")
    text = _read_text(path)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: not valid TOML: {exc}") from exc
    try:
        config = ProjectConfig.model_validate(data)
    except ValidationError as exc:
        raise _errors(exc, str(path)) from exc
    # Engines that merge into one config fail at load time too, where every command reports ConfigError.
    try:
        engine_configs(config, PlanningAssumptions.load())
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return config


def prompt_id(intent_id: str, paraphrase_idx: int, text: str) -> str:
    return sha256_of([intent_id, paraphrase_idx, text])[:16]


def load_intents(root: Path) -> list[Intent]:
    path = Path(root) / "intents.yaml"
    if not path.exists():
        raise ConfigError(f"{path} not found; run `footnote init <site url>` first")
    text = _read_text(path)
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML: {exc}") from exc
    try:
        parsed = IntentsFile.model_validate(data)
    except ValidationError as exc:
        raise _errors(exc, str(path)) from exc
    return [
        Intent(
            id=spec.id,
            label=spec.label,
            kind=spec.kind,
            target_page_ids=list(spec.target_pages),
            prompts=[
                Prompt(id=prompt_id(spec.id, i, text), text=text, paraphrase_idx=i)
                for i, text in enumerate(spec.prompts)
            ],
        )
        for spec in parsed.intents
    ]


def engine_configs(config: ProjectConfig, assumptions: PlanningAssumptions) -> list[EngineConfig]:
    """Each engine with the planning limits merged under its params.

    ConfigError when two entries merge into one engine config (for example one spells out a planning
    default the other gets merged in): run_key builds each Run id from config_sha, so their runs would
    collide.
    """
    engines = [spec.to_engine_config(assumptions) for spec in config.engines]
    first: dict[str, int] = {}
    for idx, engine in enumerate(engines):
        prior = first.setdefault(engine.config_sha, idx)
        if prior != idx:
            raise ConfigError(
                f"duplicate engine: engines.{prior} and engines.{idx} ({engine.provider}/"
                f"{engine.model_requested}) are the same engine config once the planning limits are merged "
                f"under their params {dict(sorted(engine.params.items()))}; their runs would share ids, so "
                "remove one or give them different params"
            )
    return engines


def config_sha_of(config: ProjectConfig, intents: list[Intent], engines: list[EngineConfig]) -> str:
    """Identity of a design: what decides the calls a burst makes and how they are judged, that is the site,
    the engine config shas, the intents (id, kind and prompt ids, so their wordings) and the paraphrases and
    reps. The budget, the pacing (politeness, timeouts), bursts_per_month and the planning priors (baseline,
    icc, min_shared_intents) change what a burst costs or how it is planned, not what it measures."""
    return sha256_of(
        {
            "site": config.site.model_dump(),
            "engines": [e.config_sha for e in engines],
            "intents": [[i.id, i.kind, [p.id for p in i.prompts]] for i in intents],
            "paraphrases": config.design.paraphrases,
            "reps": config.design.reps,
        }
    )


# The engines `footnote init` writes: (provider, model, params of their own). write_templates adds each
# provider's planning limits from planning.yaml to its params (Ruling B25), so a later change to planning.yaml
# cannot change the engine configs of a project that already exists.
TEMPLATE_ENGINES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("openai", "gpt-5-mini", {"force_search": True}),
    ("anthropic", "claude-sonnet-4-5", {}),
    ("perplexity", "fast", {}),
)


def template_engine_specs(assumptions: PlanningAssumptions) -> list[EngineSpec]:
    """The engines footnote.toml starts with, as written: each template engine's params with its provider's
    planning limits added, sorted by name."""
    specs = []
    for provider, model, params in TEMPLATE_ENGINES:
        merged = {**assumptions.providers[provider].limits, **params}
        specs.append(EngineSpec(provider=provider, model=model, params=dict(sorted(merged.items()))))
    return specs


def _toml_value(value: Any) -> str:
    """A template param as TOML: a bool as true or false, an int as written."""
    return str(value).lower() if isinstance(value, bool) else str(value)


def _engines_toml(assumptions: PlanningAssumptions) -> str:
    blocks = []
    for spec in template_engine_specs(assumptions):
        lines = ["[[engines]]", f'provider = "{spec.provider}"', f'model = "{spec.model}"']
        lines += ["[engines.params]", *(f"{key} = {_toml_value(v)}" for key, v in spec.params.items())]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


TOML_TEMPLATE = """\
# footnote.toml: FootnoteOne project configuration. API keys live in environment variables, never here.

[site]
{site}

# An engine is its provider, model and params, call limits included: a burst run with other params is
# another engine config, reported apart from this one. The limits cap what one call can cost.
{engines}

[design]
paraphrases = 3                     # wordings per intent
reps = 2                            # repeats per wording per burst
budget_usd_per_burst = 20.0         # hard cap; `footnote plan` shows the worst case before you spend
bursts_per_month = 4                # bursts a month (4 is about weekly); `footnote plan` prices the month
baseline_cited_rate = 0.2           # planning prior until you have data
icc = 0.3                           # answers to one intent are correlated; measured later
min_shared_intents = 8              # no verdict below this (METRICS 0.2.0)

[keys]
openai = "OPENAI_API_KEY"
anthropic = "ANTHROPIC_API_KEY"
perplexity = "PERPLEXITY_API_KEY"
"""

INTENTS_TEMPLATE = """\
# intents.yaml: what readers ask that your pages answer. Ids are permanent; edit wording, not ids.
version: 1
intents:
  - id: example-topic
    label: "A question your page answers"
    kind: unbranded                 # unbranded | branded | placebo
    target_pages: []                # page ids from .footnote/pages.jsonl, or URLs
    prompts:
      - "A question a reader would type, first wording"
      - "The same question, second wording"
      - "The same question, third wording"
  - id: placebo-water
    label: "Placebo: a question your pages cannot answer"
    kind: placebo
    prompts:
      - "What is the boiling point of water at sea level?"
"""


_CHANNELS = "# channel URLs or ids; videos are discovered through the channel feed"


def _site_toml(site_url: str) -> str:
    """The [site] table's lines for `site_url`: its host as the own domain, or, for a site on a shared
    platform (Ruling B27), no own domain and the address as an off-site prefix, with a comment that says
    why (and a YouTube channel's address listed as its channel too)."""
    platform = shared_platform(site_url)
    lines = [f'url = "{site_url}"', 'name = ""']
    if platform is None:
        lines += [
            f'own_domains = ["{host_of(site_url)}"]            # subdomains count as yours',
            'offsite_prefixes = []               # for example "https://medium.com/@you", '
            '"https://you.substack.com"',
            f"youtube_channels = []               {_CHANNELS}",
        ]
        return "\n".join(lines)
    channels = f'["{site_url}"]' if platform == "youtube.com" else "[]"
    lines += [
        f"# {platform} is shared by many creators, so no domain on it counts as yours: own_domains stays",
        "# empty (allowed only while offsite_prefixes lists something) and the pages under these prefixes",
        "# count. Add your own domain, if you have one, to own_domains.",
        "own_domains = []",
        f'offsite_prefixes = ["{site_url}"]',
        f"youtube_channels = {channels}  {_CHANNELS}",
    ]
    return "\n".join(lines)


def write_templates(root: Path, site_url: str, force: bool = False) -> list[Path]:
    """Write footnote.toml and intents.yaml for a new project, then load the written footnote.toml once, so a
    template problem is a ConfigError rather than a broken file found later. ConfigError too if the site url
    is not a full http or https URL, holds a character a TOML string cannot hold as written (a quote, a
    backslash, a control character), is a shared platform's home page, or if either file exists and not
    force."""
    try:
        site_url = _http_url(site_url, "site url")
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if any(char in '"\\' or char < " " or char == "\x7f" for char in site_url):
        raise ConfigError(f"site url must not hold a quote, backslash or control character, got {site_url!r}")
    platform = shared_platform(site_url)
    if platform and urlsplit(site_url).path in ("", "/"):
        raise ConfigError(
            f"{platform} is shared by many creators: give your profile's address on it, such as "
            f"https://{platform}/@you, not the platform's home page"
        )
    root = Path(root)
    engines = _engines_toml(PlanningAssumptions.load())
    targets = {
        "footnote.toml": TOML_TEMPLATE.format(site=_site_toml(site_url), engines=engines),
        "intents.yaml": INTENTS_TEMPLATE,
    }
    existing = [name for name in targets if (root / name).exists()]
    if existing and not force:
        raise ConfigError(f"{', '.join(existing)} already exists in {root}; pass --force to overwrite")
    written = []
    for name, text in targets.items():
        (root / name).write_text(text, encoding="utf-8")
        written.append(root / name)
    load_config(root)
    return written
