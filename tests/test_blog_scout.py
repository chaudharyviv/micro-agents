"""Tests for Blog Idea Scout agent."""

import json
import pytest
from unittest.mock import MagicMock

from agents.blog_scout.logic import scout_blog_ideas, _parse_ideas


class TestScoutBlogIdeas:
    """Tests for scout_blog_ideas function."""

    def test_scout_with_topic(self, mocker):
        """Test scout_blog_ideas with a specific topic."""
        # Mock web_search
        mock_search_results = [
            {
                "title": "Python 3.13 Released",
                "url": "https://example.com/python313",
                "snippet": "New features in Python 3.13",
            },
            {
                "title": "Async Programming Guide",
                "url": "https://example.com/async",
                "snippet": "Mastering async/await patterns",
            },
        ]
        mocker.patch(
            "agents.blog_scout.logic.web_search", return_value=mock_search_results
        )

        # Mock LLM response with valid blog ideas
        llm_response = json.dumps(
            [
                {
                    "title": "What's New in Python 3.13",
                    "pitch": "Explore the new features coming to Python 3.13",
                    "source_url": "https://example.com/python313",
                    "source_title": "Python 3.13 Released",
                },
                {
                    "title": "Async/Await Patterns in Python",
                    "pitch": "Deep dive into async programming with Python",
                    "source_url": "https://example.com/async",
                    "source_title": "Async Programming Guide",
                },
            ]
        )
        mocker.patch("agents.blog_scout.logic.call_llm", return_value=llm_response)

        result = scout_blog_ideas(topic="Python programming")

        assert len(result) == 2
        assert result[0]["title"] == "What's New in Python 3.13"
        assert result[0]["source_url"] == "https://example.com/python313"
        assert result[1]["title"] == "Async/Await Patterns in Python"

    def test_scout_with_default_topics(self, mocker):
        """Test scout_blog_ideas with default topics (topic=None)."""
        mock_search_results = [
            {
                "title": "GitHub Trending Repos",
                "url": "https://example.com/trending",
                "snippet": "This week's trending repositories",
            },
        ]
        mocker.patch(
            "agents.blog_scout.logic.web_search", return_value=mock_search_results
        )

        llm_response = json.dumps(
            [
                {
                    "title": "Must-Watch GitHub Repos",
                    "pitch": "Check out this week's hottest repositories",
                    "source_url": "https://example.com/trending",
                    "source_title": "GitHub Trending Repos",
                },
            ]
        )
        mocker.patch("agents.blog_scout.logic.call_llm", return_value=llm_response)

        result = scout_blog_ideas(topic=None)

        assert len(result) == 1
        assert result[0]["title"] == "Must-Watch GitHub Repos"

    def test_scout_parse_failure_with_retry(self, mocker):
        """Test scout_blog_ideas retries on parse failure."""
        mock_search_results = [
            {
                "title": "Test Article",
                "url": "https://example.com/test",
                "snippet": "Test content",
            },
        ]
        mocker.patch(
            "agents.blog_scout.logic.web_search", return_value=mock_search_results
        )

        # First call returns invalid JSON, second returns valid JSON
        invalid_response = "{not valid json}"
        valid_response = json.dumps(
            [
                {
                    "title": "Test Idea",
                    "pitch": "A test idea",
                    "source_url": "https://example.com/test",
                    "source_title": "Test Article",
                },
            ]
        )

        mock_llm = mocker.patch(
            "agents.blog_scout.logic.call_llm",
            side_effect=[invalid_response, valid_response],
        )

        result = scout_blog_ideas(topic="test")

        # Should have retried
        assert mock_llm.call_count == 2
        assert len(result) == 1
        assert result[0]["title"] == "Test Idea"

    def test_scout_invalid_source_url_filtered(self, mocker):
        """Test that ideas with invalid source URLs are filtered out."""
        mock_search_results = [
            {
                "title": "Real Article",
                "url": "https://example.com/real",
                "snippet": "Real content",
            },
        ]
        mocker.patch(
            "agents.blog_scout.logic.web_search", return_value=mock_search_results
        )

        # LLM returns an idea with a fabricated URL
        llm_response = json.dumps(
            [
                {
                    "title": "Real Idea",
                    "pitch": "A real idea",
                    "source_url": "https://example.com/real",
                    "source_title": "Real Article",
                },
                {
                    "title": "Fabricated Idea",
                    "pitch": "This is made up",
                    "source_url": "https://fabricated.com/fake",
                    "source_title": "Made up article",
                },
            ]
        )
        mocker.patch("agents.blog_scout.logic.call_llm", return_value=llm_response)

        result = scout_blog_ideas(topic="test")

        # Only the real idea should be in results
        assert len(result) == 1
        assert result[0]["title"] == "Real Idea"

    def test_scout_no_search_results_returns_fallback(self, mocker):
        """Test fallback when no search results are found."""
        mocker.patch("agents.blog_scout.logic.web_search", return_value=[])
        mocker.patch("agents.blog_scout.logic.call_llm")

        result = scout_blog_ideas(topic="test")

        # Should return fallback error dict
        assert len(result) == 1
        assert "Unable to generate ideas" in result[0]["title"]

    def test_scout_llm_failure_returns_fallback(self, mocker):
        """Test fallback when LLM call fails."""
        mock_search_results = [
            {"title": "Article", "url": "https://example.com/test", "snippet": "Content"}
        ]
        mocker.patch(
            "agents.blog_scout.logic.web_search", return_value=mock_search_results
        )

        mocker.patch(
            "agents.blog_scout.logic.call_llm",
            side_effect=Exception("LLM service down"),
        )

        result = scout_blog_ideas(topic="test")

        # Should return fallback with error message
        assert len(result) == 1
        assert "Unable to generate ideas" in result[0]["title"]
        assert "LLM service" in result[0]["pitch"]

    def test_scout_all_ideas_filtered_returns_fallback(self, mocker):
        """Test fallback when all ideas are filtered due to invalid URLs."""
        mock_search_results = [
            {"title": "Article", "url": "https://example.com/real", "snippet": "Content"}
        ]
        mocker.patch(
            "agents.blog_scout.logic.web_search", return_value=mock_search_results
        )

        # LLM returns ideas with fabricated URLs
        llm_response = json.dumps(
            [
                {
                    "title": "Fake Idea",
                    "pitch": "Made up",
                    "source_url": "https://fabricated.com/1",
                    "source_title": "Fake",
                },
            ]
        )
        mocker.patch("agents.blog_scout.logic.call_llm", return_value=llm_response)

        result = scout_blog_ideas(topic="test")

        # Should return fallback
        assert len(result) == 1
        assert "Unable to generate ideas" in result[0]["title"]


