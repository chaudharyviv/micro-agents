"""Structured outputs: schemas the API will accept, responses they describe, and call_llm's handling of them."""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import jsonschema
import pytest

from core.errors import BudgetExceededError, LLMOutputError, LLMUnavailableError
from core.llm import call_llm
from core.llm_utils import INT, NULLABLE_STR, STR, arr, enum, obj

from agents.blog_scout.prompts import RESPONSE_SCHEMA as BLOG
from agents.cve_impact.prompts import RESPONSE_SCHEMA as CVE
from agents.do_i_care.prompts import ANALYSIS_SCHEMA as CARE_ANALYSIS, SCORING_SCHEMA as CARE_SCORING
from agents.opportunity_scout.prompts import RESPONSE_SCHEMA as OPPORTUNITY
from agents.repo_onboarding.prompts import RESPONSE_SCHEMA as ONBOARDING
from agents.security_audit.prompts import RESPONSE_SCHEMA as AUDIT

SCHEMAS = {
    "blog": BLOG, "cve": CVE, "care_scoring": CARE_SCORING, "care_analysis": CARE_ANALYSIS,
    "opportunity": OPPORTUNITY, "onboarding": ONBOARDING, "audit": AUDIT,
}

# Keywords OpenAI's strict mode rejects (or that we avoid because support varies by model).
UNSUPPORTED = {"minLength", "maxLength", "pattern", "format", "minItems", "maxItems", "minimum", "maximum",
               "allOf", "not", "if", "then", "else", "dependentRequired", "patternProperties", "oneOf"}
MAX_DEPTH = 5


def check_strict(schema, depth=1, path="root"):
    """Assert `schema` satisfies OpenAI strict structured-output rules; return the maximum nesting depth."""
    assert not (UNSUPPORTED & set(schema)), f"{path}: unsupported keywords {UNSUPPORTED & set(schema)}"
    deepest = depth
    if schema.get("type") == "object":
        props = schema["properties"]
        assert schema.get("additionalProperties") is False, f"{path}: additionalProperties must be false"
        assert list(schema["required"]) == list(props), f"{path}: every property must be required"
        for name, sub in props.items():
            deepest = max(deepest, check_strict(sub, depth + 1, f"{path}.{name}"))
    elif schema.get("type") == "array":
        deepest = max(deepest, check_strict(schema["items"], depth + 1, f"{path}[]"))
    return deepest


class TestSchemasAreValidStrictSchemas:
    @pytest.mark.parametrize("name", list(SCHEMAS))
    def test_strict_rules(self, name):
        schema = SCHEMAS[name]
        assert schema["type"] == "object"  # a strict root must be an object
        assert check_strict(schema) <= MAX_DEPTH

    @pytest.mark.parametrize("name", list(SCHEMAS))
    def test_is_a_valid_json_schema(self, name):
        jsonschema.Draft202012Validator.check_schema(SCHEMAS[name])

    def test_builders(self):
        assert obj(a=STR, b=INT) == {
            "type": "object", "properties": {"a": STR, "b": INT}, "required": ["a", "b"], "additionalProperties": False,
        }
        assert arr(STR) == {"type": "array", "items": STR}
        assert enum("x", "y") == {"type": "string", "enum": ["x", "y"]}
        assert NULLABLE_STR["type"] == ["string", "null"]


SAMPLES = {
    "blog": {"ideas": [{"title": "T", "pitch": "P", "source_url": "https://e.com/a", "source_title": "S"}]},
    "cve": {"summary": "s", "impacts": [{"cve_id": "CVE-2024-1", "impact": "i"}], "common_themes": ["t"], "recommendations": ["r"]},
    "care_scoring": {"scores": [{"id": 1, "score": 7, "reason": "r"}]},
    "care_analysis": {"items": [{"id": 1, "why_it_matters": "w", "suggested_action": "a"}]},
    "opportunity": {
        "current_skills": ["Python"],
        "skill_gaps": [{"skill": "Go", "reason": "r", "source_url": None}, {"skill": "Rust", "reason": "r", "source_url": "https://e.com"}],
        "opportunities": [{"role": "SRE", "fit": "f"}],
        "project_idea": {"title": "t", "description": "d", "skills_built": ["Go"]},
    },
    "onboarding": {
        "project_name": "p", "overview": "o", "tech_stack": ["py"], "setup_steps": ["s"],
        "project_structure": [{"path": "src/", "description": "code"}], "key_concepts": ["k"],
        "development_workflow": {"branching_strategy": "b", "testing": "t", "building": "bu", "deploying": ""},
        "common_tasks": {"run_tests": "pytest", "start_dev_server": "", "build": "", "lint": ""}, "resources": ["r"],
    },
    "audit": {
        "repo_url": "u", "audit_summary": "s", "overall_risk": "High",
        "critical_issues": [{"issue": "i", "impact": "m", "remediation": "r"}],
        "safe_contribution_practices": ["p"], "onboarding_security_checklist": ["c"],
        "remediation_roadmap": [{"phase": 1, "timeframe": "now", "actions": ["a"]}], "security_recommendations": ["r"],
    },
}


