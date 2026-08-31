# Micro Agents — Design & Architecture Spec

**Author:** Vivikt
**Status:** Draft v1
**Scope:** 6 small agents, 1 repo, open-weight LLMs, free-tier deployment

---

## 1. Why This Matters

This isn't a hiring-pitch project — it's a portfolio-breadth project. The gap it fills:

- Every other repo in the portfolio (SWINGTRADE, War Room, infra-arbiter) is **domain-deep**: storage, trading, incident response. None of them demonstrate that you can ship **small, general-purpose, tool-using agents** quickly, outside a single domain.
- No current project runs an **open-weight model** end-to-end. Everything so far leans on Claude/GPT-4o via API. This repo is the "I can also build without a paid API dependency" credential.
- No current project is deployed on **Hugging Face** — a different platform than Vercel/Streamlit Cloud/Cloud Run, which diversifies the hosting story on your profile.
- It's a natural home for **evaluation-as-a-feature** — showing you measure whether an agent works, not just that it runs.

The unifying theme across all 6 agents: **small tool, one boring problem, visible reasoning, no unnecessary framework weight.**

---

## 2. Why Open-Weight LLMs (and why it's worth doing deliberately)

Reasons this is worth building, independent of any hiring narrative:

1. **No vendor lock-in story.** Every other project depends on a paid, closed API. This repo proves the same agent patterns work on models you don't pay per-token for.
2. **Data-sovereignty relevance.** Open-weight models can run fully local/self-hosted — relevant to your storage/infra background even without pushing a job narrative. It's simply a different, real constraint to have engineered around.
3. **Cost ceiling of zero.** Lets the repo run indefinitely on free tiers without a metering worry — appropriate for a "breadth" project you're not actively monetizing.
4. **Learning value.** Forces you to deal with weaker instruction-following, smaller context windows, and rougher tool-calling — a different (and useful) engineering problem than working against Claude/GPT-4o.
5. **Hugging Face is the ecosystem, not just a host.** Publishing on HF (models, Spaces, maybe a dataset for evals) gives you a footprint on a platform with its own audience and discovery, separate from GitHub.

---

## 3. High-Level Architecture

```
micro-agents/
│
├── core/
│   ├── llm.py          # thin wrapper: call any open-weight model via one interface
│   ├── search.py        # web search tool wrapper (Tavily primary, DDG fallback)
│   ├── github_tool.py    # GitHub REST wrapper: repo fetch, PR fetch, issue fetch
│   ├── agent.py         # minimal agent loop (plan -> tool call -> observe -> respond)
│   └── evaluator.py      # scores agent output against test cases
│
├── agents/
│   ├── blog_scout/
│   │   ├── app.py        # Gradio/Streamlit UI, imports core/
│   │   ├── prompts.py
│   │   └── evals.jsonl   # ~10 test cases: input, expected behavior, scoring rule
│   ├── repo_onboarding/
│   ├── cve_impact/
│   ├── issue_fix_planner/
│   ├── do_i_care/
│   └── opportunity_scout/
│
├── evals/
│   └── run_evals.py      # runs evals.jsonl per agent, prints score table
│
└── README.md            # "6 small agents. Open-weight models. Free-tier only."
```

**Design principle:** `core/` is intentionally thin — a wrapper, not a framework. Each agent file should be readable top-to-bottom in under 2 minutes. No LangGraph here; the point of this repo is to show you *don't* need heavy orchestration for small, well-scoped agents. (LangGraph stays your tool of choice for the stateful/cyclical systems — Bank in a Box, infra-arbiter — where it's actually earning its complexity.)

### 3.1 `core/llm.py` — model access layer

- Single function: `call_llm(prompt, system=None, model="default") -> str`
- Backed by **Groq** (free tier, fast inference, hosts Llama 3.3 70B, Llama 3.1 8B, and other open weights) as primary.
- Fallback: **Hugging Face Inference Providers** (free-tier serverless inference on select open models) if Groq rate-limits.
- Model choice is a config value per agent, not hardcoded — lets you swap Llama/Mistral/Qwen per agent to show range.

