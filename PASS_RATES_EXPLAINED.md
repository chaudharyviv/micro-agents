# Pass Rates Explained: 80%+ for All Agents

## What Are Pass Rates?

**Pass rates** measure **how often each agent produces correct, useful output** when given test inputs.

```
Pass Rate = (# Passing Tests) / (Total Tests) × 100%

Example: 4 out of 5 tests pass = 80% pass rate ✅
```

---

## Why 80%?

✅ **80% = Production Ready**
- Agent works correctly in ~4 out of 5 scenarios
- Edge cases may fail, but core functionality is solid
- Good enough for real-world use

⚠️ **Below 80% = Needs Work**
- More than 1 in 5 tests fail
- Core logic has issues
- Not ready for deployment

---

## How Pass Rates Work

### Example: Blog Scout Agent

**Test Case 1:**
```jsonl
{"input": "machine learning", "expected_behavior": "Should return 3-5 blog ideas with valid https:// URLs", "check": "has_source_url"}
```

**What happens:**
1. Call agent: `scout_blog_ideas("machine learning")`
2. Agent searches → finds 5 articles
3. Agent calls LLM → generates 5 blog ideas with links
4. Check rule `has_source_url` validates each idea has a URL
5. ✅ **Test passes** because all ideas have URLs

**Test Case 2:**
```jsonl
{"input": "", "expected_behavior": "Empty input should search default topics (GitHub trending, AI agents, platform engineering)", "check": "min_count"}
```

**What happens:**
1. Call agent: `scout_blog_ideas("")`
2. Agent treats empty input as `None`
3. Agent searches default topics
4. Agent generates 4 blog ideas
5. Check rule `min_count` validates ≥3 ideas
6. ✅ **Test passes** because 4 ≥ 3

---

## The 3 Types of Pass Rate Issues

### Issue 1: API Failures (Easy to Fix)

**Problem:**
```python
result = fetch_repo(url)  # 404 error
# Crashes without error handling
```

**Solution:**
```python
try:
    result = fetch_repo(url)
except Exception as e:
    # Return graceful error
    return {"error_message": f"Failed: {e}", "status": "error"}
```

**Impact:** Pass rate goes from 20% → 80% (tests expect error handling)

### Issue 2: LLM Hallucination (Medium Difficulty)

**Problem:**
```
User asks: "Analyze CVE-2024-1234"
LLM says: "This CVE affects Linux, Windows, macOS, and also affects
          the moon because it's made of cheese"  ← Hallucination!
```

**Solution:**
```python
# Improve system prompt with guardrails
SYSTEM_PROMPT = """You are a security analyst.
IMPORTANT: Only discuss facts from the provided search results.
Do NOT invent information.
If information is not available, say 'Information not available.'"""
```

**Impact:** Pass rate goes from 40% → 75% (better guardrails)

### Issue 3: Format/Parse Errors (Hardest to Fix)

**Problem:**
```
LLM returns:
"Here are 5 blog ideas:
1. Title: Machine Learning Trends
   Pitch: Understanding ML"

Expected: [{"title": "...", "pitch": "...", "source_url": "..."}]

Format doesn't match → Parse error → Test fails
```

**Solution:**
```python
# Improve prompt to enforce format
USER_PROMPT = """Please return your response as a JSON array:
[
  {
    "title": "...",
    "pitch": "...",
    "source_url": "..."
  }
]

RETURN ONLY THE JSON ARRAY, NO OTHER TEXT."""

# Add robust parsing
try:
    ideas = json.loads(response)
except json.JSONDecodeError:
    # Try extracting JSON from markdown
    ideas = extract_json_from_markdown(response)
```

**Impact:** Pass rate goes from 30% → 80% (proper format + parsing)

---

## How to Achieve 80%+ for Each Agent

### Step 1: Run Baseline Evals

```bash
python evals/run_evals.py
```

