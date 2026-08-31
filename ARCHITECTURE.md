# Micro-Agents Architecture

A reference guide to the system design, data flow, and component interactions.

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Gradio Web Interface (app.py)                │
│                     (Multi-tab Dashboard)                       │
└──────────────────────┬──────────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
    ┌───▼────┐    ┌────▼────┐   ┌────▼────┐
    │ Agent 1 │    │ Agent 2 │   │ Agent 3 │  ... (6 agents)
    └───┬────┘    └────┬────┘   └────┬────┘
        │              │              │
        └──────────────┼──────────────┘
                       │
            ┌──────────▼──────────┐
            │   Core API Layer    │
            │  (core/*.py files)  │
            └──────────┬──────────┘
                       │
        ┌──────────────┼──────────────┬──────────────┐
        │              │              │              │
    ┌───▼────┐   ┌─────▼─────┐  ┌────▼────┐   ┌────▼────┐
    │  Groq  │   │  Tavily   │  │ GitHub  │   │    HF   │
    │  LLM   │   │ Search    │  │  REST   │   │Inference│
    └────────┘   └───────────┘  └────────┘   └────────┘
       (Primary)    (Primary)    (Primary)     (Fallbacks)
```

---

## Component Breakdown

### 1. Gradio Web Interface (app.py)

**Purpose:** Multi-tab UI for all 6 agents  
**Technology:** Gradio 4.x  
**Features:**
- Tab-based navigation (one tab per agent)
- Real-time input/output formatting
- Error handling and display
- No state persistence between tabs

**Key Functions:**
```python
blog_scout_tab(topic)              # Search + LLM for blog ideas
repo_onboarding_tab(repo_url)      # Onboarding guide generation
cve_impact_tab(query)              # Security impact analysis
issue_planner_tab(issue_url)       # Implementation planning
do_i_care_tab(headlines_text)      # Relevance scoring
opportunity_scout_tab(username)    # Career opportunity detection
security_audit_tab(repo_url)       # Security audit (extra agent)
```

---

### 2. Agent Layer (agents/*/logic.py)

Six specialized agents, each following a specific pattern:

#### Pattern Levels

| Level | Pattern | Steps | Example |
|-------|---------|-------|---------|
| **1** | Search + LLM | 2 | Blog Scout: `search(topic) → call_llm()` |
| **3** | Fixed Pipeline | 3 | Repo Onboarding: `fetch_repo() → search() → call_llm()` |
| **4** | ReAct + Guardrail | 4 | Issue Planner: loop with guardrail against code generation |
| **5** | Score → Filter → Analyze | 3 | Do I Care?: score all → filter top 3 → analyze |

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

**4. Issue Fix Planner (Level 4 - ReAct Loop)**
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
**Purpose:** Unified LLM interface (Groq primary + HF fallback)

```python
call_llm(
    prompt: str,
    system: str = None,
    model: str = "default",
    temperature: float = 0.7,
    max_tokens: int = 2048
) → str
```

**Flow:**
1. Try Groq API (primary)
2. On failure/timeout → fallback to HF Inference
3. Return LLM response

**Secrets:** `GROQ_API_KEY`, `HF_TOKEN` from `.env`

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

---

#### core/agent.py
**Purpose:** ReAct-pattern agent loop (used by Level 4+ agents)

```python
run_agent(
    task: str,
    tools: dict,  # {"tool_name": callable, ...}
    max_steps: int = 4,
    verbose: bool = False
) → str
```

**Loop:**
```
for step in range(max_steps):
    plan = call_llm(build_plan_prompt(task, history))
    if plan indicates completion:
        return plan.output
    tool_call = parse_tool_call(plan)
    result = tools[tool_call.name](*tool_call.args)
    history.append((plan, result))
return summarize_history(history)
```

**Key Properties:**
- Max 4 steps per task
- Tool calling via LLM (parse from response text)
- History tracking for context
- Optional verbose output

---

#### core/memory.py (Optional)
**Purpose:** Optional persistent memory layer for agents

```python
save_memory(key: str, value: dict) → None
load_memory(key: str) → dict
clear_memory() → None
```

**Usage:** Agents can optionally cache analysis results, search outputs, etc.

---

### 4. External APIs

