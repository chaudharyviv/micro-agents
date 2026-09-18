# Micro-Agents Architecture

A reference guide to the system design, data flow, and component interactions.

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                  Streamlit Web Interface (app.py)                │
│              (per-agent tabs  +  Orchestrator tab)                │
└───────────────┬─────────────────────────────┬─────────────────────┘
                │                             │
   direct call  │                             │  free-text task
   (single-agent tab)                         │
                │                    ┌─────────▼─────────┐
                │                    │    ORCHESTRATOR     │
                │                    │ agents/orchestrator │
                │                    │  runs on the agent  │
                │                    │  harness (below) -  │
                │                    │  no fixed pipeline   │
                │                    └─────────┬─────────┘
                │                             │ model decides which 1-2
                │              ┌──────────────┼──────────────┐
                │              │              │              │
        ┌───────▼───────┬──────▼──────┬───────▼──────┬───────────────┐
        │  Blog Scout    │ Repo Onboard│ CVE Impact   │  ... (7 total) │
        │  logic.py      │ logic.py    │ logic.py     │  specialist    │
        └───────┬───────┴──────┬──────┴───────┬──────┴───────────────┘
                │              │              │
                └──────────────┼──────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   Core API Layer    │
                    │  (core/llm.py, core/search.py, core/github_tool.py) │
                    └──────────┬──────────┘
                               │
                ┌──────────────┼──────────────┐
                │              │              │
            ┌───▼────┐   ┌─────▼─────┐  ┌────▼────┐
            │ OpenAI │   │  Tavily   │  │ GitHub  │
            │  LLM   │   │ Search    │  │  REST   │
            └────────┘   └───────────┘  └────────┘
            (retried once)  (Primary)    (Primary)
                          DuckDuckGo fallback
```

The orchestrator is the key agentic layer, and it's the one agent that runs on a real **harness** (`core/harness.py`) instead of a hand-coded pipeline: each of the 7 specialists is exposed to the model as an OpenAI function-calling tool, and the model itself decides which 1-2 tools to call, with what input, and when it has enough to write a synthesized Markdown report — the harness only supplies the tool registry, the execution loop, and guardrails (step limit, per-tool error isolation, and a grounding check that blocks a tool call whose input doesn't actually appear in the user's task text, as a defense against prompt injection from earlier tool results). See [core/harness.py](#coreharnesspy) below.

---

## Component Breakdown

### 1. Streamlit Web Interface (app.py)

**Purpose:** Multi-tab UI for all 7 agents
**Technology:** Streamlit >=1.57
**Features:**
- Tab-based navigation (`st.tabs`, one tab per agent)
- Each tab is a form (`st.form`) so input only submits on button click, not on every keystroke
- Shared `render_agent_tab()` helper wires up the form, spinner, and Markdown output for every tab
- Error handling and display via `st.warning` (missing input) and formatted Markdown (agent errors)
- No state persistence between tabs

**Key Functions:**
```python
render_agent_tab(form_key, label, placeholder, button_label, run, format_result, ...)
# Generic tab renderer, parameterized per agent:
scout_blog_ideas(topic)              # Search + LLM for blog ideas
generate_onboarding_guide(repo_url)  # Onboarding guide generation
analyze_cve_impact(query)            # Security impact analysis
run_issue_fix_planner(issue_url)     # Implementation planning
run_do_i_care(headlines)             # Relevance scoring
run_opportunity_scout(username)      # Career opportunity detection
generate_security_audit(repo_url)    # Security audit
```

---

### 2. Agent Layer (agents/*/logic.py)

Seven specialized agents, each following a specific pattern, plus an orchestrator that routes to them:

#### Pattern Levels

| Level | Pattern | Steps | Example |
|-------|---------|-------|---------|
| **1** | Search + LLM | 2 | Blog Scout: `search(topic) → call_llm()` |
| **3** | Fixed Pipeline | 3 | Repo Onboarding: `fetch_repo() → search() → call_llm()` |
| **4** | Multi-step fixed pipeline + guardrail | 4 | Issue Planner: sequential steps with a guardrail against code generation |
| **5** | Score → Filter → Analyze, or combine other agents | 3 | Do I Care?: score all → filter top 3 → analyze |

#### Agent Descriptions

**1. Blog Idea Scout (Level 1)**
```
Input:  topic (string)
        ↓
    search(topic, max_results=5)
        ↓
    call_llm(results) 
        ↓
