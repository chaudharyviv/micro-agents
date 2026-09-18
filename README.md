# micro-agents

Seven specialist AI agents plus an orchestrator, powered by OpenAI's gpt-4o-mini.

## Overview

Micro-Agents is a lightweight framework for building specialized AI agents without external dependencies like LangGraph, LangChain, or persistent databases. Each agent follows a specific pattern and uses core APIs for LLM, search, and GitHub interactions. An orchestrator agent routes free-text tasks to 1-2 specialists and synthesizes their results.

See [Deployment](#deployment) for instructions to run this locally or deploy your own instance.

---

## Agents

| Agent | Pattern | Input | Output | Complexity |
|-------|---------|-------|--------|------------|
| **Orchestrator** | Agent harness: model picks tool(s), calls them, synthesizes | Free-text task | Synthesized Markdown report | ⭐⭐⭐ |
| **Blog Idea Scout** | Level 1: Search → LLM | Topic (or "trending") | 3-5 blog ideas with pitches & URLs | ⭐ |
| **Repo Onboarding** | Level 3: Fetch Repo → Search → LLM | Repo URL | Comprehensive onboarding guide | ⭐⭐ |
| **CVE Impact** | Level 3: Search → LLM | CVE ID / Software | Security assessment & remediation | ⭐⭐ |
| **Issue Fix Planner** | Level 4: Fetch Issue → Search → LLM → Plan | Issue URL | Implementation plan (no code) | ⭐⭐ |
| **Do I Care?** | Level 5: Score → Filter → Analyze | Headlines + Profile | Top 3 with why & actions | ⭐⭐ |
| **Opportunity Scout** | Level 5: Analyze → Trends → Opportunities | GitHub Username | Skill gaps, jobs, project ideas | ⭐⭐ |
| **Security Audit** | Level 5: combines Onboarding + CVE Impact | Repo URL | Unified security audit report | ⭐⭐ |

**Pattern Levels:**
- **Level 1:** Direct LLM call (search + LLM)
- **Level 3:** Fixed pipeline (3 tool calls + synthesis)
- **Level 4:** Multi-step fixed pipeline with guardrails (no code generation)
- **Level 5:** Relevance scoring & filtering, or combining other agents' output (score → filter → analyze)

---

## Quick Start

### Requirements
- Python 3.9+
- `pip` and `venv`
- Secrets: `OPENAI_API_KEY`, `TAVILY_API_KEY`, `GITHUB_TOKEN` (optional)

### Local Setup

```bash
# Clone and enter directory
git clone https://github.com/user/micro-agents
cd micro-agents

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure secrets
cp .env.example .env
# Edit .env with your API keys
```

### Run Locally

```bash
# Start the Streamlit app
streamlit run app.py

# Opens at http://localhost:8501
```

### Test Smoke Tests

```bash
# Test individual agents
python agents/blog_scout/logic.py
python agents/repo_onboarding/logic.py
python agents/issue_fix_planner/logic.py
# ... and so on

# Run all evaluations
python evals/run_evals.py
```

---

## Deployment on Streamlit Community Cloud

1. Push the repo to GitHub and create an app at [share.streamlit.io](https://share.streamlit.io) with `app.py` as the entry point.
2. In **App settings → Secrets**, add `OPENAI_API_KEY` (required) plus optional `TAVILY_API_KEY` and `GITHUB_TOKEN`. Streamlit Cloud exposes top-level secrets as environment variables, which is where the `core/` modules read them.
3. Set a monthly spend cap on the OpenAI key - the in-app cooldown and per-session request cap are only a soft guard.

**Memory on a public deployment.** Each agent = LLM + tools + memory, and memory is designed for shared, ephemeral hosting: nothing is written to disk.
- **Session memory** (`SessionMemory`): each visitor's own run history, kept in their session and never shown to anyone else. The orchestrator's `recall_memory` tool reads it for follow-ups.
- **Shared result cache** (`ResultCache`): holds results only for agents that depend solely on a public URL (`repo_onboarding`, `cve_impact`, `security_audit`, `issue_fix_planner`), for one hour, capped at 100 entries. Cache hits skip GitHub/OpenAI and don't count against the visitor's request cap.
- Free-text agents (`blog_scout`, `do_i_care`, `opportunity_scout`) and the orchestrator's own reports are never cached across visitors.

---

## Deployment on Hugging Face Spaces

### Prerequisites
1. [Hugging Face account](https://huggingface.co)
2. `huggingface-cli` installed: `pip install huggingface-hub`
3. Personal access token from [HF settings](https://huggingface.co/settings/tokens)

### Steps

1. **Create a Space:**
   ```bash
   huggingface-cli login  # Enter your token
   huggingface-cli repo create micro-agents --type space --space-sdk streamlit
   ```

2. **Push code:**
   ```bash
   cd micro-agents
   git remote add space https://huggingface.co/spaces/{your-username}/micro-agents
   git push space main
   ```

3. **Set secrets in HF Space UI:**
   - Go to **Space Settings → Secrets**
   - Add:
     - `OPENAI_API_KEY`: Your OpenAI API key
     - `TAVILY_API_KEY`: Your Tavily API key (optional, uses DuckDuckGo as fallback)
     - `GITHUB_TOKEN`: Your GitHub PAT (optional, for rate limit increase)

4. **Verify deployment:**
   - Navigate to `https://huggingface.co/spaces/{your-username}/micro-agents`
   - All 6 tabs should load and respond to inputs

### Security Checklist
- ✅ `.env` is in `.gitignore` (no secrets in repo)
- ✅ Secrets stored only in HF Space UI, not in code
- ✅ Verify with: `git log -p | grep -i api_key` (should be empty)

---

## Architecture

```
micro-agents/
├── core/                          # Shared API wrappers
│   ├── llm.py                    # OpenAI (gpt-4o-mini), retried once
│   ├── search.py                 # Tavily + DuckDuckGo fallback
│   ├── github_tool.py            # GitHub REST API wrapper
│   └── memory.py                 # Session memory + shared result cache (in-process, bounded)
│
├── agents/                        # 7 specialists + 1 orchestrator
│   ├── orchestrator/             # Routes a task to 1-2 specialists, synthesizes results
│   ├── blog_scout/               # Level 1: Search → LLM
│   ├── repo_onboarding/          # Level 3: Fetch → Search → LLM
│   ├── cve_impact/               # Level 3: Search → LLM
│   ├── issue_fix_planner/        # Level 4: multi-step fixed pipeline
│   ├── do_i_care/                # Level 5: Score → Filter → Analyze
│   ├── opportunity_scout/        # Level 5: Analyze → Trends → Opportunities
│   └── security_audit/           # Level 5: combines onboarding + CVE analysis
│
├── evals/                         # Evaluation suite
│   └── run_evals.py              # Test runner (reports pass rate)
│
├── app.py                         # Streamlit multi-tab UI (main entry point)
├── requirements.txt               # Dependencies (no LangGraph, LangChain)
├── .env.example                   # Secret template
└── README.md                      # This file
```

### Core APIs

**LLM (core/llm.py):**
- OpenAI API (`gpt-4o-mini`), retried once on failure
- Usage: `call_llm(prompt, system=None, model="default")`

**Search (core/search.py):**
- Primary: Tavily API (1000 free queries/month)
- Fallback: DuckDuckGo (no key required)
- Returns: `[{"title": str, "url": str, "snippet": str}, ...]`

**GitHub (core/github_tool.py):**
- REST API (60 req/hr unauthenticated, 5000 req/hr authenticated)
- Functions: `fetch_repo()`, `fetch_issue()`, `fetch_pr()`
- Tracks GitHub's own rate-limit headers and fails fast with a clear error when the budget is nearly exhausted, instead of burning through it silently

**Agent Harness (core/harness.py):**
- A minimal, from-scratch tool-calling loop built on OpenAI function calling - no LangChain/LangGraph
- Each specialist is registered as a `Tool`; the model itself decides which 1-2 tools to call and when
  it has enough to answer, instead of a hand-coded routing step
- Guardrails: a per-step cap, per-tool error isolation, and an optional per-call `validate` hook
- Usage: `run_harness(task, tools, system_prompt)`

**Orchestrator (agents/orchestrator/logic.py):**
- Runs entirely on the harness above: wraps all 7 specialists as tools and lets the model pick,
  call, and synthesize a Markdown report in one agentic loop
- Uses the harness's `validate` hook to sanity-check each tool call's input against the original
  task text, guarding against prompt injection from content fetched by an earlier tool call
- Usage: `run_orchestrator(task)`

---

## Evaluation Results

Each agent is tested against 3–5 test cases in `evals.jsonl`. Run evaluations:

```bash
python evals/run_evals.py              # Run all evals, show pass rates
python evals/run_evals.py --verbose    # Show detailed test results
```

**How to Achieve 80%+ Pass Rates:**

See [EVALUATION_GUIDE.md](./EVALUATION_GUIDE.md) for detailed instructions on:
- Understanding what pass rates mean
- Running evaluations locally
- Debugging and fixing failing tests
- Improving agents from 60% → 80%+

**Target Pass Rates** (Production Readiness):

| Agent | Target | Achieved | Test Cases |
|-------|--------|-----------|------------|
| blog_scout | ≥80% | ✅ 100% (5/5) | 5 (search + URL validation) |
| repo_onboarding | ≥80% | ✅ 80% (4/5) | 5 (guide structure + completeness) |
| cve_impact | ≥80% | ⚠️ 60% (3/5) | 5 (security assessment + error handling) |
| issue_fix_planner | ≥80% | ✅ 80% (4/5) | 5 (no code generation guardrail) |
| do_i_care | ≥80% | ⚠️ 40% (2/5) | 5 (relevance scoring + filtering) |
| opportunity_scout | ≥80% | ✅ 80% (4/5) | 5 (career analysis + insights) |
| orchestrator | ≥80% | ✅ 80% (4/5) | 5 (routing + synthesis) |

**Overall: 74% (26/35)** — 5/7 agents at or above the 80% target. `cve_impact` and `do_i_care` are below target and tracked for improvement; see [EVALUATION_GUIDE.md](./EVALUATION_GUIDE.md) for how to debug and raise a specific agent's pass rate.

*Generated via `python evals/run_evals.py`.*

---

## Development Workflow

### Adding an Agent

1. **Create directory:** `agents/my_agent/`
2. **Implement files:**
   - `prompts.py` — system & user prompts
   - `logic.py` — core agent logic (imports from `core/`, smoke test in `__main__`)
3. **Add to dashboard:** Import in root `app.py` and add a tab
4. **Evaluate:** Add `evals.jsonl` with 3-5 test cases

### Testing

```bash
# Test individual agent
python agents/my_agent/logic.py

# Run evaluations
python evals/run_evals.py
```

### Design Constraints

- **No frameworks:** No LangGraph, LangChain, Pydantic, or databases
- **No durable persistence:** memory is in-process only (per-session history + a bounded, TTL'd shared cache) - no database and no files, so nothing survives a restart
- **No async:** Simple synchronous calls only
- **Smoke tests only:** No pytest or CI/CD setup
- **Secrets via .env:** Never hardcoded

---

## Environment Variables

Create `.env` from `.env.example`:

```bash
OPENAI_API_KEY=your-openai-key
TAVILY_API_KEY=your-tavily-key          # Optional, uses DuckDuckGo fallback
GITHUB_TOKEN=your-github-pat            # Optional, for higher rate limits
```

---

## Troubleshooting

### Agent times out
- Check API keys and rate limits
- Reduce model complexity or max_steps
- Use fallback APIs (DuckDuckGo)

### Streamlit app won't start
- Ensure all dependencies installed: `pip install -r requirements.txt`
- Check port 8501 is not in use: `netstat -tuln | grep 8501`
- Run smoke test: `python agents/blog_scout/logic.py`

### CVE/Security agents return empty
- May need GITHUB_TOKEN for higher rate limits
- Tavily fallback (DuckDuckGo) has limited results
- Try more specific search queries

### Deployment issues
- Verify `.env` is in `.gitignore`
- Check HF Space secrets are set (Settings → Secrets)
- Logs available in Space → Runtime tab

---

## Contributing

To extend micro-agents:

1. **Add a new agent** following the Level 1-5 pattern in `agents/`
2. **Test locally** with smoke tests and evals
3. **Update app.py** to include the new agent in the dashboard
4. **Update README** with agent description and pass rates



---

## License

MIT

---

## Resources

- **OpenAI:** https://platform.openai.com/docs
- **Tavily:** https://tavily.com
- **GitHub API:** https://docs.github.com/en/rest
- **Streamlit:** https://docs.streamlit.io

---

**Last Updated:** August 2026 | **Version:** 1.0 
