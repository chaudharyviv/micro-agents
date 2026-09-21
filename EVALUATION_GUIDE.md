# Evaluation Guide

How the agents are evaluated, how to run the suite, how to add a case, and how to read a failure.

The evals answer one question: **does each agent do what it claims, on real inputs, in a way we can check?**
They are only useful if they can fail, so every check asserts on content (is the answer grounded, ranked,
routed, consistent?) rather than on the mere presence of an answer, and the checks themselves are tested.

For current pass rates, run the suite or read the latest CI run. Numbers are deliberately not copied into
docs, because they go stale; `evals/baseline.json` records the last accepted live run.

---

## Running the evals

```bash
python evals/run_evals.py --replay        # offline, free, no keys: what CI runs on every change
python evals/run_evals.py                 # live: real OpenAI, Tavily, GitHub and OSV.dev calls
python evals/run_evals.py --agent do_i_care --only ranks   # a subset
python evals/run_evals.py --list          # show every case
python evals/run_evals.py -v              # also list passing cases and notes
```

A live run needs `OPENAI_API_KEY` and `TAVILY_API_KEY` (and `GITHUB_TOKEN` is strongly recommended, since GitHub's
unauthenticated limit of 60 requests an hour is not enough). It makes roughly 40 model calls, and the runner caps
a run with `--max-model-calls` (default 250) using the same budget gate as the app.

### The two modes

| | `--replay` (offline) | live |
|---|---|---|
| Needs | nothing | API keys, network |
| Costs | nothing | a few cents |
| Cases marked `"offline": true` (validation and error paths) | run the **real agent code**, with all network access blocked | run the real agent code |
| Other cases | the checks run against the output **recorded** in `evals/golden/` | run the real agent |
| Catches | invalid case files; a check that no longer matches the shape agents return; a case with no recording; validation and error-path regressions | everything above, plus real model behavior and live data |

Replay is what runs on every pull request. It cannot see a change to an agent's *behavior* on networked cases,
because it replays recorded output; the scheduled live run does (see CI below).

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Every agent met the pass-rate bar and nothing regressed against the baseline |
| 1 | **Quality failure**: an agent is below `--min-pass-rate` (default 0.8 live, 1.0 replay), or a case that passed in the baseline now fails |
| 2 | **Infrastructure**: rate limits, outages or the budget cap stopped cases from running, and no quality failure was seen |
| 3 | The suite could not run: invalid case files, a live run without keys, or bad arguments |

Infrastructure errors are reported separately and are **not counted against an agent's pass rate**, so a GitHub
outage cannot turn an agent red, and cannot turn it green either: the run exits 2.

---

## Anatomy of a case

Cases live in `agents/<agent>/evals.jsonl`, one JSON object per line:

```json
{"id": "ranks-relevant-first",
 "description": "Three infrastructure headlines and three irrelevant ones: the top items are the relevant ones.",
 "input": ["Profile: platform engineer moving to Kubernetes", "Kubernetes 1.34 deprecates ...", "Local bakery wins ..."],
 "checks": [{"type": "success"},
            {"type": "ranking", "relevant": ["Kubernetes 1.34 deprecates ..."], "min_hits": 1, "max_offtopic": 0},
            {"type": "analysis_complete"}],
 "attempts": 2}
```

| Field | |
|---|---|
| `id` | Unique within the agent: lower-case letters, digits, hyphens. Used for recordings and the baseline |
| `description` | What the case protects against, in a sentence. Required |
| `input` | What the agent is called with: a string; a list of strings for `do_i_care`; `null` allowed for `blog_scout` |
| `checks` | A non-empty list. **Every** check must pass |
| `offline` | Optional. `true` for validation and error paths that need no network (they run for real in replay mode) |
| `attempts` | Optional. Run up to this many times; the case passes if any attempt does. Passing only on a retry is reported as *flaky*. Use it for probabilistic model behavior, not to hide a bug |

Case files are validated when loaded: an unknown check name, a misspelled parameter, an old-format line, a
duplicate id or a wrong input type stops the run with exit code 3 and lists every problem. Nothing that is
misspelled can silently pass.

## The checks

Defined in `evals/checks.py`. A few of them:

| Check | Asserts |
|---|---|
| `success` / `error` | The agent returned a result, or an error whose message mentions one of the given phrases |
| `nonempty`, `count`, `each`, `unique`, `equals`, `one_of`, `at_least` | Structure and values at a dotted path |
| `contains_any` | The output at a path mentions something specific (for example `tech_stack` includes `go`) |
| `no_placeholder` | None of the "see full analysis" style filler text |
| `no_code`, `word_count`, `has_sections` | An issue plan is a plan: right length, all sections, no code, no PR suggestion |
| `urls_subset` | Every cited URL is one the agent was actually given (grounding) |
| `ranking`, `excludes`, `none_relevant`, `analysis_complete` | Do I Care ranks the relevant items first, keeps injected ones out, filters, and explains |
| `risk_consistent` | The CVE risk level is exactly what the findings imply, judged against a rule the check states itself |
| `osv_ids_exist` | Every CVE/advisory ID really is an OSV record for that package (network; skipped in replay) |
| `routes`, `report_mentions`, `links_safe` | The orchestrator used the right specialists on exactly the target named, and its report has no images or unsourced links |

