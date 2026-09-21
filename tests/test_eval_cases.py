"""Eval case files: they must be valid, use real checks, and be backed by recorded outputs."""

import json

import pytest

from evals import cases as cases_module
from evals.cases import AGENTS, CaseError, golden_path, load_all, load_cases
from evals.checks import REGISTRY


@pytest.fixture(scope="module")
def shipped():
    return load_all()


class TestShippedCases:
    def test_every_agent_has_cases(self, shipped):
        assert set(shipped) == set(AGENTS)
        assert all(len(v) >= 3 for v in shipped.values())

    def test_security_audit_is_covered(self, shipped):
        assert "security_audit" in shipped  # the old runner had no entry for it

    def test_every_check_type_exists_and_manual_is_gone(self, shipped):
        types = {s["type"] for cases in shipped.values() for c in cases for s in c.checks}
        assert types <= set(REGISTRY)
        assert "manual" not in types

    def test_no_case_is_only_a_smoke_test(self, shipped):
        # Every case must assert on content or an error, not merely that something came back.
        for cases in shipped.values():
            for c in cases:
                assert any(s["type"] != "success" for s in c.checks), f"{c.key} only checks for success"

    def test_offline_cases_are_error_paths(self, shipped):
        for cases in shipped.values():
            for c in cases:
                if c.offline:
                    assert [s["type"] for s in c.checks] == ["error"], f"{c.key} is offline but not a pure error path"

    # Checks that already assert several things at once, so one of them is enough for a case.
    COMPOSITE = {"blog_ok_or_explained", "error_or_report"}

    def test_networked_cases_that_expect_success_assert_more_than_that_something_came_back(self, shipped):
        for cases in shipped.values():
            for c in cases:
                if not c.offline and not any(s["type"] == "error" for s in c.checks):
                    assert len(c.checks) >= 2 or {s["type"] for s in c.checks} <= self.COMPOSITE, f"{c.key} asserts too little"

    def test_ids_are_unique_across_the_suite(self, shipped):
        keys = [c.key for cases in shipped.values() for c in cases]
        assert len(keys) == len(set(keys))

    def test_every_networked_case_has_a_recorded_output(self, shipped):
        missing = [c.key for cases in shipped.values() for c in cases if not c.offline and not golden_path(c.agent, c.id).exists()]
        assert not missing, f"no golden for {missing}; record with: python evals/run_evals.py --record"

    def test_recorded_outputs_belong_to_their_case(self, shipped):
        for cases in shipped.values():
            for c in cases:
                path = golden_path(c.agent, c.id)
                if path.exists():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    assert (data["agent"], data["id"]) == (c.agent, c.id)
                    assert data["input"] == c.input, f"{c.key}: the case input changed since it was recorded; re-record"

    def test_no_orphaned_recorded_outputs(self, shipped):
        known = {(c.agent, c.id) for cases in shipped.values() for c in cases if not c.offline}
        found = {(p.parent.name, p.stem) for p in (golden_path("x", "y").parent.parent).glob("*/*.json")}
        assert found <= known, f"recorded outputs with no case: {sorted(found - known)}"


class TestReplayOfShippedCases:
    """Pytest-level twin of `python evals/run_evals.py --replay`: the checks accept the recorded outputs."""

    def test_replay_passes_for_every_case(self, shipped):
        from evals.run_evals import PASS, run_case

        failures = []
        for cases in shipped.values():
            for c in cases:
                r = run_case(c, "replay")
                if r.status != PASS:
                    failures.append((c.key, r.failures))
        assert not failures, failures


