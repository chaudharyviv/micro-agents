"""The eval runner: classification, retries, replay, baselines, and above all its exit codes."""

import json
import socket

import pytest

from evals import run_evals as runner
from evals.cases import Case, CaseError
from evals.run_evals import (
    ERROR, FAIL, PASS, CaseResult, agent_summary, decide, infra_problem, load_baseline, no_network, regressions,
    run_case, save_baseline,
)

ERR_NOT_FOUND = {"error_message": "Repository not found: x", "status": "error"}


def case(checks=None, **kw):
    defaults = dict(agent="cve_impact", id="c", description="d", input="https://github.com/o/r",
                    checks=checks or [{"type": "nonempty", "paths": ["a"]}])
    return Case(**{**defaults, **kw})


def result(status, agent="cve_impact", id="c"):
    return CaseResult(agent, id, status)


@pytest.fixture
def agent_returns(monkeypatch):
    """Make call_agent return the queued outputs (or raise them if they are exceptions)."""
    calls = []

    def install(*outputs):
        queue = list(outputs)

        def fake(agent, value):
            calls.append((agent, value))
            out = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(out, Exception):
                raise out
            return out

        monkeypatch.setattr(runner, "call_agent", fake)
        return calls

    return install


class TestRunCaseLive:
    def test_pass(self, agent_returns):
        agent_returns({"a": "x"})
        r = run_case(case(), "live")
        assert r.status == PASS and r.failures == [] and r.attempts == 1

    def test_fail_reports_which_check_and_why(self, agent_returns):
        agent_returns({"a": ""})
        r = run_case(case(), "live")
        assert r.status == FAIL and "nonempty" in r.failures[0] and "['a']" in r.failures[0]

    def test_all_checks_are_reported_not_just_the_first(self, agent_returns):
        agent_returns({})
        r = run_case(case([{"type": "nonempty", "paths": ["a"]}, {"type": "equals", "path": "b", "value": 1}]), "live")
        assert len(r.failures) == 2

    def test_retry_then_pass_is_marked_flaky(self, agent_returns):
        calls = agent_returns({"a": ""}, {"a": "x"})
        r = run_case(case(attempts=3), "live")
        assert r.status == PASS and r.attempts == 2 and len(calls) == 2
        assert any("flaky" in n for n in r.notes)

    def test_still_failing_after_all_attempts(self, agent_returns):
        calls = agent_returns({"a": ""})
        r = run_case(case(attempts=3), "live")
        assert r.status == FAIL and r.attempts == 3 and len(calls) == 3

    def test_run_wide_attempts_apply_unless_the_case_sets_its_own(self, agent_returns):
        calls = agent_returns({"a": ""})
        run_case(case(), "live", attempts=2)
        assert len(calls) == 2
        calls.clear()
        run_case(case(attempts=1), "live", attempts=5)
        assert len(calls) == 1

    def test_an_agent_exception_is_a_failure_with_the_reason(self, agent_returns):
        agent_returns(RuntimeError("kaboom"))
        r = run_case(case(), "live")
        assert r.status == FAIL and "RuntimeError" in r.failures[0] and "kaboom" in r.failures[0]

    def test_error_result_where_success_was_expected_is_a_failure(self, agent_returns):
        agent_returns({"error_message": "Failed to parse onboarding guide. Please try again."})
        assert run_case(case([{"type": "success"}]), "live").status == FAIL  # the agent's fault, not the environment's


class TestInfrastructureIsNotAgentFailure:
    @pytest.mark.parametrize(
        "message",
        [
            "GitHub rate limit exceeded",
            "GitHub API error. Please try again.",
            "Could not reach the OSV.dev vulnerability database. Please try again later.",
            "OpenAI backend unavailable after retrying. Please try again shortly.",
            "The service has reached its daily usage limit. It resets at 00:00 UTC.",
            "This request reached its limit of 12 model calls and was stopped.",
            "Could not retrieve current job-market data, so no comparison can be made.",
        ],
    )
    def test_classified_as_infra(self, agent_returns, message):
        agent_returns({"error_message": message})
        r = run_case(case([{"type": "success"}]), "live")
        assert r.status == ERROR and message[:20] in r.failures[0]

    def test_infra_errors_are_not_retried(self, agent_returns):
        calls = agent_returns({"error_message": "GitHub rate limit exceeded"})
        run_case(case([{"type": "success"}], attempts=3), "live")
        assert len(calls) == 1

    def test_a_case_that_expects_an_error_is_judged_by_its_check(self, agent_returns):
        agent_returns({"error_message": "GitHub rate limit exceeded"})
        r = run_case(case([{"type": "error", "contains_any": ["not found"]}]), "live")
        assert r.status == FAIL  # a rate limit is not 'not found'

    def test_blog_failure_placeholder_from_search_outage_is_infra(self):
        out = [{"title": "Unable to generate ideas", "pitch": "No search results were returned. Please try again later."}]
        assert infra_problem(case(agent="blog_scout"), out)

    def test_ordinary_success_is_not_infra(self):
        assert infra_problem(case(), {"a": 1}) is None