class TestSamplesMatchSchemasAndParsers:
    @pytest.mark.parametrize("name", list(SAMPLES))
    def test_sample_validates(self, name):
        jsonschema.validate(SAMPLES[name], SCHEMAS[name])

    def test_audit_risk_is_an_enum(self):
        bad = {**SAMPLES["audit"], "overall_risk": "Apocalyptic"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, AUDIT)

    def test_extra_or_missing_fields_are_invalid(self):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**SAMPLES["blog"], "extra": 1}, BLOG)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({}, BLOG)

    def test_blog_parser_reads_the_wrapped_object(self):
        from agents.blog_scout.logic import _parse_ideas

        ideas = _parse_ideas(json.dumps(SAMPLES["blog"]), {"https://e.com/a"})
        assert ideas and ideas[0]["title"] == "T"

    def test_onboarding_parser_turns_structure_back_into_a_dict(self):
        from agents.repo_onboarding.logic import _parse_guide

        guide = _parse_guide(json.dumps(SAMPLES["onboarding"]), "u")
        assert guide["project_structure"] == {"src/": "code"}
        assert guide["development_workflow"]["testing"] == "t"

    def test_onboarding_parser_still_accepts_a_dict_structure(self):
        from agents.repo_onboarding.logic import _parse_guide

        sample = {**SAMPLES["onboarding"], "project_structure": {"a/": "b"}}
        assert _parse_guide(json.dumps(sample), "u")["project_structure"] == {"a/": "b"}

    def test_audit_parser(self):
        from agents.security_audit.logic import _parse_audit

        assert _parse_audit(json.dumps(SAMPLES["audit"]), "u")["overall_risk"] == "High"

    def test_cve_parser_turns_impact_list_into_a_dict(self):
        from agents.cve_impact.logic import _parse_analysis

        parsed = _parse_analysis(json.dumps(SAMPLES["cve"]), "u")
        assert parsed["impacts"] == {"CVE-2024-1": "i"}

    def test_care_parsers_read_wrapped_objects(self):
        from agents.do_i_care.logic import _parse_analysis, _parse_scores

        assert _parse_scores(json.dumps(SAMPLES["care_scoring"]), 1) == {1: (7, "r")}
        assert _parse_analysis(json.dumps(SAMPLES["care_analysis"]), {1})[1]["why_it_matters"] == "w"

    def test_opportunity_parser_keeps_null_and_real_sources(self):
        from agents.opportunity_scout.logic import _parse_analysis

        parsed = _parse_analysis(json.dumps(SAMPLES["opportunity"]), {"https://e.com"})
        assert [g["source_url"] for g in parsed["skill_gaps"]] == [None, "https://e.com"]


# ---- call_llm ----------------------------------------------------------------------------------------

def completion(content='{"a": "b"}', finish="stop", refusal=None):
    msg = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)], usage=SimpleNamespace(total_tokens=10))


