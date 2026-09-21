"""Tests for core/targets.py."""

import pytest

from core.targets import (
    extract_issue_refs,
    extract_repo_refs,
    extract_usernames,
    parse_issue_ref,
    parse_repo_ref,
    parse_username,
)


class TestRepoRefs:
    @pytest.mark.parametrize(
        "text",
        [
            "https://github.com/Owner/Repo",
            "http://github.com/owner/repo/",
            "https://github.com/owner/repo.git",
            "https://www.github.com/owner/repo",
            "github.com/owner/repo",
            "https://github.com/owner/repo/tree/main/src",
            "https://github.com/owner/repo?tab=readme#top",
            "  https://github.com/owner/repo  ",
            "https://github.com/owner/repo/issues/5",
        ],
    )
    def test_parse_normalizes_to_the_same_ref(self, text):
        assert parse_repo_ref(text) == ("owner", "repo")

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "not a url",
            "https://gitlab.com/owner/repo",
            "https://evil.com/github.com/owner/repo",
            "https://notgithub.com/owner/repo",
            "https://github.com/owner",
            "owner/repo",
        ],
    )
    def test_parse_rejects_non_repo_urls(self, text):
        assert parse_repo_ref(text) is None

    def test_similar_names_are_different_refs(self):
        assert parse_repo_ref("https://github.com/a/repo-evil") != parse_repo_ref("https://github.com/a/repo")
        assert parse_repo_ref("https://github.com/a/repo2") != parse_repo_ref("https://github.com/a/repo")

    def test_extract_finds_all_and_handles_prose_punctuation(self):
        text = "Compare https://github.com/a/b, then github.com/c/d.git. Also see (https://github.com/e/f)."
        assert extract_repo_refs(text) == {("a", "b"), ("c", "d"), ("e", "f")}

    def test_extract_ignores_lookalike_hosts(self):
        assert extract_repo_refs("visit https://evilgithub.com/a/b and https://x.com/github.com/c/d") == set()

    def test_extract_ignores_github_site_pages(self):
        assert extract_repo_refs("https://github.com/features/copilot https://github.com/orgs/x") == set()

    def test_issue_url_also_grounds_its_repo(self):
        assert ("a", "b") in extract_repo_refs("look at https://github.com/a/b/issues/7")


class TestIssueRefs:
    def test_parse(self):
        assert parse_issue_ref("https://github.com/O/R/issues/12") == ("o", "r", 12)
        assert parse_issue_ref("https://github.com/o/r/issues/12#issuecomment-1") == ("o", "r", 12)

    @pytest.mark.parametrize("text", ["https://github.com/o/r", "https://github.com/o/r/pull/3", "https://github.com/o/r/issues/x"])
    def test_parse_rejects(self, text):
        assert parse_issue_ref(text) is None

    def test_number_must_match_exactly(self):
        assert parse_issue_ref("https://github.com/o/r/issues/12") != parse_issue_ref("https://github.com/o/r/issues/123")
        assert extract_issue_refs("see github.com/o/r/issues/123") == {("o", "r", 123)}

    def test_extract(self):
        text = "fix https://github.com/o/r/issues/1 and https://github.com/o/r/issues/2."
        assert extract_issue_refs(text) == {("o", "r", 1), ("o", "r", 2)}


class TestUsernames:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("What should GitHub user torvalds work on to advance their career?", {"torvalds"}),
            ("career advice for @Guido", {"guido"}),
            ("look at github.com/gvanrossum", {"gvanrossum"}),
            ("username: octo-cat please", {"octo-cat"}),
            ("GitHub user Linus has repos", {"linus"}),
            ("GitHub profile: Linus", {"linus"}),
            ("username: octo", {"octo"}),
            ("torvalds", {"torvalds"}),
            ("@torvalds", {"torvalds"}),
            ("owner of https://github.com/anthropics/sdk", {"anthropics"}),
        ],
    )
    def test_clearly_named(self, text, expected):
        assert extract_usernames(text) >= expected

    @pytest.mark.parametrize(
        "text",
        [
            "the user is happy about it",
            "any user can do this",
            "Review my account",
            "what is a developer to do",
            "advice for the profile page",
            "the developer Linus has repos",
            "look at the GitHub profile page",
            "GitHub user is happy",
            "Suggest blog post ideas about Rust programming",
            "email me at bob@example.com",
        ],
    )
    def test_ordinary_prose_names_nobody(self, text):
        assert extract_usernames(text) == set()

    def test_parse_username(self):
        assert parse_username("torvalds") == "torvalds"
        assert parse_username("@Torvalds") == "torvalds"
        assert parse_username("https://github.com/Torvalds") == "torvalds"
        assert parse_username("https://github.com/torvalds/linux") == "torvalds"
        assert parse_username("not a name") is None
        assert parse_username("-bad") is None
        assert parse_username("") is None
