from dataclasses import replace

import pytest
from pydantic import ValidationError

from footnoteone import config as config_module
from footnoteone.canon import classify_owner, host_of
from footnoteone.config import (
    ALLOWED_PARAMS,
    ConfigError,
    EngineSpec,
    KeysConfig,
    config_sha_of,
    engine_configs,
    load_config,
    load_intents,
    shared_platform,
    write_templates,
)
from footnoteone.library import owned_set_for
from footnoteone.planning import PlanningAssumptions, max_output_for, max_searches_for, worst_case_usd
from footnoteone.pricing import PriceTable

TOML = """
[site]
url = "https://example.org"
offsite_prefixes = ["https://medium.com/@ali"]
youtube_channels = ["UC1234567890123456789012"]

[[engines]]
provider = "openai"
model = "gpt-5-mini"
[engines.params]
force_search = true

[[engines]]
provider = "anthropic"
model = "claude-sonnet-4-5"

[design]
paraphrases = 3
reps = 2
budget_usd_per_burst = 12.5
"""

INTENTS = """
version: 1
intents:
  - id: attribution-patching
    label: attribution patching vs activation patching
    prompts: ["What is attribution patching?", "Explain attribution patching"]
  - id: placebo-water
    label: placebo
    kind: placebo
    prompts: ["Boiling point of water at sea level?"]
"""


def write(root, toml=TOML, intents=INTENTS):
    (root / "footnote.toml").write_text(toml)
    (root / "intents.yaml").write_text(intents)


def test_load_config_derives_own_domain_and_defaults(tmp_path):
    write(tmp_path)
    cfg = load_config(tmp_path)
    assert cfg.site.own_domains == ["example.org"]
    assert cfg.design.icc == 0.3 and cfg.design.min_shared_intents == 8 and cfg.design.reps == 2
    assert cfg.keys.env_name("openai") == "OPENAI_API_KEY"


def test_engine_configs_merge_planning_limits_under_user_params():
    cfg_engines = [
        EngineSpec(provider="openai", model="gpt-5-mini", params={"force_search": True, "max_tool_calls": 1})
    ]
    [e] = engine_configs_for(cfg_engines)
    assert e.params == {"max_output_tokens": 1200, "max_tool_calls": 1, "force_search": True}
    assert e.surface == "api:openai"


def engine_configs_for(specs):
    a = PlanningAssumptions.load()
    return [s.to_engine_config(a) for s in specs]


@pytest.mark.parametrize(
    "toml, message",
    [
        (TOML.replace('url = "https://example.org"', 'url = "example.org"'), "scheme"),
        (TOML.replace('force_search = true', 'temperature = 0.2'), "temperature"),
        (TOML.replace('budget_usd_per_burst = 12.5', 'budget_usd_per_burst = inf'), "finite"),
        (TOML.replace('budget_usd_per_burst = 12.5', 'budget_usd_per_burst = 0'), "budget"),
        (TOML.replace('[design]', '[design]\nunknown = 1'), "unknown"),
        (
            TOML
            + '\n[[engines]]\nprovider = "anthropic"\nmodel = "x"\n[engines.params]\n'
            + 'allowed_domains = ["a"]\nblocked_domains = ["b"]\n',
            "not both",
        ),
        (
            TOML
            + '\n[[engines]]\nprovider = "openai"\nmodel = "gpt-5-mini"\n[engines.params]\n'
            + 'force_search = true\n',
            "duplicate",
        ),
    ],
)
def test_load_config_rejects(tmp_path, toml, message):
    write(tmp_path, toml=toml)
    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path)


def test_missing_files_raise_config_error(tmp_path):
    with pytest.raises(ConfigError, match="footnote.toml"):
        load_config(tmp_path)


def test_load_intents_gives_stable_ids(tmp_path):
    write(tmp_path)
    intents = load_intents(tmp_path)
    again = load_intents(tmp_path)
    assert [i.id for i in intents] == ["attribution-patching", "placebo-water"]
    assert intents[0].prompts[1].paraphrase_idx == 1 and len(intents[0].prompts[1].id) == 16
    assert [p.id for p in intents[0].prompts] == [p.id for p in again[0].prompts]
    assert intents[1].kind == "placebo"