class TestReplay:
    def test_uses_the_recorded_output_and_never_calls_the_agent(self, agent_returns, tmp_path, monkeypatch):
        calls = agent_returns({"a": ""})
        path = tmp_path / "g.json"
        path.write_text(json.dumps({"output": {"a": "recorded"}}))
        monkeypatch.setattr(runner, "golden_path", lambda a, i: path)
        monkeypatch.setattr(runner, "ROOT", tmp_path)

        r = run_case(case(), "replay")

        assert r.status == PASS and calls == []

    def test_a_recorded_output_that_no_longer_satisfies_the_checks_fails(self, tmp_path, monkeypatch):
        path = tmp_path / "g.json"
        path.write_text(json.dumps({"output": {"a": ""}}))
        monkeypatch.setattr(runner, "golden_path", lambda a, i: path)
        assert run_case(case(), "replay").status == FAIL

    def test_missing_recording_fails_and_says_how_to_fix_it(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner, "golden_path", lambda a, i: tmp_path / "missing.json")
        monkeypatch.setattr(runner, "ROOT", tmp_path)
        r = run_case(case(), "replay")
        assert r.status == FAIL and "--record" in r.failures[0]

    def test_network_checks_are_skipped_and_reported(self, tmp_path, monkeypatch):
        path = tmp_path / "g.json"
        path.write_text(json.dumps({"output": {"cve_analysis": []}}))
        monkeypatch.setattr(runner, "golden_path", lambda a, i: path)
        r = run_case(case([{"type": "osv_ids_exist"}]), "replay")
        assert r.status == PASS and "osv_ids_exist" in r.notes[0]

    def test_offline_case_runs_the_real_agent_code_with_the_network_blocked(self, agent_returns):
        calls = agent_returns(ERR_NOT_FOUND)
        r = run_case(case([{"type": "error", "contains_any": ["not found"]}], offline=True), "replay")
        assert r.status == PASS and len(calls) == 1

    def test_offline_case_that_touches_the_network_fails(self, monkeypatch):
        def sneaky(agent, value):
            try:
                socket.create_connection(("example.com", 80), timeout=1)
            except OSError:
                pass
            return ERR_NOT_FOUND

        monkeypatch.setattr(runner, "call_agent", sneaky)
        r = run_case(case([{"type": "error", "contains_any": ["not found"]}], offline=True), "replay")
        assert r.status == FAIL and "network access" in r.failures[0]


class TestNetworkBlock:
    def test_blocks_and_counts_then_restores(self):
        real = socket.create_connection
        with no_network() as seen:
            with pytest.raises(OSError, match="blocked"):
                socket.create_connection(("example.com", 80))
            with pytest.raises(OSError):
                socket.socket().connect(("example.com", 80))
        assert seen["count"] == 2
        assert socket.create_connection is real

    def test_restores_after_an_exception(self):
        real = socket.socket.connect
        with pytest.raises(ValueError):
            with no_network():
                raise ValueError("x")
        assert socket.socket.connect is real


class TestExitCodes:
    def test_all_passing_is_zero(self):
        assert decide([result(PASS), result(PASS, id="d")], 0.8, None)[0] == 0

    def test_below_the_bar_is_one(self):
        code, reasons = decide([result(PASS), result(FAIL, id="d")], 0.8, None)
        assert code == 1 and "below the pass-rate bar" in reasons[0]

    def test_meeting_the_bar_with_a_known_failure_is_still_zero(self):
        results = [result(PASS, id=str(i)) for i in range(4)] + [result(FAIL, id="x")]
        assert decide(results, 0.8, None)[0] == 0

    def test_replay_bar_of_one_means_any_failure_fails(self):
        assert decide([result(PASS), result(FAIL, id="d")], 1.0, None)[0] == 1

    def test_regression_against_the_baseline_is_one_even_above_the_bar(self):
        results = [result(PASS, id=str(i)) for i in range(9)] + [result(FAIL, id="x")]
        code, reasons = decide(results, 0.8, {"cve_impact/x": PASS})
        assert code == 1 and "cve_impact/x" in reasons[0]

    def test_a_failure_that_was_already_failing_is_not_a_regression(self):
        results = [result(PASS, id=str(i)) for i in range(9)] + [result(FAIL, id="x")]
        assert decide(results, 0.8, {"cve_impact/x": FAIL})[0] == 0

    def test_infra_errors_alone_are_two(self):
        code, reasons = decide([result(PASS), result(ERROR, id="d")], 0.8, None)
        assert code == 2 and "infrastructure" in reasons[0]

    def test_quality_failure_outranks_infra_errors(self):
        assert decide([result(FAIL), result(ERROR, id="d")], 0.8, None)[0] == 1

    def test_infra_errors_are_not_counted_against_the_agent(self):
        summary = agent_summary([result(PASS), result(ERROR, id="d"), result(ERROR, id="e")])
        assert summary["cve_impact"]["rate"] == 1.0

    def test_an_agent_where_nothing_could_be_judged_is_two_not_zero(self):
        code, reasons = decide([result(ERROR), result(PASS, agent="blog_scout")], 0.8, None)
        assert code == 2 and "no case could be judged" in " ".join(reasons)