### 3.2 `core/search.py` — web search tool

- **Tavily** free tier (1,000 searches/month, built for LLM agents — returns clean structured results, not raw HTML).
- Fallback: DuckDuckGo unofficial API, no key required, lower reliability.

### 3.3 `core/github_tool.py` — GitHub access

- Unauthenticated GitHub REST calls for public repo metadata (60 req/hr limit) — fine at this scale.
- Optional personal access token (read-only, public_repo scope) if you hit rate limits during demos.

### 3.4 `core/agent.py` — the loop

Minimal ReAct-style loop, roughly:

```
def run_agent(task, tools, max_steps=4):
    for step in range(max_steps):
        decision = call_llm(plan_prompt(task, history))
        if decision.is_final:
            return decision.output
        result = execute_tool(decision.tool_call)
        history.append((decision, result))
    return best_effort_output(history)
```

No persistence, no memory store, no multi-agent handoff — deliberately. That complexity belongs to your depth projects, not here.

### 3.5 `core/evaluator.py`

- Loads `evals.jsonl` per agent (input, expected_behavior, scoring_rule).
- Runs agent against each input, scores output (exact-match, keyword-presence, or LLM-graded — start with the first two, cheapest and clearest).
- `run_evals.py` prints a simple table: agent name, pass rate, failures. This becomes the "here's how I know it works" section of the README.

---

## 4. Per-Agent Spec Template

Every agent gets a short spec before code. Template:

```
### Agent: <name>
Pattern level: <1-5, per the agent-levels ladder below>
Input: <what the user provides>
Tools used: <list>
Output: <concrete artifact produced>
Stop condition: <when does it decide it's done>
Eval cases: <3-5 example inputs + what "correct" looks like>
```

### Agent-level ladder (used consistently across all 6)

| Level | Pattern | Agent |
|---|---|---|
| 1 | Input → LLM → Output | Blog Idea Scout |
| 2 | Input → LLM → Tool → LLM → Output | — |
| 3 | Plan → multiple tools → synthesize | Repo Onboarding, CVE Impact |
| 4 | Observe → decide → act loop | Issue Fix Planner |
| 5 | Loop + evaluator/filter (agent decides relevance, not just answers) | Do I Care?, Opportunity Scout |

This ladder is the README's organizing narrative — it turns "6 unrelated demos" into "a progression of one idea," which is the actual differentiator versus a random agent collection.

---

## 5. The 6 Agents — Individual Specs

### 5.1 Blog Idea Scout
- **Input:** topic area or "just scan trending"
- **Tools:** Tavily search (GitHub trending, tech blogs)
- **Output:** 3–5 blog ideas, each with a one-line pitch and a source link
- **Stop condition:** fixed 1-shot (search → rank → generate), no loop

### 5.2 Repo Onboarding Agent
- **Input:** GitHub repo URL
- **Tools:** github_tool (fetch README, file tree, key files)
- **Output:** structured brief — what it does, architecture, how to run, where to start
- **Stop condition:** fixed pipeline, 3 tool calls max

### 5.3 CVE Impact Agent
- **Input:** CVE ID or a short list of "software I run" (e.g. ONTAP, Trident, K8s versions)
- **Tools:** Tavily search scoped to NVD/vendor advisories
- **Output:** severity, exploitability, "does this affect what you listed," recommended action
- **Stop condition:** fixed pipeline; this is the agent that ties back to your real storage/security depth — worth the most polish of the 6

### 5.4 Issue Fix Planner
- **Input:** GitHub issue URL
- **Tools:** github_tool (fetch issue, fetch relevant files by search)
- **Output:** implementation plan (files to touch, suggested approach, suggested tests) — **never writes code or opens a PR**, planning only
- **Stop condition:** loop with max 4 steps, explicit "planning only" guardrail in system prompt