**The checks are tested too.** `tests/test_eval_checks.py` gives every check a known-good and a known-bad
fixture and fails if a check has none. Two rules learned the hard way:

- A check must be able to fail. The old suite had a `manual` check that passed anything, and an unknown rule name
  that passed by default. Neither exists any more.
- A check must not ask the code under test for the right answer. `risk_consistent` states the risk rule itself;
  had it imported the agent's own function it would have agreed with a broken one.

## Adding a case

1. Add a line to `agents/<agent>/evals.jsonl`. Prefer stable, real targets (a well-known repository, an old
   issue) over ones that might change or disappear.
2. Write checks that would fail if the agent were *wrong*, not just absent. Ask: what defect should turn this red?
3. Validate and run just that case:
   ```bash
   python evals/run_evals.py --list --agent cve_impact
   python evals/run_evals.py --agent cve_impact --only my-new-case --record
   ```
   `--record` saves the passing output to `evals/golden/<agent>/<id>.json` so replay (and CI) can check it.
4. Commit the case and its recording. `tests/test_eval_cases.py` fails if a networked case has no recording, if a
   recording has no case, or if a case's input changed after it was recorded.

If you add a new check, add it to `FIXTURES` in `tests/test_eval_checks.py` with good and bad outputs.

## Recordings and the baseline

- **Recordings** (`evals/golden/`) are real agent outputs from passing live runs. Replay re-runs the *checks*
  against them, so if an agent's output shape changes, or a check is edited, replay fails until the recording is
  refreshed: `python evals/run_evals.py --record`. Review the diff, because a recording is only as good as the run
  it came from.
- **The baseline** (`evals/baseline.json`) is each case's pass/fail from the last accepted live run. A live run
  exits 1 if a case that passed there now fails, even when the agent is still above the pass-rate bar. Update it
  deliberately after an accepted run: `python evals/run_evals.py --update-baseline` (refused if any case hit
  infrastructure errors).

## When something fails

Read the failing line first; each failure names the check and says why:

```
FAIL  do_i_care/nothing-relevant  (1.9s)
      - none_relevant: expected no top items and an explanation, got 3 items, message=None
```

Then decide which of these it is:

1. **The agent is wrong.** Fix the agent. Run the case again with `--only`.
2. **The model was unlucky.** Probabilistic cases have `attempts: 2` for this. If a case is flaky in the run summary
   *often*, it is telling you the behavior is not reliable: strengthen the prompt or schema rather than the retry.
3. **The check or the case is wrong.** A check that is too strict, or a target that changed (a repository renamed,
   a user deleted their repos). Fix the case, and re-record.
4. **The environment.** Reported as `INFRA`, exit code 2. Re-run later.

## CI

- `tests.yml` runs on every push and pull request: the unit tests on Python 3.10-3.12, and the **offline evals**
  (`--replay`), which fail the build on any failure.
- `evals.yml` runs the **live evals** weekly and on demand. It needs repository secrets `OPENAI_API_KEY` and
  `TAVILY_API_KEY`; GitHub access uses the workflow's own token. It exits non-zero on a regression, a case below the
  bar, infrastructure problems, or a configuration problem, and uploads the report as an artifact. It does not run
  on pull requests: fork pull requests cannot read secrets, and a live run costs money.

## Checking that the evals can fail

Green results prove nothing on their own. To confirm the suite is sensitive, break an agent on purpose and check
that the matching case turns red. The technique used when this suite was built: patch one function in-process
(make `_risk_level` return `"Low"`, make the planner append a code block, make a specialist run on the wrong repo,
make a finding carry an invented CVE ID), run the one relevant case live, and expect `FAIL`. All 13 defects tried
were caught. Seven were missed on the first attempt: six because the mutation harness patched the wrong name (so
nothing was actually broken), and one because `risk_consistent` trusted the code it was testing. Fixing that check
is what turned the last miss into a catch.

## What the evals do not cover

- **Answer quality beyond structure and grounding.** Whether an onboarding guide is *good*, or a career suggestion
  *wise*, is not checked; there is no LLM judge, and using the same model family to grade itself would be a weak
  signal anyway.
- **Behavior under attack it can't reproduce.** Prompt-injection resistance is covered by unit tests with hostile
  inputs and by one live case (an injected headline), not by injected content in a real third-party repository.
- **Orchestrator refusal of an ungrounded target.** Tests cover the check itself; a live case can't force the model
  to pick a wrong target on demand.
- **Anything replay can't see.** Replay checks recorded outputs; only a live run exercises current agent behavior
  on networked cases.
- **Sample size.** About 47 cases across 8 agents is a regression net, not a benchmark. Pass rates are not
  statistically meaningful estimates of quality.
