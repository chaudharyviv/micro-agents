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
| **CVE Impact** | Level 3: Fetch Repo → Parse Manifests → OSV.dev → LLM prose | Repo URL | Known vulnerabilities in declared dependencies, with fixed versions | ⭐⭐ |
| **Issue Fix Planner** | Level 4: Fetch Issue → Search → LLM → Plan | Issue URL | Implementation plan (no code) | ⭐⭐ |
| **Do I Care?** | Level 5: Score → Filter → Analyze | Headlines (+ optional `Profile:` first line) | Up to 3 relevant items with why & action | ⭐⭐ |
| **Opportunity Scout** | Level 5: GitHub API → Market Search → LLM | GitHub Username | Skill gaps (with sources), roles, project idea | ⭐⭐ |
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

# Run the offline evaluations (free, no keys). Drop --replay for the live suite, which calls real APIs.
python evals/run_evals.py --replay
```

---

## Deployment on Streamlit Community Cloud

1. Push the repo to GitHub and create an app at [share.streamlit.io](https://share.streamlit.io) with `app.py` as the entry point.
2. In **App settings → Secrets**, add `OPENAI_API_KEY` (required) plus optional `TAVILY_API_KEY` and `GITHUB_TOKEN`. Streamlit Cloud exposes top-level secrets as environment variables, which is where the `core/` modules read them.
3. **Set a hard spending limit on the OpenAI key in the OpenAI dashboard.** The in-app limits below are a safeguard, not a substitute: they live in one process, reset when it restarts, and can't see other apps using the same key.

**Usage limits on a public deployment.** Every model call goes through one gate (`core/budget.py`), and each attempt counts, retries included (the OpenAI SDK's own silent retries are turned off so nothing is uncounted):

| Limit | Default | Environment variable |
|-------|---------|----------------------|
| Model calls per user action (an agent run) | 12 | `LLM_REQUEST_CALLS` |
| Model calls per orchestrator run, specialists included | 20 | `LLM_ORCHESTRATOR_REQUEST_CALLS` |
| Model calls per client per UTC day | 100 | `LLM_CLIENT_DAILY_CALLS` |
| Model calls per UTC hour, all visitors | 300 | `LLM_HOURLY_CALLS` |
| Model calls per UTC day, all visitors | 1,500 | `LLM_DAILY_CALLS` |
| Tokens per UTC day, all visitors | 2,000,000 | `LLM_DAILY_TOKENS` |

The orchestrator also runs at most 3 tool calls per task, however many the model asks for at once, and each visitor is limited to 20 requests per hour (cache hits are free and don't count). When a limit is reached the user sees which one, and no API call is made.

- **The global limits are the real protection.** The per-client limits key on the connection's IP address, which Streamlit documents as spoofable, so a determined caller can get around them. The hourly cap stops such a caller from draining the whole day's budget in minutes.
- **State is in memory.** A restart resets the counters, and they are not shared between processes. Set `BUDGET_STATE_FILE=path` to persist the global counters across restarts where the host's disk survives them.
- The daily defaults are sized to stay around a dollar a day at `gpt-4o-mini` list prices; check current pricing and adjust them to your own budget.

**Output safety.** Model output is shaped by text an attacker can write (READMEs, issues, search results), so it is handled defensively at three points:

- **Structured outputs.** Agents that produce JSON ask the API for output that conforms to a strict JSON schema (`core/llm.py`, schemas in each agent's `prompts.py`), rather than asking for JSON in the prompt and hoping. A refusal or an output cut off by the length limit is reported as such and never retried, and never parsed as if it were complete.
- **Untrusted text is delimited in one place.** All third-party text put in a prompt goes through `wrap_untrusted` (`core/llm_utils.py`), which strips look-alikes of the delimiter (including zero-width and full-width variants) so the text cannot close its own block. Prompt templates deliberately don't contain the delimiter, and a test enforces it. Tool results fed to the orchestrator are wrapped the same way, and truncation is marked.
- **Rendered Markdown is sanitized** (`core/safe_markdown.py`). Images are removed, since an image loads its URL with no click and can carry data out. A link is kept only if its URL was named by the user or appears in a structured `url`-type field of a result; anything else, including bare URLs, `www.` links, reference-style links and raw HTML, is shown as inert text with the URL visible. URLs found inside free text (a README, a model's prose) are deliberately not trusted. The rewrite is verified by re-parsing the output with a CommonMark parser; if anything unsafe remains, all link syntax is escaped, and failing that the content is withheld. This is fuzz-tested against random adversarial input.

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
│   ├── llm.py                    # OpenAI (gpt-4o-mini), retried once; every call goes through the budget gate
│   ├── llm_utils.py              # Strict-schema builders, JSON extraction, untrusted-text wrapping
│   ├── safe_markdown.py          # Link/image sanitizer for rendered Markdown
│   ├── budget.py                 # Per-request / per-client / hourly / daily model-call and token limits
│   ├── ratelimit.py              # Per-client sliding-window request limiter
│   ├── targets.py                # Parses repo/issue/user targets for exact-match grounding
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
│   ├── run_evals.py              # Eval runner: replay/live modes, exit codes, baseline
│   ├── checks.py                 # The assertions cases are built from (each one tested)
│   ├── cases.py                  # Loads and validates evals.jsonl
│   ├── adapters.py               # How each agent is called from a case
│   ├── golden/                   # Recorded outputs from live runs, for offline replay
│   └── baseline.json             # Last accepted live run, for regression detection
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

## Evaluation

47 cases across all 8 agents (`agents/<agent>/evals.jsonl`). Each case runs an agent on a real input and asserts on
the *content* of the result: whether it is grounded, ranked, routed and consistent, not merely whether something
came back.

```bash
python evals/run_evals.py --replay     # offline, free, no keys: what CI runs on every change
python evals/run_evals.py              # live: real OpenAI, Tavily, GitHub and OSV.dev (about 40 model calls)
python evals/run_evals.py -v --agent do_i_care --only ranks   # a subset, verbosely
```

What makes the results trustworthy:

- **Assertions that can fail.** Examples: a CVE finding's ID must be a real OSV record for that package; every URL
  Opportunity Scout cites must be one it was given; Do I Care's top items must be the relevant headlines and an
  injected one must stay out; the orchestrator must run only the named repo and its report may contain no images
  or unsourced links. Each check is itself tested against known-good and known-bad output.
- **Validation cases really call the agent.** They run the real agent code with the network blocked.
- **Exit codes** distinguish a quality failure (1) from infrastructure trouble (2) and a suite that couldn't run (3),
  so CI can gate on them. A run also fails if a case that passed in `evals/baseline.json` now fails.
- **Model spend is capped** per run, using the same budget gate as the app.

CI runs the offline half on every push and pull request, and the live half weekly and on demand
(`.github/workflows/`). See [EVALUATION_GUIDE.md](./EVALUATION_GUIDE.md) for the case format, the checks, how to add
a case, and how to read a failure.

**Snapshot (2026-09-21).** Two consecutive live runs each passed 47 of 47 cases with no retries needed
(38 and 40 model calls). To test the tests, 13 defects were injected into the agents one at a time (a risk level
that ignores findings, an invented CVE ID, a fetcher that stops returning manifests, an injected headline scored
10, a plan containing code, a report with an image, a specialist run on the wrong repo, and others); all 13 turned
the matching case red. That is a small regression net, not a benchmark: it says nothing about answer quality
beyond structure and grounding, which nothing here judges.

---

## Development Workflow

### Adding an Agent

1. **Create directory:** `agents/my_agent/`
2. **Implement files:**
   - `prompts.py` — system & user prompts
   - `logic.py` — core agent logic (imports from `core/`, smoke test in `__main__`)
3. **Add to dashboard:** Import in root `app.py` and add a tab
4. **Evaluate:** Add `evals.jsonl` cases with real assertions, record their output with `python evals/run_evals.py --record --agent my_agent`, and add the agent to `evals/cases.py` and `evals/adapters.py` (see [EVALUATION_GUIDE.md](./EVALUATION_GUIDE.md))

### Testing

```bash
# Test individual agent
python agents/my_agent/logic.py

# Unit tests, then the offline evals (both run in CI)
pytest
python evals/run_evals.py --replay
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