Output:
```
Agent                     Pass Rate       Tests      Status
────────────────────────────────────────────────────────────
blog_scout                40% (2/5)       ⚠️  REVIEW
repo_onboarding           60% (3/5)       ⚠️  REVIEW
cve_impact                30% (1/5)       ⚠️  REVIEW
issue_fix_planner         50% (2/4)       ⚠️  REVIEW
do_i_care                 40% (2/5)       ⚠️  REVIEW
opportunity_scout         40% (2/5)       ⚠️  REVIEW
────────────────────────────────────────────────────────────
OVERALL                   43%
```

### Step 2: Prioritize Low Performers

Focus on agents with lowest pass rates:
1. **CVE Impact (30%)** — Start here
2. **Blog Scout (40%)** — Second
3. **Do I Care (40%)** — Third
...etc

### Step 3: Debug One Failure at a Time

For **CVE Impact**, first failure:

```bash
python evals/run_evals.py --verbose 2>&1 | grep -A 5 "cve_impact"
```

Output:
```
📊 Testing cve_impact...
    ❌ Test 1/5
       Error: Failed to parse JSON response
```

**Debug:**
```python
from agents.cve_impact.logic import analyze_cve_impact

result = analyze_cve_impact("CVE-2024-1234")
print(result)
print(type(result))  # Check if dict or string
print(result.get("error_message"))  # Check for errors
```

### Step 4: Fix Issue

Example fixes:

**Fix 1: Add input validation**
```python
def analyze_cve_impact(query):
    if not query or not query.strip():
        return {
            "error_message": "CVE ID or software name required",
            "status": "error"
        }
    # ... rest of logic ...
```

**Fix 2: Improve LLM prompt**
```python
# agents/cve_impact/prompts.py
SYSTEM_PROMPT = """You are a security analyst specializing in CVE assessment.
Analyze the provided CVE details and return ONLY a JSON object with:
{
  "cve_id": "string",
  "severity": "Critical|High|Medium|Low",
  "exploitability": "High|Medium|Low",
  "impact": "string (2-3 sentences)",
  "recommendation": "string (specific action to take)"
}

Do NOT add any text outside the JSON object."""
```

**Fix 3: Add error handling**
```python
try:
    analysis = call_llm(prompt)
except Exception as e:
    return {
        "error_message": f"LLM failed: {e}",
        "status": "error"
    }
```

### Step 5: Rerun & Verify

```bash
python evals/run_evals.py --verbose 2>&1 | grep -A 5 "cve_impact"
```

If pass rate improved, move to next failing agent.

---

## Real-World Progression Example

### Starting Point: All Agents Below Target

```
Day 1: Initial State
├─ blog_scout:      40% (2/5)
├─ repo_onboarding: 60% (3/5)
├─ cve_impact:      30% (1/5)  ← Lowest, fix first
├─ issue_planner:   50% (2/4)
├─ do_i_care:       40% (2/5)
└─ opp_scout:       40% (2/5)
Overall: 43%
```

### After Fixing CVE Impact

```
Day 2: Fixed cve_impact (improved error handling + prompt)
├─ blog_scout:      40% (2/5)
├─ repo_onboarding: 60% (3/5)
├─ cve_impact:      80% (4/5)  ← ✅ Now passes target!
├─ issue_planner:   50% (2/4)
├─ do_i_care:       40% (2/5)
└─ opp_scout:       40% (2/5)
Overall: 52%
```

### After Fixing Blog Scout

```
Day 3: Fixed blog_scout (improved URL validation + fallback)
├─ blog_scout:      85% (4.25/5)  ← ✅ Passes target!
├─ repo_onboarding: 60% (3/5)
├─ cve_impact:      80% (4/5)  ✅
├─ issue_planner:   50% (2/4)
├─ do_i_care:       40% (2/5)
└─ opp_scout:       40% (2/5)
Overall: 59%
```

### After Fixing All Agents

```
Day 7: All agents ≥80%
├─ blog_scout:      85% (4.25/5)  ✅
├─ repo_onboarding: 80% (4/5)     ✅
├─ cve_impact:      80% (4/5)     ✅
├─ issue_planner:   80% (3.2/4)   ✅
├─ do_i_care:       80% (4/5)     ✅
└─ opp_scout:       85% (4.25/5)  ✅
Overall: 82% ✅ PRODUCTION READY!
```

---

## Common Quick Wins for Pass Rate Improvement

