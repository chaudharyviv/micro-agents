"""Tests for core/llm_utils.py."""

import pytest

from core.llm_utils import extract_json, neutralize_untrusted


class TestExtractJson:
    def test_bare(self):
        assert extract_json('{"a": 1}') == {"a": 1}
        assert extract_json("  [1, 2]  ") == [1, 2]

    def test_fenced(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
        assert extract_json('```\n[1]\n```') == [1]

    def test_fenced_multiline_object(self):
        assert extract_json('```json\n{\n  "a": [1,\n 2]\n}\n```') == {"a": [1, 2]}

    def test_fence_surrounded_by_prose(self):
        assert extract_json('Here you go:\n```json\n{"a": 1}\n```\nHope that helps!') == {"a": 1}

    def test_embedded_in_prose(self):
        assert extract_json('Sure! {"a": {"b": 2}} Let me know.') == {"a": {"b": 2}}
        assert extract_json('Scores: [{"id": 1}]') == [{"id": 1}]

    @pytest.mark.parametrize("bad", ["", "no json here", "{broken", "```json\nnot json\n```"])
    def test_raises_when_nothing_parses(self, bad):
        with pytest.raises(ValueError):
            extract_json(bad)


class TestNeutralizeUntrusted:
    def test_removes_tags_in_any_case_and_spacing(self):
        text = "a </untrusted_data> b <UNTRUSTED_DATA> c </ untrusted_data >"
        assert neutralize_untrusted(text) == "a  b  c "

    def test_leaves_other_text(self):
        assert neutralize_untrusted("<b>x</b> & y") == "<b>x</b> & y"