@pytest.mark.parametrize(
    "yaml_text, message",
    [
        (INTENTS.replace("placebo-water", "attribution-patching"), "duplicate"),
        (INTENTS.replace("id: placebo-water", "id: Placebo Water"), "slug"),
        (INTENTS.replace('prompts: ["Boiling point of water at sea level?"]', "prompts: []"), "prompt"),
        (INTENTS.replace("kind: placebo", "kind: control"), "kind"),
    ],
)
def test_load_intents_rejects(tmp_path, yaml_text, message):
    write(tmp_path, intents=yaml_text)
    with pytest.raises(ConfigError, match=message):
        load_intents(tmp_path)


def test_config_sha_changes_with_intents_and_engines(tmp_path):
    write(tmp_path)
    cfg, intents = load_config(tmp_path), load_intents(tmp_path)
    engines = engine_configs(cfg, PlanningAssumptions.load())
    sha = config_sha_of(cfg, intents, engines)
    assert sha == config_sha_of(cfg, intents, engines) and len(sha) == 64
    assert sha != config_sha_of(cfg, intents[:1], engines)


def test_write_templates_round_trip(tmp_path):
    paths = write_templates(tmp_path, "https://example.org")
    assert [p.name for p in paths] == ["footnote.toml", "intents.yaml"]
    cfg = load_config(tmp_path)
    assert cfg.site.url == "https://example.org" and len(cfg.engines) == 3
    assert any(i.kind == "placebo" for i in load_intents(tmp_path))
    with pytest.raises(ConfigError, match="exists"):
        write_templates(tmp_path, "https://example.org")
    assert "OPENAI_API_KEY" in (tmp_path / "footnote.toml").read_text()
    assert "sk-" not in (tmp_path / "footnote.toml").read_text()


def test_allowed_params_cover_each_provider():
    assert {"force_search", "user_location", "max_output_tokens", "max_tool_calls"} <= (
        ALLOWED_PARAMS["openai"]
    )
    assert {"max_uses", "allowed_domains", "blocked_domains", "user_location", "max_tokens"} <= (
        ALLOWED_PARAMS["anthropic"]
    )
    assert {"search_domain_filter", "search_recency_filter", "user_location", "max_output_tokens"} <= (
        ALLOWED_PARAMS["perplexity"]
    )


SECOND_OPENAI = (
    '\n[[engines]]\nprovider = "openai"\nmodel = "gpt-5-mini"\n[engines.params]\nforce_search = true\n'
)


def test_load_config_rejects_engines_that_merge_into_one_config(tmp_path):
    # engines.2 spells out max_tool_calls = 3, the planning limit engines.0 gets merged in: both end up with
    # one config_sha, so every run_key (the Run id) of the two would collide.
    write(tmp_path, toml=TOML + SECOND_OPENAI + "max_tool_calls = 3\n")
    with pytest.raises(ConfigError, match=r"engines\.0 and engines\.2 \(openai/gpt-5-mini\)") as info:
        load_config(tmp_path)
    assert str(info.value).startswith(str(tmp_path / "footnote.toml"))


def test_engine_configs_reject_a_collision_a_planning_limit_change_creates(tmp_path):
    # Distinct at the shipped max_tool_calls (3); raising that limit to 5 merges engines.2 into engines.0.
    toml = TOML.replace("force_search = true", "force_search = true\nmax_tool_calls = 5") + SECOND_OPENAI
    write(tmp_path, toml=toml)
    cfg, a = load_config(tmp_path), PlanningAssumptions.load()
    assert len({e.config_sha for e in engine_configs(cfg, a)}) == 3
    openai = a.providers["openai"]
    raised = replace(openai, limits={**openai.limits, "max_tool_calls": 5})
    with pytest.raises(ConfigError, match=r"engines\.0 and engines\.2"):
        engine_configs(cfg, replace(a, providers={**a.providers, "openai": raised}))


def test_write_templates_reports_a_url_without_scheme_as_config_error(tmp_path):
    with pytest.raises(ConfigError, match="scheme"):
        write_templates(tmp_path, "example.org")
    assert list(tmp_path.iterdir()) == []