def write_cases(tmp_path, monkeypatch, lines, agent="blog_scout"):
    path = tmp_path / "evals.jsonl"
    path.write_text("\n".join(json.dumps(x) if not isinstance(x, str) else x for x in lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(cases_module, "cases_path", lambda a: path)
    return agent


GOOD = {"id": "ok", "description": "d", "input": "x", "checks": [{"type": "count", "path": "", "min": 1}]}


class TestLoaderValidation:
    def problems(self, tmp_path, monkeypatch, lines, agent="blog_scout"):
        write_cases(tmp_path, monkeypatch, lines, agent)
        with pytest.raises(CaseError) as e:
            load_cases(agent)
        return e.value.problems

    def test_valid_file_loads(self, tmp_path, monkeypatch):
        write_cases(tmp_path, monkeypatch, [GOOD, {**GOOD, "id": "second", "offline": True, "attempts": 3}])
        cases = load_cases("blog_scout")
        assert [c.id for c in cases] == ["ok", "second"]
        assert cases[1].offline and cases[1].attempts == 3 and cases[0].key == "blog_scout/ok"

    def test_old_format_is_rejected(self, tmp_path, monkeypatch):
        old = {"input": "x", "expected_behavior": "Should work", "check": "manual"}
        assert any("old-format" in p for p in self.problems(tmp_path, monkeypatch, [old]))

    def test_unknown_check_type(self, tmp_path, monkeypatch):
        bad = {**GOOD, "checks": [{"type": "manual"}]}
        assert any("unknown check type 'manual'" in p for p in self.problems(tmp_path, monkeypatch, [bad]))

    def test_missing_check_parameter(self, tmp_path, monkeypatch):
        bad = {**GOOD, "checks": [{"type": "equals", "path": "a"}]}
        assert any("missing" in p for p in self.problems(tmp_path, monkeypatch, [bad]))

    def test_empty_checks(self, tmp_path, monkeypatch):
        assert any("non-empty list" in p for p in self.problems(tmp_path, monkeypatch, [{**GOOD, "checks": []}]))

    def test_duplicate_ids(self, tmp_path, monkeypatch):
        assert any("duplicate id" in p for p in self.problems(tmp_path, monkeypatch, [GOOD, GOOD]))

    def test_bad_id(self, tmp_path, monkeypatch):
        assert any("'id' must be" in p for p in self.problems(tmp_path, monkeypatch, [{**GOOD, "id": "Not Valid!"}]))

    def test_missing_description_and_input(self, tmp_path, monkeypatch):
        bad = {"id": "x", "checks": GOOD["checks"]}
        problems = self.problems(tmp_path, monkeypatch, [bad])
        assert any("'description'" in p for p in problems) and any("'input'" in p for p in problems)

    @pytest.mark.parametrize("agent,value", [("do_i_care", "a string"), ("do_i_care", [1, 2]), ("repo_onboarding", ["x"]), ("opportunity_scout", None)])
    def test_input_types_are_checked_per_agent(self, tmp_path, monkeypatch, agent, value):
        assert any("input must be" in p for p in self.problems(tmp_path, monkeypatch, [{**GOOD, "input": value}], agent))

    def test_blog_input_may_be_null(self, tmp_path, monkeypatch):
        write_cases(tmp_path, monkeypatch, [{**GOOD, "input": None}])
        assert load_cases("blog_scout")[0].input is None

    def test_unknown_keys_and_bad_flags(self, tmp_path, monkeypatch):
        problems = self.problems(tmp_path, monkeypatch, [{**GOOD, "colour": "red", "offline": "yes", "attempts": 0}])
        assert any("unknown keys" in p for p in problems)
        assert any("'offline'" in p for p in problems)
        assert any("'attempts'" in p for p in problems)

    def test_invalid_json_and_non_objects(self, tmp_path, monkeypatch):
        problems = self.problems(tmp_path, monkeypatch, ["{not json", "[1, 2]"])
        assert any("invalid JSON" in p for p in problems) and any("must be an object" in p for p in problems)

    def test_all_problems_are_reported_together(self, tmp_path, monkeypatch):
        problems = self.problems(tmp_path, monkeypatch, [{"id": "a"}, {"id": "b"}])
        assert len(problems) >= 4

    def test_missing_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cases_module, "cases_path", lambda a: tmp_path / "nope.jsonl")
        with pytest.raises(CaseError, match="no evals.jsonl"):
            load_cases("blog_scout")