Output: list[dict] with title, pitch, source_url
```

**2. Repo Onboarding (Level 3)**
```
Input:  repo_url (GitHub URL)
        ↓
    fetch_repo(url) → get README, file tree, metadata
        ↓
    search(repo_name + "tutorial") → find related tutorials
        ↓
    call_llm(all_data) → generate guide
        ↓
Output: dict with project_name, overview, setup_steps, etc.
```

**3. CVE Impact (Level 3)**
```
Input:  cve_id or software_name
        ↓
    search(CVE details) → get vulnerability info
        ↓
    call_llm(search_results) → assess impact
        ↓
Output: dict with severity, exploitability, recommendations
```

**4. Issue Fix Planner (Level 4 - multi-step fixed pipeline)**
```
Input:  issue_url (GitHub issue)
        ↓
    Step 1: fetch_issue(url) → get issue title/body
    Step 2: search(repo + issue keywords) → find related files
    Step 3: call_llm("What files?") → identify affected files
    Step 4: call_llm("Generate plan") → create implementation plan
        ↓
Output: dict with plan text (150-400 words, NO CODE)
        
Guardrail: System prompt explicitly forbids code generation
```

**5. Do I Care? (Level 5 - Relevance Scoring)**
```
Input:  headlines list, user_profile
        ↓
    Step 1: call_llm("Score each 1-10") → relevance scores
    Step 2: Filter → keep top 3
    Step 3: call_llm("Why & actions?") → detailed analysis
        ↓
Output: list[dict] with rank, headline, why_it_matters, action
```

**6. Opportunity Scout (Level 5 - Analysis + Insights)**
```
Input:  github_username
        ↓
    Step 1: search(username) → find GitHub presence
    Step 2: call_llm("Job market trends") → analyze market
    Step 3: call_llm("Opportunities") → identify gaps & opportunities
        ↓
Output: dict with skill_gaps, job_suggestions, project_idea
```

---

### 3. Core API Layer (core/*.py)

Shared modules used by all agents. No duplication—each agent imports from core/.

#### core/llm.py
**Purpose:** Unified LLM interface (OpenAI, retried once on failure)

```python
call_llm(
    prompt: str,
    system: str = None,
    model: str = "default"
) → str
```

**Flow:**
1. Call OpenAI API (`gpt-4o-mini`)
2. On failure → retry once
3. Return LLM response, or raise `LLMUnavailableError` if both attempts fail

**Secrets:** `OPENAI_API_KEY` from `.env`

---

#### core/search.py
**Purpose:** Unified search interface (Tavily primary + DuckDuckGo fallback)

```python
search(
    query: str,
    max_results: int = 5
) → list[dict]  # [{"title": str, "url": str, "snippet": str}, ...]
```

**Flow:**
1. Try Tavily API (primary, 1000 searches/month free)
2. On failure/quota → fallback to DuckDuckGo (no key required)
3. Return results

**Secrets:** `TAVILY_API_KEY` from `.env`

---

#### core/github_tool.py
**Purpose:** GitHub REST API wrapper (unauthenticated + authenticated fallback)

```python
fetch_repo(repo_url: str) → dict
  # Returns: name, description, language, stars, file_tree, readme

fetch_issue(issue_url: str) → dict
  # Returns: title, body, state, created_at, comments_count

fetch_pr(pr_url: str) → dict
  # Returns: title, body, state, created_at, merged_at