def test_design_bursts_per_month_defaults_to_about_weekly_and_must_be_positive(tmp_path):
    # Spec section 3 item 2: `footnote plan` prices a burst and a month, so the design says how many bursts
    # a month it runs.
    write(tmp_path)
    assert load_config(tmp_path).design.bursts_per_month == 4
    write(tmp_path, toml=TOML.replace("reps = 2", "reps = 2\nbursts_per_month = 30"))
    assert load_config(tmp_path).design.bursts_per_month == 30
    write(tmp_path, toml=TOML.replace("reps = 2", "reps = 2\nbursts_per_month = 0"))
    with pytest.raises(ConfigError, match="bursts_per_month"):
        load_config(tmp_path)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    write_templates(fresh, "https://example.org")
    assert "bursts_per_month = 4" in (fresh / "footnote.toml").read_text()
    assert load_config(fresh).design.bursts_per_month == 4


# Engine params are typed before anything reads them: the planner's worst case and the request must read the
# same limit. A float such as max_tool_calls = 10.0 used to reach the request as 10 while the reservation fell
# back to 3 searches.
LIMIT_KEYS = {
    "openai": ("max_output_tokens", "max_tool_calls"),
    "anthropic": ("max_tokens", "max_uses"),
    "perplexity": ("max_output_tokens",),
}
MODELS = {"openai": "gpt-5-mini", "anthropic": "claude-sonnet-4-5", "perplexity": "fast"}


@pytest.mark.parametrize(
    "provider, key", [(p, k) for p, keys in LIMIT_KEYS.items() for k in keys], ids=lambda x: str(x)
)
@pytest.mark.parametrize("value", [10.0, "10", True, 0, -3, None, [10]], ids=repr)
def test_limit_params_must_be_whole_numbers_of_at_least_1(provider, key, value):
    with pytest.raises(ValueError, match=rf"{key} must be a whole number of at least 1"):
        EngineSpec(provider=provider, model=MODELS[provider], params={key: value})


@pytest.mark.parametrize(
    "provider, params, message",
    [
        ("openai", {"user_location": "US"}, "user_location must be a table"),
        ("anthropic", {"user_location": ["US"]}, "user_location must be a table"),
        ("anthropic", {"allowed_domains": "example.com"}, "allowed_domains must be a list of"),
        ("anthropic", {"allowed_domains": ["example.com", ""]}, "allowed_domains must be a list of"),
        ("anthropic", {"blocked_domains": [3]}, "blocked_domains must be a list of"),
        ("perplexity", {"search_domain_filter": "example.com"}, "search_domain_filter must be a list of"),
        ("perplexity", {"search_domain_filter": [1]}, "search_domain_filter must be a list of"),
        ("perplexity", {"search_recency_filter": 7}, "search_recency_filter must be text"),
    ],
)
def test_structured_params_are_type_checked(provider, params, message):
    with pytest.raises(ValueError, match=message):
        EngineSpec(provider=provider, model=MODELS[provider], params=params)


def test_well_typed_params_load_and_the_planner_reads_them():
    a, table = PlanningAssumptions.load(), PriceTable.load()
    spec = EngineSpec(
        provider="anthropic", model="claude-sonnet-4-5",
        params={"max_uses": 8, "max_tokens": 4000, "user_location": {"type": "approximate", "country": "GB"},
                "allowed_domains": ["example.org"]},
    )
    engine = spec.to_engine_config(a)
    assert max_searches_for(engine, a) == 8 and max_output_for(engine, a) == 4000
    assert worst_case_usd(engine, table, a) == pytest.approx(
        8 * 0.01 + table.token_usd("anthropic", "claude-sonnet-4-5", 10000, 4000)
    )
    perplexity = EngineSpec(
        provider="perplexity", model="fast",
        params={"search_domain_filter": ["example.org"], "search_recency_filter": "week"},
    )
    assert perplexity.params["search_recency_filter"] == "week"


def test_a_float_limit_in_footnote_toml_is_a_config_error_naming_it(tmp_path):
    write(tmp_path, toml=TOML.replace("force_search = true", "force_search = true\nmax_tool_calls = 10.0"))
    with pytest.raises(ConfigError, match=r"engines\.0.*max_tool_calls must be a whole number of at least 1"):
        load_config(tmp_path)


# Ruling B25: init writes each engine's planning limits into footnote.toml, so a later change to planning.yaml
# cannot change an existing project's engine configs (and so its engine history). The shas below are the
# template engines' config_sha computed before the limits were written out, when they were merged in from
# planning.yaml: writing them out changes no identity.
TEMPLATE_SHAS = [
    "d3e4754d4850f5f6d6c95aa7088d74aa6f928080e80f0774d11200b526e57485",  # openai gpt-5-mini, force_search
    "805143d5e3d6cfd9a98c30f4d8f739d81a83dce1caef1fb45c4bea3571f0f24a",  # anthropic claude-sonnet-4-5
    "8d1021ca65b644f213e71b327407db3ef65d2c8c7fa63a7e2d518ced7722c072",  # perplexity fast
]


