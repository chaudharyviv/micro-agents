"""
Parse GitHub targets (repos, issues, users) out of text, for exact-match comparison.

Used to check that a target the model chose really came from the user's own words: the same
parser is applied to the user's text and to the model's tool input, and the parsed identifiers
must be equal. Identifiers are lower-cased since GitHub names are case-insensitive.
"""

import re
from typing import Optional

USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")

_HOST = r"(?:https?://)?(?:www\.)?github\.com/"
_OWNER = r"([A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38})"
_REPO = r"([A-Za-z0-9._-]+)"
# A repo name ends where the path, query, fragment or text ends; trailing dots are sentence
# punctuation ("...github.com/a/b."), not part of the name.
_REPO_URL = re.compile(rf"(?<![A-Za-z0-9./-]){_HOST}{_OWNER}/{_REPO}", re.IGNORECASE)
_ISSUE_URL = re.compile(rf"(?<![A-Za-z0-9./-]){_HOST}{_OWNER}/{_REPO}/issues/(\d+)(?![0-9A-Za-z])", re.IGNORECASE)
_USER_URL = re.compile(rf"(?<![A-Za-z0-9./-]){_HOST}{_OWNER}(?![A-Za-z0-9-])", re.IGNORECASE)
_AT_NAME = re.compile(rf"(?<![A-Za-z0-9._%+-])@{_OWNER}(?![A-Za-z0-9-])")
# "GitHub user torvalds", "GitHub profile: torvalds", or "username: torvalds". A keyword like "user" on
# its own is not enough, since it appears in ordinary prose ("the user is happy").
_KEYWORD_NAME = re.compile(
    rf"\bgithub[ \t]+(?:user(?:name)?|developer|dev|profile|account|handle|contributor|maintainer|engineer)\b[ \t]*[:\-]?[ \t]*@?{_OWNER}(?![A-Za-z0-9-])"
    rf"|\b(?:user(?:name)?|handle)[ \t]*:[ \t]*@?{_OWNER}(?![A-Za-z0-9-])",
    re.IGNORECASE,
)

# Words that can follow "user"/"developer"/... in ordinary prose without being a username.
_STOPWORDS = frozenset(
    "a an the is are was be been of on in at to for from with by and or as if it its this that these those "
    "who whom whose what which when where how why should would could can may might will shall do does did "
    "has have had not no yes any all some each every more most other another such own same than then there "
    "their them they we you your our my me he she him her his hers i about after before again against "
    "account profile github page link url repo repos repository repositories activity history stats "
    "work code projects".split()
)

_RESERVED_OWNERS = frozenset(
    "about apps features marketplace orgs organizations sponsors settings topics trending explore "
    "login join pricing enterprise customer-stories security collections events readme".split()
)


def _clean_repo(name: str) -> str:
    name = name.rstrip(".")
    return name[:-4] if name.lower().endswith(".git") else name


def parse_repo_ref(text: str) -> Optional[tuple[str, str]]:
    """(owner, repo) for text that is a GitHub repo URL (optionally with a path/query), else None."""
    m = _REPO_URL.match(text.strip())
    if not m:
        return None
    return m.group(1).lower(), _clean_repo(m.group(2)).lower()


def extract_repo_refs(text: str) -> set[tuple[str, str]]:
    """Every (owner, repo) mentioned as a GitHub URL in text (including via issue or tree URLs)."""
    return {
        (m.group(1).lower(), _clean_repo(m.group(2)).lower())
        for m in _REPO_URL.finditer(text)
        if _clean_repo(m.group(2)) and m.group(1).lower() not in _RESERVED_OWNERS
    }


def parse_issue_ref(text: str) -> Optional[tuple[str, str, int]]:
    """(owner, repo, number) for text that is a GitHub issue URL, else None."""
    m = _ISSUE_URL.match(text.strip())
    if not m:
        return None
    return m.group(1).lower(), _clean_repo(m.group(2)).lower(), int(m.group(3))


def extract_issue_refs(text: str) -> set[tuple[str, str, int]]:
    return {(m.group(1).lower(), _clean_repo(m.group(2)).lower(), int(m.group(3))) for m in _ISSUE_URL.finditer(text)}


def parse_username(text: str) -> Optional[str]:
    """A GitHub username from '@name', 'name', or a github.com/name URL; else None."""
    text = text.strip()
    m = _USER_URL.match(text)
    candidate = m.group(1) if m else text.lstrip("@")
    return candidate.lower() if USERNAME_RE.match(candidate) else None


def extract_usernames(text: str) -> set[str]:
    """
    Usernames the text clearly names: '@name', 'github.com/name' (or the owner in a repo URL),
    or a name right after 'GitHub user' / 'GitHub profile' (or 'username:'). A bare word is not enough,
    so ordinary prose can't ground a target. Also accepts the whole text being a single username.
    """
    names = {m.group(1) for m in _AT_NAME.finditer(text)}
    names |= {m.group(1) for m in _USER_URL.finditer(text)}
    for m in _KEYWORD_NAME.finditer(text):
        name = m.group(1) or m.group(2)
        if name.lower() not in _STOPWORDS:
            names.add(name)
    whole = text.strip().lstrip("@")
    if USERNAME_RE.match(whole):
        names.add(whole)
    return {n.lower() for n in names if n.lower() not in _RESERVED_OWNERS}
