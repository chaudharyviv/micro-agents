# Micro-Agents: Design & Philosophy

Core design decisions and philosophy behind the micro-agents framework.

---

## Mission

Build **lightweight, specialized AI agents** for distinct tasks using OpenAI's `gpt-4o-mini`, deployable as a single Streamlit app without external frameworks or persistent infrastructure.

**Core Constraint:** No LangGraph, no LangChain, no databases, no complex orchestration.

---

## Design Principles

### 1. Simplicity Over Abstraction

**Decision:** Pure Python functions, not framework-based agents.

**Why:**
- LangGraph/LangChain add 90% complexity agents don't need
- Direct function calls are easier to debug and reason about
- Each agent is ~200 lines of logic.py
- New team members understand the codebase in 1 hour

**Trade-off:** No multi-agent routing, no complex memory management—but that's intentional.

---

### 2. Composable Core APIs

**Decision:** Shared `core/` modules (llm, search, github_tool) used by all agents.

**Why:**
- No duplicated API calls across agents
- Evolution of core APIs benefits all agents immediately
- Adding a new agent = combining existing core functions
- Easy to extend with new capabilities

**Pattern:**
```python
# ✅ Good: Use core APIs
from core.llm import call_llm
from core.search import search

# ❌ Bad: Inline API calls
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
```

---

### 3. Graceful Fallbacks

**Decision:** Primary + fallback for each external API.

| API | Primary | Fallback | When to Fallback |
|-----|---------|----------|------------------|
| **LLM** | OpenAI (`gpt-4o-mini`) | Retry once, same provider | Transient error / timeout |
| **Search** | Tavily | DuckDuckGo | Quota exceeded |
| **GitHub** | Unauth | Auth (token) | Rate limit hit |

**Why:**
- Agents work even when primary service fails
- No single point of failure
- Graceful degradation (slower, not broken)
- Easier to test locally without all keys

---

### 4. Pattern-Based Agent Design

**Decision:** Agents follow one of 5 defined patterns (Level 1–5).

| Level | Pattern | Steps | Example | Complexity |
|-------|---------|-------|---------|-----------|
| **1** | Search → LLM | 2 | Blog Scout | ⭐ |
| **3** | Fixed pipeline | 3 | Repo Onboarding | ⭐⭐ |
| **4** | Multi-step fixed pipeline + guardrail | 4 | Issue Planner | ⭐⭐ |
| **5** | Score → filter → analyze | 3 | Do I Care? | ⭐⭐ |

**Why:**
- Clear structure for new agents
- Predictable complexity growth
- Easier to explain and teach
- Evals map naturally to patterns

---

### 5. Evaluation-First Development

**Decision:** Every agent ships with `evals.jsonl` (3–5 test cases) + pass-rate reporting.

**Why:**
- "How do we know it works?" → Run `evals/run_evals.py`
- Evals catch regressions immediately
- Pass rate is the source of truth for readiness
- No mystery failures in production

**Format:**
```jsonl
{"input": "...", "expected_behavior": "...", "check": "exact_match|keyword|llm_graded"}
```

---

### 6. No Persistence = Simple Deployment

**Decision:** Each run is independent; no database, cache, or session state.

**Why:**
- Stateless apps are trivial to scale (HF Spaces CPU Basic works)
- No migrations, schema changes, or data cleanup
- Easier to reason about behavior (input → output, no side effects)
- Each user gets the same fresh execution

**Trade-off:** Can't remember user preferences or past results—but that's acceptable for this use case.

---

### 7. Transparent Cost Model

**Decision:** Use APIs with clear, predictable pricing.

| Service | Free Tier | Cost | Use Case |
|---------|-----------|------|----------|
| OpenAI | No | Pay-as-you-go (`gpt-4o-mini` is the cheapest chat tier) | LLM |
| Tavily | 1000 searches/month | $5/month after | Search |
| GitHub | 60 req/hr unauth | Free | Repo fetching |
| Streamlit | Community Cloud free tier | Free (with limits) | Hosting |

**Why:**
- No surprise bills
- Can prototype on free tier
- Easy to estimate production cost
- Encourages minimal API usage

---

## Architectural Decisions

### Why No LangGraph?

| Aspect | LangGraph | Micro-Agents |
|--------|-----------|--------------|
| Lines of code per agent | 300–500 | 150–200 |
| Learning curve | Steep (composable nodes, edges) | Flat (just Python functions) |
| Debugging | Requires graph visualization tools | Print statements, log files |
| Multi-agent routing | Native support | Not needed for use case |
| Dependency weight | ~50MB | ~1MB |

**Decision:** For 6 specialized agents, LangGraph is overkill. Function composition is simpler.

---

### Why No Database?

