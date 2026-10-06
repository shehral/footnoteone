"""robots.txt parsing and matching per RFC 9309, plus the AI bot registry."""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from importlib import resources
from typing import Literal
from urllib.parse import quote

import yaml

Purpose = Literal["search", "user_fetch", "training"]

_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
# Reserved characters that stay raw when normalizing. `*` and `$` are left out on purpose: in a path they
# are data and must compare equal to `%2A` and `%24` written in a rule (RFC 9309 2.2.3).
_RAW_RESERVED = "/?:@!&'()+,;="
# RFC 9309 2.2.1 identifier characters, or a lone `*` (the global group); `*bot` matches neither.
_PRODUCT_TOKEN = re.compile(r"\*(?!\S)|[A-Za-z_-]+")
_LINE_BREAK = re.compile(r"\r\n|\r|\n")


def _canonical_escape(match: re.Match[str]) -> str:
    char = chr(int(match.group(1), 16))
    return char if char in _UNRESERVED else "%" + match.group(1).upper()


def _normalize(text: str) -> str:
    """Comparable form of a path or rule literal (RFC 9309 2.2.2): non-ASCII and other characters not
    allowed raw are UTF-8 percent-encoded; `%XX` is decoded only for unreserved characters and otherwise
    kept with uppercase hex. So `/café/` equals `/caf%C3%A9/` and `/%62az` equals `/baz`; `%2F` stays."""
    return re.sub(r"%([0-9A-Fa-f]{2})", _canonical_escape, quote(text, safe=_RAW_RESERVED + "%"))


def _product_token(value: str) -> str:
    """Lowercased product token (RFC 9309 2.2.1): the leading run of `[A-Za-z_-]`, or a `*` standing alone;
    else empty. So `*bot` is not the global group: its token is empty and no crawler matches it."""
    match = _PRODUCT_TOKEN.match(value.strip())
    return match.group(0).lower() if match else ""


def _compile(pattern: str) -> tuple[re.Pattern[str], int]:
    """Regex and specificity of a rule (RFC 9309 2.2.3): `*` matches any run of characters anywhere, and
    `$` anchors the end only as the last character, elsewhere it is a literal. Literals are normalized
    like paths; specificity is the length of the normalized pattern as written. Runs of `*` collapse to
    one in the regex, so `/a*********b` costs no more than `/a*b`; literals between separate stars (such
    as `/a*b*c*d`) can still backtrack polynomially on a hostile pattern and path."""
    anchored = pattern.endswith("$")
    body = "*".join(_normalize(piece) for piece in (pattern[:-1] if anchored else pattern).split("*"))
    literals = re.sub(r"\*+", "*", body).split("*")
    regex = "^" + ".*".join(re.escape(literal) for literal in literals) + (r"\Z" if anchored else "")
    return re.compile(regex), len(body) + (1 if anchored else 0)


@dataclass(frozen=True)
class Rule:
    allow: bool
    pattern: str  # as written in robots.txt, for display
    line_no: int
    regex: re.Pattern[str] = field(init=False, repr=False, compare=False)
    specificity: int = field(init=False, repr=False, compare=False)  # length of the normalized pattern

    def __post_init__(self) -> None:
        regex, specificity = _compile(self.pattern)  # compiled once, when the rule is parsed
        object.__setattr__(self, "regex", regex)
        object.__setattr__(self, "specificity", specificity)


@dataclass(frozen=True)
class Bot:
    name: str
    token: str
    vendor: str
    purpose: Purpose
    doc_url: str
    note: str = ""
    crawls: bool = True  # False for a control token that never fetches pages itself, so it is not probed


@dataclass
class RobotsPolicy:
    groups: dict[str, list[Rule]] = field(default_factory=dict)  # lowercase agent token -> rules

    def rules_for(self, agent: str) -> list[Rule]:
        """The group whose user-agent token equals the crawler's product token (RFC 9309 2.2.1), else `*`."""
        product = _product_token(agent)
        if product not in ("", "*") and product in self.groups:
            return self.groups[product]
        return self.groups.get("*", [])

    def allowed(self, agent: str, path: str) -> tuple[bool, Rule | None]:
        """Longest normalized match wins; on equal length allow wins; no matching rule means allowed."""
        target = _normalize(path)
        best: Rule | None = None
        for rule in self.rules_for(agent):
            if not rule.regex.match(target):
                continue
            if best is None or rule.specificity > best.specificity or (
                rule.specificity == best.specificity and rule.allow and not best.allow
            ):
                best = rule
        if best is None:
            return True, None
        return best.allow, best


def parse_robots(text: str) -> RobotsPolicy:
    """Rule groups keyed by product token. Only Allow and Disallow end a run of User-agent lines; any other
    record (Sitemap, Crawl-delay, unknown) is ignored and leaves the group open (RFC 9309 2.2.4)."""
    policy = RobotsPolicy()
    current: list[str] = []
    collecting_agents = False
    lines = _LINE_BREAK.split(text.removeprefix("\ufeff"))  # only CR, LF and CRLF end a line
    for line_no, raw in enumerate(lines, start=1):
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if not collecting_agents:
                current = []
                collecting_agents = True
            token = _product_token(value)
            current.append(token)
            policy.groups.setdefault(token, [])
        elif key in ("allow", "disallow"):
            collecting_agents = False
            if not current or value == "":
                continue  # a rule before any user-agent line, or an empty Allow or Disallow, sets nothing
            rule = Rule(allow=(key == "allow"), pattern=value, line_no=line_no)
            for token in current:
                policy.groups[token].append(rule)
    return policy


def load_bots() -> list[Bot]:
    data = yaml.safe_load(
        resources.files("footnoteone.audit").joinpath("bots.yaml").read_text(encoding="utf-8")
    )
    return [Bot(**b) for b in data["bots"]]