def test_the_template_writes_the_planning_limits_and_keeps_the_engine_identities(tmp_path):
    write_templates(tmp_path, "https://example.org")
    cfg, a = load_config(tmp_path), PlanningAssumptions.load()
    assert [spec.params for spec in cfg.engines] == [
        {"force_search": True, "max_output_tokens": 1200, "max_tool_calls": 3},
        {"max_tokens": 1200, "max_uses": 3},
        {"max_output_tokens": 1200},
    ]
    assert [e.config_sha for e in engine_configs(cfg, a)] == TEMPLATE_SHAS


def test_a_planning_limit_change_leaves_a_templated_project_alone(tmp_path):
    write_templates(tmp_path, "https://example.org")
    cfg, a = load_config(tmp_path), PlanningAssumptions.load()
    raised = {
        name: replace(p, limits={key: value + 100 for key, value in p.limits.items()})
        for name, p in a.providers.items()
    }
    assert [e.config_sha for e in engine_configs(cfg, replace(a, providers=raised))] == TEMPLATE_SHAS
    # An engine written without limits still gets them merged in, so it does follow planning.yaml.
    bare = EngineSpec(provider="anthropic", model="claude-sonnet-4-5")
    assert bare.to_engine_config(replace(a, providers=raised)).params == {"max_tokens": 1300, "max_uses": 103}


# config_sha_of names the design (M7): the site, the engine configs, the intents and their wordings, and the
# paraphrases and reps. Money, pacing and the planning priors are not the design.
def design_sha(tmp_path, toml=TOML, intents=INTENTS):
    write(tmp_path, toml=toml, intents=intents)
    cfg = load_config(tmp_path)
    return config_sha_of(cfg, load_intents(tmp_path), engine_configs(cfg, PlanningAssumptions.load()))


@pytest.mark.parametrize(
    "extra",
    [
        "budget_usd_per_burst = 99.0",
        "politeness_s = 2.0",
        "call_timeout_s = 30.0",
        "bursts_per_month = 30",
        "baseline_cited_rate = 0.4",
        "icc = 0.1",
        "min_shared_intents = 12",
    ],
)
def test_config_sha_ignores_budget_pacing_and_priors(tmp_path, extra):
    base = design_sha(tmp_path)
    key = extra.split(" = ")[0]
    toml = TOML.replace("budget_usd_per_burst = 12.5", "") if key == "budget_usd_per_burst" else TOML
    assert design_sha(tmp_path, toml=toml + extra + "\n") == base


@pytest.mark.parametrize(
    "old, new",
    [
        ("reps = 2", "reps = 3"),
        ("paraphrases = 3", "paraphrases = 2"),
        ('url = "https://example.org"', 'url = "https://example.net"'),
        ("force_search = true", "force_search = false"),
    ],
)
def test_config_sha_follows_the_design(tmp_path, old, new):
    assert design_sha(tmp_path, toml=TOML.replace(old, new)) != design_sha(tmp_path)


def test_config_sha_follows_the_intents_and_their_wordings(tmp_path):
    base = design_sha(tmp_path)
    assert design_sha(tmp_path, intents=INTENTS.replace("Explain attribution", "Define attribution")) != base
    assert design_sha(tmp_path, intents=INTENTS.replace("kind: placebo", "kind: branded")) != base


# E5 (Ruling B27): a site on a platform many creators share gets no own domain; its address becomes an
# off-site prefix, so the platform's other creators never count as the user's.
SHARED = [
    ("https://medium.com/@ali", "medium.com"),
    ("https://www.youtube.com/@alichannel", "youtube.com"),
    ("https://github.com/ali", "github.com"),
    ("https://x.com/ali", "x.com"),
    ("https://substack.com/@ali", "substack.com"),
    ("https://bsky.app/profile/ali.bsky.social", "bsky.app"),
    ("https://mobile.twitter.com/ali", "twitter.com"),
]