```

**Rate Limits:**
- Unauthenticated: 60 req/hr
- Authenticated: 5000 req/hr (if `GITHUB_TOKEN` set)

**Secrets:** `GITHUB_TOKEN` (optional)

**Rate limit awareness:** caches the `X-RateLimit-Remaining`/`X-RateLimit-Reset` headers GitHub returns
and fails fast with a clear `GitHubAPIError` when the budget is nearly exhausted, instead of silently
burning through it mid-request. Also logs a one-time warning if `GITHUB_TOKEN` reports OAuth scopes
broader than the recommended read-only `public_repo`.

---

#### core/harness.py
**Purpose:** A minimal, from-scratch tool-calling agent harness (no LangChain/LangGraph) built on
OpenAI's native function calling. "Agent = Model + Harness": the model supplies judgment, this module
supplies everything else.

```python
Tool(name, description, parameters, run, validate=None)
  # validate(args, task) -> bool: optional per-call guardrail; a `False` blocks execution
  # without spending a real tool call, e.g. to reject an ungrounded input

run_harness(task: str, tools: list[Tool], system_prompt: str, max_steps: int = 4) → HarnessResult
  # HarnessResult: {"final_message": str, "tool_calls": [...], "steps_used": int}
```

**Flow:**
1. The model sees the task and the tool registry (as OpenAI function schemas) and decides, step by
   step, whether to call a tool or answer directly - the harness never hardcodes which tool runs when
2. Each requested tool call is validated (if a `validate` hook is set), executed, and its result fed
   back to the model; a failing tool is caught and reported as data, not raised, so one bad call
   doesn't abort the run
3. The loop ends when the model responds without requesting a tool call, or after `max_steps` - at the
   step limit the model is asked for a best-effort final answer using only what it already gathered

This replaces the project's earlier `core/agent.py` ReAct-loop prototype, which was unused by any agent,
had a bug where parsed tool arguments were silently discarded, and was removed.

---

#### agents/orchestrator/logic.py
**Purpose:** Routes a free-text task to 1-2 specialist agents and synthesizes their results, running
entirely on `core/harness.py`

```python
run_orchestrator(task: str) → dict
  # Returns: {"report": str, "specialists_used": [...], "status": "success"|"error"}
```

**Flow:**
1. Each of the 7 specialists is wrapped as a `Tool` (single `input: string` argument) and registered
   with the harness alongside a system prompt describing the orchestrator's job
2. Every tool's `validate` hook is `_input_matches_task()`, which sanity-checks the model's extracted
   input against the original task text - this blocks a redirected target introduced by prompt
   injection in content a tool fetched earlier in the same run
3. `run_harness()` runs the loop (capped at 3 steps): the model picks 1-2 tools, sees their results,
   and writes its own synthesized Markdown report as the final (non-tool-call) message
4. `run_orchestrator()` translates the harness's `HarnessResult` back into the app's existing
   `{report, specialists_used, status}` contract, so `app.py` and the eval suite didn't need to change

---

#### core/memory.py
**Purpose:** The "memory" in agent = LLM + tools + memory. In-process only, bounded, no filesystem -
safe on shared, ephemeral hosts (Streamlit Cloud) where disk is shared by all visitors and wiped on restart.

```python
SessionMemory(max_entries=20)       # one visitor's run history: record / recent / has_input / clear
ResultCache(ttl_seconds, max_entries)  # shared LRU + TTL, thread-safe: get / put
run_with_memory(agent, input, fn, cache, session, before_run) → (output, cache_hit)
```

**Two tiers, kept separate on purpose:**
- **Session memory** - private to a visitor's session (`st.session_state`). Every agent run is recorded
  here; the orchestrator's `recall_memory` tool reads it so follow-ups ("do the same for the second
  repo") work.
- **Shared result cache** - one instance for all sessions (`st.cache_resource`), only for agents whose
  output depends solely on a public URL (`CACHEABLE_AGENTS`: repo_onboarding, cve_impact,
  security_audit, issue_fix_planner). Errors are never cached. On a hit the agent doesn't run, and
  `before_run` (the rate limiter) isn't consulted, so hits don't cost the visitor a request.

**Security notes:** recalled memory is treated as untrusted data in the orchestrator prompt (it
originates from public content). Follow-up tool calls may reuse an input the visitor already ran this
session (`SessionMemory.has_input`), but any other input must still appear in the task text.

---

### 4. External APIs

#### OpenAI (LLM)
- **Model:** `gpt-4o-mini`
- **Cost:** Pay-as-you-go, cheapest current chat-completion tier
- **Retry:** One retry on failure before raising `LLMUnavailableError`

#### Tavily (Search API)
- **Model:** Web search with AI summaries
- **Cost:** 1000 free searches/month
- **Fallback:** DuckDuckGo if quota exhausted

#### GitHub REST API v3
- **Rate Limit:** 60/hr (unauthenticated), 5000/hr (authenticated)
- **Costs:** Free, read-only operations
- **Use Cases:** fetch repos, issues, PRs, file trees

---

## Data Flow Examples

### Example 1: Blog Idea Scout

```
User Input: "machine learning"
    │
    ├─→ agents/blog_scout/logic.py::scout_blog_ideas()
    │
    ├─→ core/search.py::search("machine learning", max_results=5)
    │   ├─→ Try Tavily API
    │   └─→ [{"title": "...", "url": "...", "snippet": "..."}, ...]
    │
    ├─→ core/llm.py::call_llm(
    │       prompt="Given these search results, suggest 5 blog ideas...",
    │       system="You are a blog idea scout..."
    │   )
    │   ├─→ Call OpenAI API (gpt-4o-mini)
    │   └─→ Returns LLM-generated ideas
    │
    └─→ Output to Streamlit
        Display: "### Idea 1: ... with source link"