| Aspect | With Database | Stateless |
|--------|---------------|-----------|
| Deployment | Requires managed DB, migrations | Single Docker container |
| Scaling | Connection pooling, caching layer needed | Trivial, just spawn more instances |
| Complexity | Schema design, data cleanup, backups | None |
| Use case fit | Multi-user with historical data | Single-user or transient results |

**Decision:** Agents are compute-bound, not data-bound. Stateless is simpler and fits the use case.

---

### Why Pattern Levels (1–5)?

**Decision:** Define 5 agent complexity levels instead of letting each agent reinvent the wheel.

**Levels:**
1. **Level 1:** Search + LLM (no loop, no state)
2. **Level 3:** Fixed pipeline (N tool calls in sequence, then synthesize)
3. **Level 4:** Multi-step fixed pipeline with a guardrail (4 sequential steps, no dynamic looping)
4. **Level 5:** Scoring + filtering (multi-step with intermediate decisions)

**Why:**
- Guides design of new agents
- Evals match patterns (scoring agents test accuracy, planning agents test guardrails)
- Team can estimate complexity upfront
- Natural progression of sophistication

---

### Why Streamlit (Migrated from Gradio)?

| Aspect | Gradio | Streamlit |
|--------|--------|----------|
| Setup | `gr.Interface` or `gr.Blocks` | Plain script, reruns top-to-bottom on interaction |
| Multi-tab UI | Native (`gr.Tabs`) | Native (`st.tabs`) |
| Batched input | Manual `.click()` wiring per widget | `st.form` batches inputs, submits once |
| Input types | Flexible (Textbox, File, Slider, etc.) | Equally flexible; reruns are cheap since forms gate expensive work |
| Hosting | HF Spaces (free) | HF Spaces (`space-sdk: streamlit`) or Streamlit Community Cloud (free) |
| Philosophy | Functional I/O (input → function → output) | Reactive script reruns, gated with `st.form` to stay functional per submit |

**Decision:** The project originally chose Gradio for its clean functional I/O model. It migrated to Streamlit because `st.form` + `st.tabs` give the same input → function → output feel per tab, while Streamlit's wider native widget set and theming (`.streamlit/config.toml`) make it easier to extend the dashboard as more agents are added.

---

## Testing Strategy

**Decision:** Three layers - smoke tests, `pytest` unit tests (mocked API calls), and evals (real API calls).

**Why:**
- Smoke tests (`if __name__ == "__main__"`) give fast manual verification per module
- `pytest` (in `tests/`) covers parsing, error handling, and control flow with mocked APIs, cheaply and deterministically - this is what runs before every change
- Evals (`evals/run_evals.py`) are the integration layer: real API calls, real-world pass rate
- Mocked unit tests and real-call evals catch different classes of bugs; neither replaces the other

**Trade-off:** More to maintain than a single layer, but each layer is cheap and catches something the others don't.

---

## Security Model

### No Secrets in Code

**Decision:** All secrets from `.env` (via `python-dotenv`), never hardcoded.

```python
# ✅ Good
api_key = os.getenv("OPENAI_API_KEY")

# ❌ Bad
api_key = "sk-12345..."
```

### Deployment Secrets

**Decision:** Secrets stored in HF Space UI (Settings → Secrets), not in `.env` file.

1. Deploy: Push code to Space (`.env` not committed)
2. Set secrets in HF UI (not in repo)
3. HF injects them as env vars at runtime

### Scope Minimization

- **OPENAI_API_KEY:** Inference only
- **TAVILY_API_KEY:** Search only
- **GITHUB_TOKEN:** Read-only (`public_repo` scope)

---

## Operational Decisions

### Local-First Workflow

**Decision:** Agents run identically locally and on HF Spaces.

```bash
# Local: Direct Streamlit execution
streamlit run app.py

# Spaces: Same code, secrets injected by platform
# No code differences
```

**Why:**
- Test before deploying
- Easier to debug (full logs, breakpoints)
- No environment-specific bugs

---

### No Logging Infrastructure

**Decision:** Print to stdout (development) + error dicts in responses (production).

```python
# Development
logger.info(f"Processing {item}...")

# Production
return {
    "status": "error",
    "error_message": "Failed to fetch repo: rate limit"
}
```

**Why:**
- Streamlit and HF Spaces capture stdout
- Error dicts are returned to user
- No need for external logging service (Sentry, DataDog, etc.)

---

### Evaluation Reporting

**Decision:** `evals/run_evals.py` outputs a table; results pasted into README.

```bash
$ python evals/run_evals.py

| Agent | Pass Rate | Failures |
|-------|-----------|----------|
| blog_scout | 80% | 1 timeout |
| repo_onboarding | 90% | 0 |
| ...
```