@pytest.mark.parametrize("url, platform", SHARED)
def test_a_shared_platform_site_gets_no_own_domain_and_its_address_as_a_prefix(tmp_path, url, platform):
    assert shared_platform(url) == platform
    write_templates(tmp_path, url)
    site = load_config(tmp_path).site
    assert site.own_domains == [] and site.offsite_prefixes == [url]
    text = (tmp_path / "footnote.toml").read_text()
    assert f"{platform} is shared by many creators" in text and "own_domains = []" in text
    owned = owned_set_for(load_config(tmp_path), [])
    assert classify_owner(url.rstrip("/") + "/a-post", owned) == "own_offsite"
    assert classify_owner(f"https://{platform}/someone-else", owned) == "other"


def test_a_youtube_channel_site_is_also_a_listed_channel(tmp_path):
    write_templates(tmp_path, "https://www.youtube.com/@alichannel")
    assert load_config(tmp_path).site.youtube_channels == ["https://www.youtube.com/@alichannel"]


@pytest.mark.parametrize(
    "url",
    ["https://ali.substack.com", "https://ali.medium.com/notes", "https://example.org", "https://ali.github.io"],
)
def test_a_site_on_its_own_host_keeps_its_domain(tmp_path, url):
    assert shared_platform(url) is None
    write_templates(tmp_path, url)
    assert load_config(tmp_path).site.own_domains == [host_of(url)]


def test_a_shared_platform_s_home_page_is_not_a_site(tmp_path):
    with pytest.raises(ConfigError, match="medium.com is shared by many creators"):
        write_templates(tmp_path, "https://medium.com/")
    assert list(tmp_path.iterdir()) == []


def test_own_domains_may_be_empty_only_beside_an_offsite_prefix(tmp_path):
    shared = TOML.replace('url = "https://example.org"', 'url = "https://example.org"\nown_domains = []')
    write(tmp_path, toml=shared)
    assert load_config(tmp_path).site.own_domains == []  # the medium.com prefix counts as theirs
    no_prefix = shared.replace('offsite_prefixes = ["https://medium.com/@ali"]', "offsite_prefixes = []")
    write(tmp_path, toml=no_prefix)
    with pytest.raises(ConfigError, match="own_domains is empty and so is site.offsite_prefixes"):
        load_config(tmp_path)
    write(tmp_path, toml=TOML)  # left out, own_domains is still the site's host
    assert load_config(tmp_path).site.own_domains == ["example.org"]


# E6: a site URL a TOML string cannot hold as written is refused, and the written footnote.toml is loaded.


@pytest.mark.parametrize("url", ['https://example.org/a"b', "https://example.org/a\\b", "https://example.org/a\tb"])
def test_write_templates_refuses_a_url_a_toml_string_cannot_hold(tmp_path, url):
    with pytest.raises(ConfigError, match="quote, backslash or control character"):
        write_templates(tmp_path, url)
    assert list(tmp_path.iterdir()) == []


def test_write_templates_loads_what_it_wrote(tmp_path, monkeypatch):
    broken = config_module.TOML_TEMPLATE.replace("paraphrases = 3", 'paraphrases = "three"')
    monkeypatch.setattr(config_module, "TOML_TEMPLATE", broken)
    with pytest.raises(ConfigError, match=r"footnote\.toml.*paraphrases"):
        write_templates(tmp_path, "https://example.org")


# F1: [keys] holds environment variable names, checked as names; a value that is not one (most likely a key
# pasted where its variable's name belongs) is refused without being echoed anywhere.
PASTED = "sk-proj-PASTEDKEY-0123456789"


@pytest.mark.parametrize("name", ["OPENAI_API_KEY", "WORK_KEY_2", "_PRIVATE", "A" * 64])
def test_keys_take_environment_variable_names(name):
    assert KeysConfig(openai=name).openai == name


@pytest.mark.parametrize("value", [PASTED, "openai_api_key", "1KEY", "A" * 65, "", "MY KEY", "KEY-1"])
def test_keys_refuse_anything_but_a_variable_name_and_never_echo_it(value):
    with pytest.raises(ValidationError) as info:
        KeysConfig(openai=value)
    assert "keys.openai must be the name of an environment variable" in str(info.value)
    assert value not in str(info.value) or not value


def test_a_key_pasted_into_footnote_toml_is_a_config_error_that_never_shows_it(tmp_path):
    write(tmp_path, toml=TOML + f'\n[keys]\nopenai = "{PASTED}"\n')
    with pytest.raises(ConfigError) as info:
        load_config(tmp_path)
    message = str(info.value) + str(info.value.__cause__)
    assert "keys.openai" in message and PASTED not in message