```

### Example 2: Issue Fix Planner (multi-step fixed pipeline)

```
User Input: "https://github.com/user/repo/issues/123"
    │
    ├─→ agents/issue_fix_planner/logic.py::run_issue_fix_planner()
    │
    ├─→ Step 1: core/github_tool.py::fetch_issue(url)
    │   └─→ {"title": "Bug: ...", "body": "...", ...}
    │
    ├─→ Step 2: core/search.py::search("Bug keywords")
    │   └─→ Related discussions, docs, solutions
    │
    ├─→ Step 3: core/llm.py::call_llm("What files affected?")
    │   └─→ "Likely files: agents/issue_fix_planner/logic.py, core/github_tool.py"
    │
    ├─→ Step 4: core/llm.py::call_llm("Implementation plan?")
    │   └─→ "Plan: 1. Modify agents/issue_fix_planner/logic.py to... 2. Add tests..."
    │   └─→ GUARDRAIL: No code snippets generated
    │
    └─→ Output to Streamlit
        Display: "# Implementation Plan\n## Files to Touch\n..."
```

---

## Architecture Principles

### 1. **No Framework Dependency**
- No LangGraph, LangChain, or other orchestration frameworks
- Pure Python with simple function calls
- Easier to understand, modify, and debug

### 2. **Composable Core APIs**
- Each agent uses `core/llm.py`, `core/search.py`, `core/github_tool.py`
- New agents can be added by combining existing core functions
- Core functions evolve to support new agent patterns

### 3. **Graceful Fallbacks**
- OpenAI call retried once (LLM)
- Tavily → DuckDuckGo (Search)
- Authenticated → Unauthenticated (GitHub)
- Ensures agents work even when a primary service hiccups

### 4. **No Persistence**
- No database and no files; memory is in-process only (`core/memory.py`) and bounded
- Per-session run history (never shared) + a shared TTL'd result cache for public-URL agents
- Nothing survives a restart, by design (safe on shared, ephemeral hosts like Streamlit Cloud)

### 5. **Evaluation-Driven**
- Each agent has `evals.jsonl` with test cases
- `evals/run_evals.py` runs all evaluations and reports pass rate
- Proof of functionality is in the evals output

---

## Scaling Considerations

### Adding a New Agent

1. **Create directory:** `agents/my_agent/`
2. **Implement files:**
   - `prompts.py` — system & user prompts
   - `logic.py` — agent logic (imports from `core/`, smoke test in `__main__`)
   - `evals.jsonl` — test cases (3-5 per agent)
3. **Add to dashboard:** Import in root `app.py`, add a tab via `render_agent_tab()`
4. **Test:** Run `logic.py` smoke test, add evals

### Extending Core APIs

If multiple agents need a new capability:

1. **Extend `core/`** with a new module (e.g., `core/my_tool.py`)
2. **Use in agents:** `from core.my_tool import function`
3. **No duplication:** Never implement in the agent directly if it's reusable

---

## Performance Characteristics

| Component | Latency | Throughput | Notes |
|-----------|---------|-----------|-------|
| **Streamlit UI** | ~100ms | N/A | Interface overhead + rerun on submit |
| **LLM (OpenAI gpt-4o-mini)** | 1-5s | Depends on account tier | Primary bottleneck; +1 attempt if retried |
| **Search (Tavily)** | 500-2000ms | 1000/month | Cached web search |
| **Search (DuckDuckGo)** | 1-3s | Unlimited | Fallback, slower |
| **GitHub API** | 200-800ms | 60-5000 req/hr | Depends on auth |
| **Full Agent Run** | 5-30s | ~5 per minute | Sum of above + parsing |

---

## Security

### Secrets Management
- `.env` file (never committed, listed in `.gitignore`)
- Loaded via `python-dotenv` at startup
- Never logged or printed
- Fallback APIs don't require keys

### API Key Scopes
- **OPENAI_API_KEY:** Inference only (read-only model access)
- **TAVILY_API_KEY:** Search only (no write permissions)
- **GITHUB_TOKEN:** Optional, `public_repo` scope recommended (read-only)

### Deployment Security
- Secrets stored in HF Space UI (Settings → Secrets), not in code
- `.env.example` provided (no secrets, only keys listed)
- Pre-deployment: `git log -p | grep -i api_key` should return nothing

---

## Monitoring & Debugging

### Logging
- Each agent has `logger = logging.getLogger(__name__)`
- Logs are printed to stdout (no persistent logging)
- User-facing error messages are generic; full exception detail is only in the server-side logs

### Error Handling
- Graceful degradation: fallback APIs kick in on primary failure
- Errors returned in response dict (e.g., `{"error_message": "...", "status": "error"}`)
- UI displays errors with a `:material/error:` icon prefix; missing input shows an `st.warning`

### Testing
- Smoke tests: `if __name__ == "__main__"` in each module
- Evaluation tests: `evals/run_evals.py` runs all agents against test cases
- Unit tests: `pytest` (mocked API calls) covering `core/` and each agent's `logic.py`

---

## Future Enhancements (Out of Scope)

- ✅ ~~Multi-agent collaboration / handoff~~ - added via `agents/orchestrator/`
- ✅ ~~Rate limiting / quota management~~ - partial: GitHub rate-limit-budget awareness
  (`core/github_tool.py`) and a per-session cooldown/cap in the Streamlit UI (`app.py`); no
  account-level spend cap or real auth
- ✅ ~~Memory~~ - in-process session memory + shared result cache (`core/memory.py`)
- 🔴 Durable memory / database
- 🔴 Scheduled/cron jobs
- 🔴 Advanced logging / telemetry
- 🔴 User authentication
- 🔴 Async/parallel execution
- 🔴 Advanced Streamlit UI features (progress bars, fragments, etc.)

These are explicitly excluded per the design philosophy (see [DESIGN.md](./DESIGN.md)), except where
marked ✅ above.

---

## References

- **Design:** [DESIGN.md](./DESIGN.md)
- **README:** [README.md](./README.md)
- **External APIs:**
  - OpenAI: https://platform.openai.com/docs
  - Tavily: https://tavily.com
  - GitHub: https://docs.github.com/en/rest
