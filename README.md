# micro-agents

Six independent AI agents for specialized tasks, powered by open-weight LLMs (Groq).

## Overview

Micro-Agents is a lightweight framework for building specialized AI agents without external dependencies like LangGraph, LangChain, or persistent databases. Each agent follows a specific pattern and uses core APIs for LLM, search, and GitHub interactions.

**Live Demo:** [Hugging Face Spaces](#deployment) | **Design Spec:** [micro-agents-design-spec.md](./micro-agents-design-spec.md)

---

## Agents

| Agent | Pattern | Input | Output | Complexity |
|-------|---------|-------|--------|------------|
| **Blog Idea Scout** | Level 1: Search → LLM | Topic (or "trending") | 3-5 blog ideas with pitches & URLs | ⭐ |
| **Repo Onboarding** | Level 3: Fetch Repo → Search → LLM | Repo URL | Comprehensive onboarding guide | ⭐⭐ |
| **CVE Impact** | Level 3: Search → LLM | CVE ID / Software | Security assessment & remediation | ⭐⭐ |
| **Issue Fix Planner** | Level 4: Fetch Issue → Search → LLM → Plan | Issue URL | Implementation plan (no code) | ⭐⭐ |
| **Do I Care?** | Level 5: Score → Filter → Analyze | Headlines + Profile | Top 3 with why & actions | ⭐⭐ |
| **Opportunity Scout** | Level 5: Analyze → Trends → Opportunities | GitHub Username | Skill gaps, jobs, project ideas | ⭐⭐ |

**Pattern Levels:**
- **Level 1:** Direct LLM call (search + LLM)
- **Level 3:** Fixed pipeline (3 tool calls + synthesis)
- **Level 4:** ReAct loop with guardrails (4 steps max, no code generation)
- **Level 5:** Relevance scoring & filtering (score → filter → analyze)

---

## Quick Start

### Requirements
- Python 3.9+
- `pip` and `venv`
- Secrets: `GROQ_API_KEY`, `TAVILY_API_KEY`, `HF_TOKEN`, `GITHUB_TOKEN` (optional)

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
# Start the Gradio app
python app.py

# Opens at http://127.0.0.1:7860
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

## Deployment on Hugging Face Spaces

### Prerequisites
1. [Hugging Face account](https://huggingface.co)
2. `huggingface-cli` installed: `pip install huggingface-hub`
3. Personal access token from [HF settings](https://huggingface.co/settings/tokens)

### Steps

1. **Create a Space:**
   ```bash
   huggingface-cli login  # Enter your token
   huggingface-cli repo create micro-agents --type space --space-sdk gradio
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
     - `GROQ_API_KEY`: Your Groq API key
     - `TAVILY_API_KEY`: Your Tavily API key (optional, uses DuckDuckGo as fallback)
     - `GITHUB_TOKEN`: Your GitHub PAT (optional, for rate limit increase)
     - `HF_TOKEN`: Your Hugging Face token (optional)

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
│   ├── llm.py                    # Groq + HF fallback
│   ├── search.py                 # Tavily + DuckDuckGo fallback
│   ├── github_tool.py            # GitHub REST API wrapper
│   ├── agent.py                  # ReAct loop (max 4 steps)
│   └── memory.py                 # Optional memory layer
│
├── agents/                        # 6 specialized agents
│   ├── blog_scout/               # Level 1: Search → LLM
│   ├── repo_onboarding/          # Level 3: Fetch → Search → LLM
│   ├── cve_impact/               # Level 3: Search → LLM
│   ├── issue_fix_planner/        # Level 4: ReAct loop
│   ├── do_i_care/                # Level 5: Score → Filter → Analyze
│   └── opportunity_scout/        # Level 5: Analyze → Trends → Opportunities
│
├── evals/                         # Evaluation suite
│   └── run_evals.py              # Test runner (reports pass rate)
│
├── app.py                         # Gradio multi-tab UI (main entry point)
├── requirements.txt               # Dependencies (no LangGraph, LangChain)
├── .env.example                   # Secret template
└── README.md                      # This file
```

### Core APIs

**LLM (core/llm.py):**
- Primary: Groq API (`gpt-4-like` model)
- Fallback: HF Inference (free tier)
- Usage: `call_llm(prompt, system=None, model="default")`

**Search (core/search.py):**
- Primary: Tavily API (1000 free queries/month)
- Fallback: DuckDuckGo (no key required)
- Returns: `[{"title": str, "url": str, "snippet": str}, ...]`

**GitHub (core/github_tool.py):**
- REST API (60 req/hr unauthenticated, 5000 req/hr authenticated)
- Functions: `fetch_repo()`, `fetch_issue()`, `fetch_pr()`

**Agent Loop (core/agent.py):**
- ReAct pattern: plan → tool call → observe → respond
- Max 4 steps per task
- Includes guardrails (no code generation for planning agents)

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

| Agent | Target | Achieved* | Test Cases |
|-------|--------|-----------|------------|
| blog_scout | ≥80% | TBD | 5 (search + URL validation) |
| repo_onboarding | ≥80% | TBD | 5 (guide structure + completeness) |
| cve_impact | ≥80% | TBD | 5 (security assessment + error handling) |
| issue_fix_planner | ≥80% | TBD | 5 (no code generation guardrail) |
| do_i_care | ≥80% | TBD | 5 (relevance scoring + filtering) |
| opportunity_scout | ≥80% | TBD | 5 (career analysis + insights) |

*\* Run `python evals/run_evals.py` to generate real pass rates*

**Status:** Ready to test. Run evals locally to see actual performance.

---

## Development Workflow

### Adding an Agent

1. **Create directory:** `agents/my_agent/`
2. **Implement files:**
   - `prompts.py` — system & user prompts
   - `logic.py` — core agent logic (imports from `core/`)
   - `app.py` — Gradio interface (smoke test in `__main__`)
3. **Add to dashboard:** Import in root `app.py` and add tab
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
- **No persistence:** Each run is independent (no memory)
- **No async:** Simple synchronous calls only
- **Smoke tests only:** No pytest or CI/CD setup
- **Secrets via .env:** Never hardcoded

---

## Environment Variables

Create `.env` from `.env.example`:

```bash
GROQ_API_KEY=your-groq-key
TAVILY_API_KEY=your-tavily-key          # Optional, uses DuckDuckGo fallback
GITHUB_TOKEN=your-github-pat            # Optional, for higher rate limits
HF_TOKEN=your-huggingface-token         # Optional, for HF Inference
```

---

## Troubleshooting

### Agent times out
- Check API keys and rate limits
- Reduce model complexity or max_steps
- Use fallback APIs (DuckDuckGo, HF Inference)

### Gradio app won't start
- Ensure all dependencies installed: `pip install -r requirements.txt`
- Check port 7860 is not in use: `netstat -tuln | grep 7860`
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

- **Groq:** https://console.groq.com/docs
- **Tavily:** https://tavily.com
- **GitHub API:** https://docs.github.com/en/rest
- **Gradio:** https://gradio.app

---

**Last Updated:** August 2026 | **Version:** 1.0 