class TestParseIdeas:
    """Tests for _parse_ideas function."""

    def test_parse_valid_ideas(self):
        """Test parsing valid JSON ideas."""
        response = json.dumps(
            [
                {
                    "title": "Idea 1",
                    "pitch": "Pitch 1",
                    "source_url": "https://example.com/1",
                    "source_title": "Source 1",
                },
                {
                    "title": "Idea 2",
                    "pitch": "Pitch 2",
                    "source_url": "https://example.com/2",
                    "source_title": "Source 2",
                },
            ]
        )
        urls = {"https://example.com/1", "https://example.com/2"}

        ideas = _parse_ideas(response, urls)

        assert len(ideas) == 2
        assert ideas[0]["title"] == "Idea 1"
        assert ideas[1]["title"] == "Idea 2"

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON."""
        response = "{not valid}"
        urls = set()

        ideas = _parse_ideas(response, urls)

        assert ideas is None

    def test_parse_missing_fields(self):
        """Test filtering ideas with missing fields."""
        response = json.dumps(
            [
                {
                    "title": "Complete Idea",
                    "pitch": "Pitch",
                    "source_url": "https://example.com/1",
                    "source_title": "Source",
                },
                {
                    "title": "Incomplete Idea",
                    "pitch": "Missing source",
                },
            ]
        )
        urls = {"https://example.com/1"}

        ideas = _parse_ideas(response, urls)

        # Only complete idea should be returned
        assert len(ideas) == 1
        assert ideas[0]["title"] == "Complete Idea"

    def test_parse_filters_invalid_urls(self):
        """Test that ideas with invalid source URLs are filtered."""
        response = json.dumps(
            [
                {
                    "title": "Valid Idea",
                    "pitch": "Pitch",
                    "source_url": "https://example.com/valid",
                    "source_title": "Source",
                },
                {
                    "title": "Invalid URL Idea",
                    "pitch": "Pitch",
                    "source_url": "https://fabricated.com/fake",
                    "source_title": "Fake",
                },
            ]
        )
        urls = {"https://example.com/valid"}

        ideas = _parse_ideas(response, urls)

        assert len(ideas) == 1
        assert ideas[0]["title"] == "Valid Idea"

    def test_parse_not_array(self):
        """Test parsing response that's not an array."""
        response = json.dumps(
            {
                "title": "Not an array",
                "pitch": "This is a single object",
            }
        )
        urls = set()

        ideas = _parse_ideas(response, urls)

        assert ideas is None

    def test_parse_empty_array(self):
        """Test parsing empty array."""
        response = json.dumps([])
        urls = set()

        ideas = _parse_ideas(response, urls)

        assert ideas is None