@pytest.fixture
def openai(mocker, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = MagicMock()
    mocker.patch("core.llm.OpenAI", return_value=client)
    return client.chat.completions.create


SCHEMA = obj(a=STR)


class TestCallLlmStructured:
    def test_sends_a_strict_json_schema(self, openai):
        openai.return_value = completion()

        result = call_llm("p", system="s", schema=SCHEMA, schema_name="thing")

        assert result == '{"a": "b"}'
        kwargs = openai.call_args.kwargs
        assert kwargs["response_format"] == {
            "type": "json_schema",
            "json_schema": {"name": "thing", "strict": True, "schema": SCHEMA},
        }
        assert kwargs["temperature"] == 0.2

    def test_free_text_calls_are_unchanged(self, openai):
        openai.return_value = completion(content="hello", finish="stop")

        assert call_llm("p") == "hello"
        assert "response_format" not in openai.call_args.kwargs
        assert openai.call_args.kwargs["temperature"] == 0.7

    def test_explicit_temperature_wins(self, openai):
        openai.return_value = completion()
        call_llm("p", schema=SCHEMA, temperature=0.9)
        assert openai.call_args.kwargs["temperature"] == 0.9

    def test_refusal_is_not_retried_and_says_so(self, openai):
        openai.return_value = completion(content=None, refusal="I can't help with that")

        with pytest.raises(LLMOutputError, match="declined"):
            call_llm("p", schema=SCHEMA)

        assert openai.call_count == 1

    def test_truncated_output_is_not_retried_and_never_returned_as_json(self, openai):
        openai.return_value = completion(content='{"a": "unfinis', finish="length")

        with pytest.raises(LLMOutputError, match="cut off"):
            call_llm("p", schema=SCHEMA)

        assert openai.call_count == 1

    def test_empty_content_is_an_error(self, openai):
        openai.return_value = completion(content="")
        with pytest.raises(LLMOutputError):
            call_llm("p", schema=SCHEMA)

    def test_output_errors_are_llm_unavailable_errors_so_agents_surface_them(self):
        assert issubclass(LLMOutputError, LLMUnavailableError)

    def test_a_transient_failure_is_still_retried(self, openai):
        openai.side_effect = [Exception("boom"), completion()]

        assert call_llm("p", schema=SCHEMA) == '{"a": "b"}'
        assert openai.call_count == 2

    def test_budget_errors_still_propagate(self, openai):
        from core.budget import Budget, BudgetLimits, set_budget

        set_budget(Budget(BudgetLimits(daily_calls=1), persist_path=None))
        openai.return_value = completion()
        call_llm("p", schema=SCHEMA)

        with pytest.raises(BudgetExceededError):
            call_llm("p", schema=SCHEMA)


# ---- every agent asks for its schema -----------------------------------------------------------------

class TestAgentsRequestStructuredOutput:
    def test_blog_scout(self, mocker):
        mocker.patch("agents.blog_scout.logic.web_search", return_value=[{"title": "t", "url": "https://e.com/a", "snippet": "s"}])
        llm = mocker.patch("agents.blog_scout.logic.call_llm", return_value=json.dumps(SAMPLES["blog"]))
        from agents.blog_scout.logic import scout_blog_ideas

        scout_blog_ideas("x")

        assert llm.call_args.kwargs["schema"] is BLOG

    def test_repo_onboarding(self, mocker):
        mocker.patch("agents.repo_onboarding.logic.fetch_repo", return_value={"name": "n", "readme": "r"})
        llm = mocker.patch("agents.repo_onboarding.logic.call_llm", return_value=json.dumps(SAMPLES["onboarding"]))
        from agents.repo_onboarding.logic import generate_onboarding_guide

        assert "error_message" not in generate_onboarding_guide("https://github.com/o/r")
        assert llm.call_args.kwargs["schema"] is ONBOARDING

    def test_security_audit(self, mocker):
        mocker.patch("agents.security_audit.logic.fetch_repo", return_value={"name": "n"})
        mocker.patch("agents.security_audit.logic.generate_onboarding_guide", return_value={"error_message": "x"})
        mocker.patch("agents.security_audit.logic.analyze_cve_impact", return_value={"error_message": "x"})
        llm = mocker.patch("agents.security_audit.logic.call_llm", return_value=json.dumps(SAMPLES["audit"]))
        from agents.security_audit.logic import generate_security_audit

        assert generate_security_audit("https://github.com/o/r")["overall_risk"] == "High"
        assert llm.call_args.kwargs["schema"] is AUDIT

    def test_retries_also_ask_for_the_schema(self, mocker):
        mocker.patch("agents.blog_scout.logic.web_search", return_value=[{"title": "t", "url": "https://e.com/a", "snippet": "s"}])
        llm = mocker.patch("agents.blog_scout.logic.call_llm", side_effect=["not json", json.dumps(SAMPLES["blog"])])
        from agents.blog_scout.logic import scout_blog_ideas

        scout_blog_ideas("x")

        assert llm.call_count == 2
        assert all(c.kwargs["schema"] is BLOG for c in llm.call_args_list)

    def test_refusal_reaches_the_user_as_a_message(self, mocker):
        mocker.patch("agents.repo_onboarding.logic.fetch_repo", return_value={"name": "n"})
        mocker.patch("agents.repo_onboarding.logic.call_llm", side_effect=LLMOutputError("The model declined to produce this result."))
        from agents.repo_onboarding.logic import generate_onboarding_guide

        assert generate_onboarding_guide("https://github.com/o/r")["error_message"].startswith("The model declined")


# ---- prompts: the delimiter is written in exactly one place ------------------------------------------

AGENT_PROMPT_MODULES = [
    "blog_scout", "repo_onboarding", "cve_impact", "issue_fix_planner", "do_i_care", "opportunity_scout", "security_audit",
]


class TestPromptTemplatesDoNotHardCodeTheDelimiter:
    @pytest.mark.parametrize("agent", AGENT_PROMPT_MODULES)
    def test_user_templates_take_already_wrapped_values(self, agent):
        module = importlib.import_module(f"agents.{agent}.prompts")
        templates = {
            name: value for name, value in vars(module).items()
            if isinstance(value, str) and name.isupper() and name != "SYSTEM_PROMPT" and ("TEMPLATE" in name or name.endswith("_PROMPT"))
        }
        assert templates, f"no templates found in {agent}"
        for name, text in templates.items():
            assert "<untrusted_data" not in text, f"{agent}.{name} hard-codes the delimiter; use core.llm_utils.wrap_untrusted"