#### Groq (Primary LLM)
- **Model:** llama2-70b-4096 or similar
- **Cost:** Free tier available
- **Rate Limit:** 30 calls/min on free tier
- **Fallback:** HF Inference if rate limited

#### Tavily (Search API)
- **Model:** Web search with AI summaries
- **Cost:** 1000 free searches/month
- **Fallback:** DuckDuckGo if quota exhausted

#### GitHub REST API v3
- **Rate Limit:** 60/hr (unauthenticated), 5000/hr (authenticated)
- **Costs:** Free, read-only operations
- **Use Cases:** fetch repos, issues, PRs, file trees

#### Hugging Face Inference API
- **Purpose:** Fallback LLM if Groq fails
- **Model:** Choices vary (mistral, llama, etc.)
- **Cost:** Free tier available
- **Rate Limit:** ~8 req/min on free tier

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
    │   ├─→ Try Groq API
    │   └─→ Returns LLM-generated ideas
    │
    └─→ Output to Gradio
        Display: "### Idea 1: ... with source link"
```

### Example 2: Issue Fix Planner (ReAct Loop)

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
    │   └─→ "Likely files: core/agent.py, agents/blog_scout/logic.py"
    │
    ├─→ Step 4: core/llm.py::call_llm("Implementation plan?")
    │   └─→ "Plan: 1. Modify core/agent.py to... 2. Add tests..."
    │   └─→ GUARDRAIL: No code snippets generated
    │
    └─→ Output to Gradio
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
- Groq → HF Inference (LLM)
- Tavily → DuckDuckGo (Search)
- Authenticated → Unauthenticated (GitHub)
- Ensures agents work even when primary services fail

### 4. **No Persistence**
- Each run is independent
- No database, cache, or session state
- Results live only in the current execution
- Optional: `core/memory.py` for agent-level caching (not used by default)

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
   - `logic.py` — agent logic (imports from `core/`)
   - `app.py` — Gradio interface
   - `evals.jsonl` — test cases (3-5 per agent)
3. **Add to dashboard:** Import in root `app.py`, add tab
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
| **Gradio UI** | ~100ms | N/A | Interface overhead |
| **LLM (Groq)** | 1-5s | 30 req/min | Primary bottleneck |
| **LLM (HF Fallback)** | 3-10s | 8 req/min | Slower but free |
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
- **GROQ_API_KEY:** Inference only (read-only model access)
- **TAVILY_API_KEY:** Search only (no write permissions)
- **GITHUB_TOKEN:** Optional, `public_repo` scope recommended (read-only)
- **HF_TOKEN:** Inference only

### Deployment Security
- Secrets stored in HF Space UI (Settings → Secrets), not in code
- `.env.example` provided (no secrets, only keys listed)
- Pre-deployment: `git log -p | grep -i api_key` should return nothing

---

## Monitoring & Debugging

### Logging
- Each agent has `logger = logging.getLogger(__name__)`
- Logs are printed to stdout (no persistent logging)
- Use `verbose=True` in `core/agent.py` for detailed tracing

### Error Handling
- Graceful degradation: fallback APIs kick in on primary failure
- Errors returned in response dict (e.g., `{"error_message": "...", "status": "error"}`)
- UI displays errors with ❌ emoji prefix

### Testing
- Smoke tests: `if __name__ == "__main__"` in each module
- Evaluation tests: `evals/run_evals.py` runs all agents against test cases
- No pytest (keep it simple)

---

## Future Enhancements (Out of Scope)

- 🔴 Multi-agent collaboration / handoff
- 🔴 Persistent memory / database
- 🔴 Scheduled/cron jobs
- 🔴 Rate limiting / quota management
- 🔴 Advanced logging / telemetry
- 🔴 User authentication
- 🔴 Async/parallel execution
- 🔴 Advanced Gradio UI features (state, progress bars, etc.)

These are explicitly excluded per design spec §8 (constraints).

---

## References

- **Design Spec:** [micro-agents-design-spec.md](./micro-agents-design-spec.md)
- **Deployment:** [deployment.md](./deployment.md)
- **README:** [README.md](./README.md)
- **External APIs:**
  - Groq: https://console.groq.com/docs
  - Tavily: https://tavily.com
  - GitHub: https://docs.github.com/en/rest
  - Hugging Face: https://huggingface.co/inference-api
