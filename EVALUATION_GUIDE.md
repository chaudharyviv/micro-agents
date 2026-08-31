# Evaluation Guide: Achieving 80%+ Pass Rates

This guide explains what the pass rates mean, how to run evaluations, and how to improve agents to meet the 80%+ target.

---

## What Are Pass Rates?

**Pass rates** measure how well each agent performs on its test cases. Each agent has 3-5 predefined test cases in `evals.jsonl` that verify:

✅ **Core functionality** — Does the agent produce output in the right format?
✅ **Correctness** — Are results accurate and useful?
✅ **Error handling** — Does the agent fail gracefully on bad input?
✅ **Guardrails** — For planning agents: no code generation, no hallucination

**Example test case:**
```jsonl
{"input": "machine learning", "expected_behavior": "Should return 3-5 blog ideas with valid https:// URLs", "check": "has_source_url"}
```

---

## Current State

The pass rates in README.md are **currently placeholder estimates**. To get real, actionable pass rates:

```bash
python evals/run_evals.py
```

This runs all agent tests and generates a table like:

```
Agent                     Pass Rate       Tests      Status
────────────────────────────────────────────────────────────
blog_scout                80% (4/5)       ✅ PASS
repo_onboarding           60% (3/5)       ⚠️  REVIEW
cve_impact                40% (2/5)       ⚠️  REVIEW
issue_fix_planner         60% (3/5)       ⚠️  REVIEW
do_i_care                 60% (3/5)       ⚠️  REVIEW
opportunity_scout         40% (2/5)       ⚠️  REVIEW
────────────────────────────────────────────────────────────
OVERALL                   55%
```

---

## How to Improve Pass Rates

### 1. Identify Failures

Run evaluations with verbose output:

```bash
python evals/run_evals.py --verbose
```

This shows which tests failed:
```
📊 Testing repo_onboarding...
    ✅ Test 1/5
    ✅ Test 2/5
    ❌ Test 3/5  ← Failed test
    ✅ Test 4/5
    ❌ Test 5/5  ← Failed test
   ⚠️ Pass rate: 3/5 (60%)
   Failures:
     - Test 3: https://github.com/anthropics/anthropic-sdk-python
     - Test 5: https://github.com/gradio-app/gradio
```

### 2. Understand the Test Case

Open `agents/{agent}/evals.jsonl` and examine the failing test:

```jsonl
{"input": "https://github.com/gradio-app/gradio", "expected_behavior": "Should generate comprehensive onboarding guide (200-500 words) with setup instructions and project structure", "check": "guide_completeness"}
```

**Keys:**
- `input` — What to pass to the agent
- `expected_behavior` — What should happen
- `check` — Validation rule

### 3. Run the Agent Manually

Test the agent directly:

```python
from agents.repo_onboarding.logic import generate_onboarding_guide

result = generate_onboarding_guide("https://github.com/gradio-app/gradio")
print(result)
```

**Check:**
- Does the output have the expected fields? (e.g., `project_name`, `overview`, `setup_steps`)
- Is the output correct? (e.g., are setup steps actually for Gradio?)
- Does it match the expected behavior?

### 4. Debug the Issue

Common reasons agents fail:

| Issue | Solution |
|-------|----------|
| **API rate limit** | Wait 1 min, add `GITHUB_TOKEN` for higher limits |
| **API timeout** | Increase timeout in `core/llm.py`, use fallback API |
| **LLM hallucination** | Improve system prompt to be more explicit |
| **Parse error** | Agent output is invalid format (not JSON, missing fields) |
| **Missing dependency** | Run `pip install -r requirements.txt` |
| **Network error** | Check internet connection, test `curl` to APIs |

### 5. Fix the Agent

Edit the agent's logic:

**Example: repo_onboarding failing on parse**

```python
# agents/repo_onboarding/logic.py

def _parse_guide(response: str, repo_url: str) -> Optional[dict]:
    """Parse JSON response from LLM."""
    try:
        # ... existing code ...
        
        # Add this check if missing
        if "overview" not in guide:
            logger.warning("Guide missing 'overview' field")
            # Fallback: add default
            guide["overview"] = f"Project at {repo_url}"
        
        return guide
    except json.JSONDecodeError:
        # ... improve error message ...
```

### 6. Rerun Evals

After fixing:

```bash
python evals/run_evals.py --verbose
```

Verify the previously-failed test now passes.

### 7. Iterate

Repeat steps 1-6 until pass rate ≥ 80%.

---

## Common Patterns for Improving Pass Rates

### Pattern 1: Improve Prompt

If LLM output is wrong, improve the system or user prompt:

```python
# agents/my_agent/prompts.py

# ❌ Too vague
SYSTEM_PROMPT = "You are a helpful assistant."

# ✅ Specific, with guardrails
SYSTEM_PROMPT = """You are a technical assistant specializing in GitHub analysis.
Your job is to:
1. Analyze the provided repository
2. Extract key information (name, description, setup steps)
3. Return ONLY a valid JSON object

IMPORTANT: Do NOT invent information. Use ONLY facts from provided data."""
```

### Pattern 2: Add Error Handling

Graceful degradation improves pass rate:

```python
# agents/my_agent/logic.py

try:
    data = fetch_repo(url)
except Exception as e:
    logger.error(f"Failed to fetch: {e}")
    # Return graceful error instead of crashing
    return {
        "error_message": f"Failed to fetch repository: {str(e)[:100]}",
        "status": "error"
    }
```

### Pattern 3: Improve Parsing

Make output parsing more robust:

```python
# ❌ Fragile: assumes exact format
guide = json.loads(response)

# ✅ Robust: handles markdown code blocks
clean_response = response.strip()
if clean_response.startswith("```"):
    # Extract JSON from markdown
    lines = clean_response.split("\n")[1:-1]
    clean_response = "\n".join(lines)