### 5.5 "Do I Care?" Agent
- **Input:** a batch of headlines/articles (paste or RSS pull) + a short profile of your interests
- **Tools:** none required beyond the LLM call — the differentiator is the filter step, not the search
- **Output:** 3 items worth attention, each with why-it-matters and a suggested action; explicitly discards the rest
- **Stop condition:** single pass, but with an explicit relevance-scoring step before the summarization step (that's what makes it Level 5, not Level 1)

### 5.6 Opportunity Scout
- **Input:** your GitHub activity (public, via github_tool) + a fixed set of job-market search queries
- **Tools:** github_tool + Tavily search
- **Output:** skill-gap note, relevant job postings, one suggested 3-day project idea
- **Stop condition:** multi-tool pipeline, no loop needed — the "persistence" framing from the original brainstorm is optional polish (e.g. a scheduled GitHub Action re-run), not required for v1

---

## 6. Hugging Face Deployment Plan (with the 2026 free-tier constraint)

**The constraint:** as of mid-2026, HF requires a paid plan to *create* new Gradio or Docker Spaces on compute; free accounts can still host up to 2 Gradio Spaces (on ZeroGPU). Static Spaces remain fully free with no limit, but have no Python backend.

**Practical options, in order of recommendation:**

1. **One multi-tab Gradio Space, not six separate Spaces.** Build a single Space with `gr.Tabs()` — one tab per agent, all sharing the same `core/` imports. This sidesteps the 2-Space limit entirely and is arguably a *better* demo (one URL, all 6 agents, consistent UI) than six disconnected links.
2. **If you want them visually separate anyway:** use your 2 free Gradio-on-ZeroGPU slots for the two most demo-worthy agents (CVE Impact and Do I Care?), and put the rest behind the single multi-tab Space.
3. **Local-first fallback:** since it also needs to run on your laptop, `streamlit run app.py` locally works regardless of HF's Gradio restrictions — Streamlit's free-tier Spaces status should be checked at build time, since HF's policy language above only explicitly names Gradio and Docker as requiring a paid plan.
4. **Verify at build time, not now:** HF's free-tier terms have changed multiple times this year per their own community forum threads — confirm current Gradio/Streamlit Space creation limits before finalizing the deployment shape, rather than trusting this doc's snapshot.

**Recommended shape:** one repo → one multi-tab Gradio Space (`micro-agents` on HF) → same code runs locally via `python app.py` for daily laptop use. This satisfies "works on my laptop, deployed on community cloud" with a single build, not six.

---

## 7. Build Order

1. `core/` (llm.py, search.py, github_tool.py, agent.py) — the only shared investment
2. Blog Idea Scout (Level 1 — proves the wiring end-to-end)
3. Repo Onboarding Agent (Level 3 — proves multi-tool)
4. CVE Impact Agent (Level 3 — your depth tie-in, worth extra polish)
5. Issue Fix Planner (Level 4 — proves the loop + guardrail)
6. Do I Care? (Level 5 — proves the filter/relevance step)
7. Opportunity Scout (Level 5 — closer, most demo-friendly)
8. `evals/` — 3–5 cases per agent, written alongside each agent, not batched at the end
9. README with the agent-level ladder table as the narrative spine
10. Multi-tab Gradio Space deployment + local run instructions

---

## 8. What This Repo Deliberately Does *Not* Do

- No LangGraph, no multi-agent orchestration — that's your other repos' job.
- No persistent memory store or database — SWINGTRADE/War Room/infra-arbiter already cover that ground.
- No auto-code-writing or auto-PR agents — Issue Fix Planner stops at a plan, on purpose; this space is saturated (Copilot, Cursor, etc.) and "plans, doesn't act" is the more interesting judgment call to demonstrate anyway.
- No paid APIs anywhere in the default path — Groq/HF free tier + Tavily free tier only.