class TestBaseline:
    def test_roundtrip_excludes_infra_errors(self, tmp_path):
        path = tmp_path / "b.json"
        save_baseline(path, [result(PASS, id="a"), result(FAIL, id="b"), result(ERROR, id="c")])
        assert load_baseline(path) == {"cve_impact/a": PASS, "cve_impact/b": FAIL}

    def test_missing_baseline_is_none(self, tmp_path):
        assert load_baseline(tmp_path / "nope.json") is None

    def test_regressions_ignore_new_and_already_failing_cases(self):
        results = [result(FAIL, id="was-pass"), result(FAIL, id="was-fail"), result(FAIL, id="brand-new")]
        baseline = {"cve_impact/was-pass": PASS, "cve_impact/was-fail": FAIL}
        assert regressions(results, baseline) == ["cve_impact/was-pass"]
        assert regressions(results, None) == []


class TestMain:
    """End to end through main(), with the case loader and agents faked."""

    @pytest.fixture
    def suite(self, monkeypatch, tmp_path):
        c = Case("blog_scout", "one", "d", None, [{"type": "nonempty", "paths": ["a"]}], offline=True)
        monkeypatch.setattr(runner, "load_all", lambda agents=None: {"blog_scout": [c]})
        monkeypatch.setattr(runner, "AGENTS", ["blog_scout"])
        monkeypatch.setattr(runner, "BASELINE_PATH", tmp_path / "baseline.json")
        return c

    def test_exit_zero_when_everything_passes(self, suite, agent_returns, capsys):
        agent_returns({"a": "x"})
        assert runner.main(["--replay", "--agent", "blog_scout"]) == 0
        assert "OK" in capsys.readouterr().out

    def test_exit_one_on_failure_and_names_the_failing_check(self, suite, agent_returns, capsys):
        agent_returns({"a": ""})
        assert runner.main(["--replay", "--agent", "blog_scout"]) == 1
        out = capsys.readouterr().out
        assert "FAIL" in out and "nonempty" in out and "blog_scout/one" in out

    def test_exit_three_for_invalid_case_files(self, monkeypatch, capsys):
        def broken(agents=None):
            raise CaseError(["blog_scout/x: unknown check type 'manual'"])

        monkeypatch.setattr(runner, "load_all", broken)
        assert runner.main(["--replay"]) == 3
        assert "unknown check type" in capsys.readouterr().err

    def test_exit_three_for_a_live_run_without_a_key(self, suite, monkeypatch, capsys):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
        assert runner.main(["--agent", "blog_scout"]) == 3
        assert "OPENAI_API_KEY" in capsys.readouterr().err

    def test_record_and_baseline_need_a_live_run(self, suite, capsys):
        assert runner.main(["--replay", "--record"]) == 3
        assert runner.main(["--replay", "--update-baseline"]) == 3

    def test_list_does_not_run_anything(self, suite, agent_returns, capsys):
        calls = agent_returns({"a": "x"})
        assert runner.main(["--list"]) == 0
        assert calls == [] and "blog_scout/one" in capsys.readouterr().out

    def test_no_cases_selected_is_three(self, suite, capsys):
        assert runner.main(["--replay", "--only", "no-such-case"]) == 3

    def test_json_report_and_step_summary(self, suite, agent_returns, tmp_path, monkeypatch):
        agent_returns({"a": "x"})
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        out = tmp_path / "r.json"
        assert runner.main(["--replay", "--json-out", str(out)]) == 0
        report = json.loads(out.read_text())
        assert report["exit_code"] == 0 and report["cases"][0]["key"] == "blog_scout/one"
        assert "| blog_scout |" in summary.read_text()

    def test_output_is_ascii_safe_for_windows_consoles(self, suite, agent_returns, capsys):
        agent_returns({"a": ""})
        runner.main(["--replay"])
        capsys.readouterr().out.encode("cp1252")  # would raise on emoji