guide = json.loads(clean_response)
```

### Pattern 4: Add Fallback Logic

Use fallback APIs to improve reliability:

```python
# agents/my_agent/logic.py

try:
    results = search(query, max_results=5)  # Tavily primary
except Exception as e:
    logger.warning(f"Primary search failed, using fallback: {e}")
    results = search_fallback(query)  # DuckDuckGo fallback
```

---

## Evaluation Metrics Explained

Each test case has a `check` rule:

| Rule | What It Tests | Example |
|------|---------------|---------|
| **min_count** | Output has minimum items | Blog Scout returns ≥3 ideas |
| **has_source_url** | All items have URLs | Each idea has a valid link |
| **has_structure** | Output has required fields | Dict has `top_items` list |
| **no_code_generation** | No code in output | Issue Planner returns plan, not code |
| **error_handling** | Errors handled gracefully | Returns `error_message` on failure |
| **input_validation** | Bad input rejected | Empty input returns error message |
| **has_plan_structure** | Plan format is correct | Plan is 150-400 words, not code |
| **keyword_presence** | Output contains key terms | Blog ideas mention topic |
| **manual** | Human inspection needed | Requires manual review |

---

## Realistic Pass Rate Targets

Different agents have different difficulty levels:

| Agent | Difficulty | Initial | Target | Why |
|-------|-----------|---------|--------|-----|
| **Blog Scout** | ⭐ Low | 80%+ | 90%+ | Simple search + LLM |
| **Repo Onboarding** | ⭐⭐ Medium | 60-70% | 85%+ | Depends on repo quality |
| **CVE Impact** | ⭐⭐ Medium | 60% | 80%+ | Security info varies |
| **Issue Planner** | ⭐⭐⭐ Hard | 50-60% | 80%+ | Requires context understanding |
| **Do I Care?** | ⭐⭐⭐ Hard | 60% | 80%+ | Subjective relevance scoring |
| **Opportunity Scout** | ⭐⭐⭐ Hard | 50-60% | 80%+ | Career analysis is complex |

---

## Step-by-Step Example: Fixing `do_i_care`

Suppose Do I Care? has 60% pass rate:

### Step 1: Run verbose evals
```bash
python evals/run_evals.py --verbose
```

Output:
```
📊 Testing do_i_care...
    ✅ Test 1/5
    ✅ Test 2/5
    ❌ Test 3/5  ← Fails here
    ✅ Test 4/5
    ❌ Test 5/5
   ⚠️ Pass rate: 3/5 (60%)
```

### Step 2: Check the failing test
```bash
# agents/do_i_care/evals.jsonl
# Line 3 (Test 3):
{"input": [], "expected_behavior": "Should return error message about empty input", "check": "input_validation"}
```

**Issue:** Passing empty list, should return error.

### Step 3: Run manually
```python
from agents.do_i_care.logic import run_do_i_care

result = run_do_i_care([])  # Empty input
print(result)
# Output: {"error_message": "No headlines provided", "status": "error"}
```

### Step 4: Check the validation rule
```python
# evals/run_evals.py
def check_result(output, rule):
    elif rule == "input_validation":
        if isinstance(output, str):
            return "error" in output.lower() or "please" in output.lower()
        if isinstance(output, dict):
            return "error_message" in output
        return False
```

**Issue found:** The check works correctly! The agent is returning `error_message`, so validation should pass.

### Step 5: Debug further
```python
# Actually run the eval
from evals.run_evals import evaluate_do_i_care

test_case = {"input": [], "check": "input_validation"}
result = evaluate_do_i_care(test_case)
print(f"Passed: {result}")  # Should be True
```

If still failing, check:
- Is the agent raising an exception?
- Is the error message in the dict?
- Is the `input` being parsed correctly?

### Step 6: Fix the issue
```python
# agents/do_i_care/logic.py

def run_do_i_care(headlines_batch, user_profile=None):
    # Add explicit check at start
    if not headlines_batch:
        return {
            "error_message": "No headlines provided. Please enter at least one headline.",
            "status": "error",
        }
    # ... rest of logic ...
```

### Step 7: Rerun evals
```bash
python evals/run_evals.py --verbose
```

Output should now show Test 3 passing:
```
📊 Testing do_i_care...
    ✅ Test 1/5
    ✅ Test 2/5
    ✅ Test 3/5  ← Now passes!
    ✅ Test 4/5
    ❌ Test 5/5
   ⚠️ Pass rate: 4/5 (80%)  ← Improved!
```

---

## Tips for 80%+ Achievement

✅ **Do this:**
1. Run evals frequently during development
2. Improve prompts based on failure patterns
3. Add robust error handling for edge cases
4. Use fallback APIs when primary fails
5. Test with real inputs (not mocked data)
6. Iterate: fix one agent at a time

❌ **Don't do this:**
1. Ignore low pass rates
2. Add special cases for specific tests (cheating)
3. Relax test criteria (they're there for a reason)
4. Skip manual testing (evals don't catch everything)
5. Deploy with <80% pass rate on agents

---

## Deployment Readiness Checklist

- [ ] All agents have ≥80% pass rate
- [ ] No hardcoded test data in logic
- [ ] Evals run cleanly: `python evals/run_evals.py`
- [ ] Manual spot-check: try each agent once
- [ ] README.md has real pass rates (from last eval run)
- [ ] All test cases documented

---

## References

- **Evals runner:** [evals/run_evals.py](./evals/run_evals.py)
- **Agent structure:** [ARCHITECTURE.md](./ARCHITECTURE.md)
- **Example agent:** [agents/blog_scout/](./agents/blog_scout/)
