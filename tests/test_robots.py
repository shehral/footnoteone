from footnoteone.audit.robots import load_bots, parse_robots

ROBOTS = """
# comment
User-agent: *
Disallow: /private/
Allow: /private/public-note

User-agent: GPTBot
User-agent: ClaudeBot
Disallow: /

User-agent: OAI-SearchBot
Allow: /

User-agent: Googlebot
Disallow: /*.pdf$
Disallow: /tmp*
"""


def test_specific_group_wins_over_star_even_when_star_comes_first():
    policy = parse_robots(ROBOTS)
    allowed, rule = policy.allowed("GPTBot", "/posts/one")
    assert allowed is False and rule is not None and rule.pattern == "/" and rule.line_no == 9


def test_two_user_agent_lines_share_one_group():
    policy = parse_robots(ROBOTS)
    assert policy.allowed("ClaudeBot", "/anything")[0] is False


def test_star_group_applies_to_unknown_agents_with_longest_match_and_allow_tiebreak():
    policy = parse_robots(ROBOTS)
    assert policy.allowed("PerplexityBot", "/private/secret")[0] is False
    assert policy.allowed("PerplexityBot", "/private/public-note")[0] is True
    assert policy.allowed("PerplexityBot", "/open")[0] is True


def test_agent_match_is_case_insensitive_on_the_product_token():
    policy = parse_robots(ROBOTS)
    assert policy.allowed("oai-searchbot/1.0", "/private/secret")[0] is True


def test_wildcards_and_end_anchor():
    policy = parse_robots(ROBOTS)
    assert policy.allowed("Googlebot", "/docs/file.pdf")[0] is False
    assert policy.allowed("Googlebot", "/docs/file.pdf?x=1")[0] is True
    assert policy.allowed("Googlebot", "/tmp/anything")[0] is False
    assert policy.allowed("Googlebot", "/tmpest")[0] is False


def test_empty_robots_allows_everything_with_no_rule():
    policy = parse_robots("")
    assert policy.allowed("GPTBot", "/x") == (True, None)


def test_percent_encoding_is_normalized():
    policy = parse_robots("User-agent: *\nDisallow: /caf%C3%A9/\n")
    assert policy.allowed("x", "/café/menu")[0] is False


def test_bot_registry_has_the_known_agents_with_purposes():
    bots = {b.token: b for b in load_bots()}
    for token in ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "Perplexity-User",
                  "ClaudeBot", "Claude-SearchBot", "Claude-User", "Googlebot", "Google-Extended", "Bingbot",
                  "Applebot-Extended", "Meta-ExternalAgent"]:
        assert token in bots, token
    assert bots["GPTBot"].purpose == "training"
    assert bots["OAI-SearchBot"].purpose == "search"
    assert bots["ChatGPT-User"].purpose == "user_fetch"
    assert bots["Google-Extended"].purpose == "training"
    assert bots["ChatGPT-User"].note and bots["Perplexity-User"].note
    assert bots["Applebot-Extended"].purpose == "training"
    assert bots["Meta-ExternalAgent"].purpose == "training"


def test_registry_marks_the_two_control_tokens_as_not_crawling():
    assert {b.token for b in load_bots() if not b.crawls} == {"Google-Extended", "Applebot-Extended"}


def test_reserved_percent_escapes_stay_encoded_and_unreserved_ones_are_decoded():
    policy = parse_robots("User-agent: *\nDisallow: /a%2Fb\nDisallow: /foo/bar/%62%61%7A\n")
    assert policy.allowed("x", "/a/b") == (True, None)
    assert policy.allowed("x", "/a%2fb")[0] is False
    assert policy.allowed("x", "/foo/bar/baz")[0] is False


def test_percent_encoded_star_is_a_literal_not_a_wildcard():
    policy = parse_robots("User-agent: *\nDisallow: /path/file-with-a-%2A.html\n")
    assert policy.allowed("x", "/path/file-with-a-*.html")[0] is False
    assert policy.allowed("x", "/path/file-with-a-x.html")[0] is True


def test_dollar_is_an_end_anchor_only_as_the_last_character():
    policy = parse_robots("User-agent: *\nDisallow: /a$b\n")
    assert policy.allowed("x", "/a$b/c")[0] is False
    assert policy.allowed("x", "/a%24b/c")[0] is False
    assert policy.allowed("x", "/ab")[0] is True