**Why:**
- Human-readable pass rate
- Easy to share (paste into README, docs, etc.)
- No external dashboard or CI dependency

---

## What This Framework Does NOT Do

By design, the following are out-of-scope:

### ✅ Multi-Agent Collaboration (added)
Originally out of scope ("increases complexity 10x, not needed for independent agents"). Added via
`agents/orchestrator/`, which routes a free-text task to 1-2 specialist agents and synthesizes their
results. It's deliberately minimal:
- No shared mutable state between agents - the orchestrator calls each specialist's `logic.py`
  function directly and passes plain strings/dicts, nothing more
- No dynamic multi-step handoff chains - at most one routing decision, then a fixed 1-2 specialist
  calls, then one synthesis call
- The routing decision is grounded against the original task text before execution, as a guard
  against a target substituted by prompt injection in content a specialist fetched earlier

**Why now:** A single, bounded orchestrator (one routing call + direct function calls) is a small,
auditable addition, not the open-ended agent-to-agent framework this section originally ruled out.

### ❌ Persistent Memory (durable storage)
- No database and no files; nothing survives a restart
- Memory is in-process only (`core/memory.py`): a per-session run history plus a bounded, TTL'd
  result cache shared across visitors for public-URL agents

**Why:** A database or on-disk store adds deployment complexity and, on shared/ephemeral hosts like
Streamlit Cloud, leaks results between visitors and disappears on restart. In-process memory gives
the "agent = LLM + tools + memory" behavior (follow-ups, instant repeats) without those risks.

### ❌ Async / Parallel Execution
- No `asyncio`, no `concurrent.futures`
- All API calls are synchronous
- No parallel tool calling

**Why:** Adds debugging difficulty. Not needed for single-user app.

### ❌ Advanced Streamlit Features
- No session state management
- No progress bars / streaming output
- No file uploads (except URLs as text)

**Why:** Increases code complexity. Not essential for use case.

### ❌ Rate Limiting / Quota Management
- No built-in rate limiter
- No usage tracking per user
- Rely on external API limits

**Why:** Single-user app. Not needed for Spaces deployment.

### ❌ CI/CD & Automated Testing
- No GitHub Actions
- No automated deployments
- Manual push to HF Space (`git push space main`)

**Why:** Small team, fast iteration. Manual works fine.

---

## Iteration Patterns

### Adding an Agent

1. **Copy template:** `agents/blog_scout/` → `agents/my_agent/`
2. **Edit prompts.py:** Define system prompt
3. **Implement logic.py:** Core agent logic (imports from `core/`, smoke test in `__main__`)
4. **Add evals.jsonl:** 3–5 test cases
5. **Update root app.py:** Add a tab via `render_agent_tab()` + import
6. **Test:** Run logic.py smoke test, run evals
7. **Done:** Agent is ready to deploy

### Adding a Core API

1. **Check:** Does this capability already exist in `core/`?
2. **If no:** Create `core/new_tool.py` with function + smoke test
3. **Use in agent:** `from core.new_tool import function`
4. **Test:** Run agent logic smoke test
5. **Done:** Other agents can now import it

### Deploying to HF Spaces

1. **Local test:** `streamlit run app.py`, verify all tabs work
2. **Git commit:** `git add -A && git commit -m "..."`
3. **Push code:** `git push space main`
4. **Set secrets:** HF Space UI → Settings → Secrets
5. **Verify:** Navigate to Space, test each tab
6. **Done:** Live!

---

## Performance Targets

| Metric | Target | Notes |
|--------|--------|-------|
| **Page load** | < 2s | Streamlit UI + HF Space startup |
| **Agent run** | 5–30s | Sum of API calls + parsing |
| **Search** | < 3s | Tavily or DuckDuckGo |
| **LLM inference** | 1–5s | OpenAI `gpt-4o-mini`, +1 attempt if retried |
| **Error recovery** | < 2s | Retry / fallback activation |
| **Throughput** | ~5 runs/min | Bound by OpenAI account rate limit with multi-step agents |

---

## Philosophy Summary

```
┌─────────────────────────────────────────┐
│  Micro-Agents Design Philosophy         │
├─────────────────────────────────────────┤
│                                         │
│  "Do ONE thing, do it SIMPLY, do it    │
│   WELL, and make it EASY to extend."   │
│                                         │
│  ✅ 6 specialized agents                │
│  ✅ Clear, predictable patterns         │
│  ✅ Graceful fallbacks                  │
│  ✅ Easy to add agents                  │
│  ✅ Evaluation-driven                   │
│  ✅ Deployed with zero infrastructure   │
│                                         │
└─────────────────────────────────────────┘
```

---

## References

- **Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md)
- **Implementation:** [README.md](./README.md)