### Quick Win 1: Input Validation
```python
# Before: Crashes on empty input
def run_agent(query):
    return search(query)  # Crashes if query is ""

# After: Handles gracefully
def run_agent(query):
    if not query or not query.strip():
        return {"error_message": "Input required", "status": "error"}
    return search(query)

# Pass rate: +10% (typical)
```

### Quick Win 2: Add Fallback
```python
# Before: Fails if Tavily is down
results = search(query)

# After: Falls back to DuckDuckGo
try:
    results = search(query)
except Exception:
    results = search_fallback(query)

# Pass rate: +15% (typical)
```

### Quick Win 3: Better Error Messages
```python
# Before: Returns None
except Exception:
    return None

# After: Returns structured error
except Exception as e:
    return {
        "error_message": f"Search failed: {str(e)[:100]}",
        "status": "error"
    }

# Pass rate: +5% (typical, if tests expect error handling)
```

### Quick Win 4: Improve Prompt
```python
# Before: Generic prompt
SYSTEM_PROMPT = "You are a helpful assistant."

# After: Specific with guardrails
SYSTEM_PROMPT = """You are a blog idea scout.
Return ONLY a JSON array of blog ideas.
Each idea must have: title, pitch, source_url.
Do NOT hallucinate URLs."""

# Pass rate: +20% (typical, if tests check output format)
```

---

## Monitoring Pass Rates Over Time

Track progress in a table:

```
Date      | Overall | blog | repo | cve | issue | care | opp | Status
----------|---------|------|------|-----|-------|------|-----|--------
Day 1     | 43%     | 40%  | 60%  | 30% | 50%   | 40%  | 40% | ⚠️ Start
Day 2     | 52%     | 40%  | 60%  | 80% | 50%   | 40%  | 40% | 🔧 Working
Day 3     | 59%     | 85%  | 60%  | 80% | 50%   | 40%  | 40% | 🔧 Working
Day 4     | 65%     | 85%  | 80%  | 80% | 50%   | 40%  | 40% | 🔧 Working
Day 5     | 72%     | 85%  | 80%  | 80% | 80%   | 40%  | 40% | 🔧 Working
Day 6     | 77%     | 85%  | 80%  | 80% | 80%   | 85%  | 40% | 🔧 Almost there
Day 7     | 82%     | 85%  | 80%  | 80% | 80%   | 85%  | 85% | ✅ Done!
```

---

## FAQ: Pass Rates

**Q: Can I achieve 100% pass rate?**
A: Theoretically yes, but not practical. 85-90% is a good realistic target. 100% would mean edge cases never fail (impossible). Target 80%+ and accept that 1-2 tests may fail due to API variability.

**Q: What if an API is down? Will pass rate drop?**
A: Yes, temporarily. But fallback APIs should handle it. If fallback is also down, tests will fail. This is expected and acceptable—tests measure agent robustness.

**Q: Do I need to fix all agents to 80%?**
A: For production, yes. Deploy when all agents are ≥80%. Agents below 80% may have inconsistent behavior.

**Q: How do I handle flaky tests (sometimes pass, sometimes fail)?**
A: These indicate your agent has non-deterministic behavior. Likely causes:
- LLM response varies (normal) → accept 70-80% pass rate
- API responses vary (network) → add retry logic or fallback
- Timeout issues → increase timeouts

**Q: Should I modify test cases to make agents pass?**
A: No! Tests are your specification. Modify the agent, not the tests. If a test is wrong, improve the test case and keep the agent aligned with spec.

---

## Next Steps

1. **Run baseline:** `python evals/run_evals.py`
2. **Read failures:** `python evals/run_evals.py --verbose`
3. **Pick lowest agent:** Focus on the one with lowest pass rate
4. **Debug one test:** Run agent manually, inspect output
5. **Fix issue:** Update prompts, error handling, or parsing
6. **Retest:** Verify pass rate improved
7. **Iterate:** Repeat for all agents until all ≥80%

See [EVALUATION_GUIDE.md](./EVALUATION_GUIDE.md) for detailed step-by-step instructions.

---

**Status:** Ready to start improving! Run `python evals/run_evals.py` now to see your baseline.