def test_longest_match_measures_the_normalized_pattern():
    policy = parse_robots("User-agent: *\nDisallow: /caf%C3%A9\nAllow: /café\n")
    allowed, rule = policy.allowed("x", "/café")
    assert allowed is True and rule is not None and rule.pattern == "/café" and rule.line_no == 3


def test_google_extended_note_claims_only_what_its_doc_page_supports():
    note = {b.token: b for b in load_bots()}["Google-Extended"].note
    assert "Search inclusion" in note
    assert "AI Overviews" not in note and "AI Mode" not in note


def test_sitemap_line_does_not_end_the_user_agent_list():
    policy = parse_robots(
        "User-agent: BarBot\nSitemap: https://foo.bar/sitemap\nUser-agent: *\nDisallow: /\n"
    )
    assert policy.allowed("BarBot", "/x")[0] is False


def test_unknown_line_does_not_end_the_user_agent_list():
    policy = parse_robots("User-agent: BarBot\nInvalid-Unknown-Line: unknown\nUser-agent: *\nDisallow: /\n")
    assert policy.allowed("FooBot", "/x")[0] is False
    assert policy.allowed("BarBot", "/x")[0] is False


def test_crawl_delay_line_does_not_end_the_user_agent_list():
    policy = parse_robots("User-agent: GPTBot\nCrawl-delay: 10\n\nUser-agent: *\nDisallow: /private/\n")
    assert policy.allowed("GPTBot", "/private/x")[0] is False


def test_leading_byte_order_mark_is_ignored():
    policy = parse_robots("\ufeffUser-agent: *\nDisallow: /\n")
    assert policy.allowed("GPTBot", "/x")[0] is False


def test_product_token_is_the_leading_run_of_token_characters():
    policy = parse_robots(
        "User-agent: ClaudeBot, GPTBot\nDisallow: /a\n\nUser-agent: Googlebot/2.1\nDisallow: /b\n"
    )
    assert policy.allowed("ClaudeBot", "/a")[0] is False
    assert policy.allowed("GPTBot", "/a") == (True, None)
    assert policy.allowed("Googlebot", "/b")[0] is False


def test_a_user_agent_starting_with_a_star_is_its_own_group_not_the_global_one():
    policy = parse_robots("User-agent: *\nDisallow: /x\n\nUser-agent: *bot\nDisallow: /\n")
    assert policy.allowed("GPTBot", "/x")[0] is False  # the global group
    assert policy.allowed("GPTBot", "/y") == (True, None)  # `*bot` did not join it
    assert policy.allowed("bot", "/y") == (True, None)  # and names no crawler


def test_group_tokens_match_exactly_not_by_prefix():
    policy = parse_robots("User-agent: Claude\nDisallow: /\n")
    assert policy.allowed("ClaudeBot", "/x") == (True, None)


def test_runs_of_stars_are_collapsed_so_matching_stays_fast():
    policy = parse_robots("User-agent: *\nDisallow: /a*********b\n")
    assert policy.allowed("x", "/a" + "c" * 29) == (True, None)
    assert policy.allowed("x", "/a" + "c" * 28 + "b")[0] is False


def test_empty_allow_sets_no_rule():
    policy = parse_robots("User-agent: *\nAllow:\n")
    assert policy.allowed("x", "/x") == (True, None)


def test_only_cr_and_lf_break_lines_so_a_form_feed_keeps_line_numbers():
    policy = parse_robots("User-agent: *\n# section\x0c two\nDisallow: /x\n")
    allowed, rule = policy.allowed("x", "/x")
    assert allowed is False and rule is not None and rule.line_no == 3


def test_user_agent_lines_separated_by_blank_lines_share_a_group():
    policy = parse_robots("User-agent: FooBot\n\nUser-agent: BarBot\nDisallow: /\n")
    assert policy.allowed("FooBot", "/x")[0] is False
    assert policy.allowed("BarBot", "/x")[0] is False


def test_repeated_groups_for_one_agent_merge():
    policy = parse_robots(
        "User-agent: FooBot\nDisallow: /a\n\n"
        "User-agent: BarBot\nDisallow: /\n\n"
        "User-agent: FooBot\nDisallow: /b\n"
    )
    assert policy.allowed("FooBot", "/a")[0] is False
    assert policy.allowed("FooBot", "/b")[0] is False
    assert policy.allowed("FooBot", "/c") == (True, None)
