"""Multi-tab Streamlit interface for Micro-Agents project."""

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st

# Per-browser-session cooldown + request cap (friendly UX limits; a reload resets them)
MIN_SECONDS_BETWEEN_REQUESTS = 5
MAX_REQUESTS_PER_SESSION = 20

# Per-client request limit, kept in the server process so a reload doesn't reset it. Best-effort:
# the client id is the connection's IP address, which Streamlit documents as spoofable. The global
# model-call budget in core/budget.py is what actually caps spend.
CLIENT_MAX_REQUESTS_PER_HOUR = 20

# Shared result cache settings
SHARED_CACHE_TTL_SECONDS = 3600
SHARED_CACHE_MAX_ENTRIES = 100

from agents.blog_scout.logic import scout_blog_ideas
from agents.repo_onboarding.logic import generate_onboarding_guide
from agents.cve_impact.logic import analyze_cve_impact
from agents.issue_fix_planner.logic import run_issue_fix_planner
from agents.do_i_care.logic import run_do_i_care
from agents.opportunity_scout.logic import run_opportunity_scout
from agents.security_audit.logic import generate_security_audit
from agents.orchestrator.logic import run_orchestrator
from agents.orchestrator.prompts import DEFAULT_TASKS
from core.budget import get_budget, request_scope
from core.memory import ResultCache, SessionMemory, run_with_memory
from core.ratelimit import SlidingWindowLimiter
from core.safe_markdown import collect_urls, sanitize_markdown, urls_in_text

# Set page configuration
st.set_page_config(
    page_title="Micro-agents Dashboard",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------
# Custom Design System (CSS Injection)
# ---------------------------------------------------------
st.markdown(
    """
    <style>
    /* Gradient primary headers */
    .main-title {
        background: linear-gradient(90deg, #2563EB, #7C3AED);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-weight: 800;
        font-size: 2.25rem;
        margin-bottom: 0.2rem;
    }
    
    /* Modern container styling */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 12px !important;
        border: 1px solid rgba(226, 232, 240, 0.8) !important;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.03) !important;
        background-color: #FFFFFF;
    }
    
    /* Sidebar polish */
    section[data-testid="stSidebar"] {
        background-color: #F8FAFC;
        border-right: 1px solid #E2E8F0;
    }

    /* Tab bar elevation */
    button[data-baseweb="tab"] {
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        padding-top: 10px !important;
        padding-bottom: 10px !important;
    }

    /* Custom badge spacing */
    .stBadge {
        margin-right: 4px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def format_blog_ideas(ideas):
    """Format blog ideas for display."""
    if isinstance(ideas, dict) and "error_message" in ideas:
        return f":material/error: Error: {ideas['error_message']}"
    if not ideas:
        return "No ideas generated."

    output = []
    for i, idea in enumerate(ideas, 1):
        title = _md_escape(idea.get("title", "N/A"))
        pitch = _md_escape(idea.get("pitch", "N/A"))
        source_title = _md_escape(idea.get("source_title") or "source")
        source_url = idea.get("source_url", "")
        entry = f"### Idea {i}: {title}\n**Pitch:** {pitch}\n"
        if source_url:
            entry += f"**Source:** [{source_title}]({source_url})\n"
        output.append(entry)
    return "\n".join(output)


def format_guide(guide):
    """Format onboarding guide for display."""
    if "error_message" in guide:
        return f":material/error: Error: {guide['error_message']}"

    output = [f"# {guide.get('project_name', 'Project')}\n"]
    output.append(f"**Overview:** {guide.get('overview', 'N/A')}\n")

    tech_stack = guide.get("tech_stack", [])
    if tech_stack:
        output.append(f"**Tech stack:** {', '.join(tech_stack)}\n")

    setup_steps = guide.get("setup_steps", [])
    if setup_steps:
        output.append("## Setup steps\n")
        for i, step in enumerate(setup_steps, 1):
            output.append(f"{i}. {step}\n")

    return "\n".join(output)


def _md_escape(text) -> str:
    """Escape markdown/HTML control characters in third-party text before rendering it."""
    return re.sub(r"([\\`*_{}\[\]()<>#+!|~])", r"\\\1", str(text))


def format_cve(analysis):
    """Format CVE analysis for display."""
    if "error_message" in analysis:
        return f":material/error: Error: {analysis['error_message']}"

    risk_level = str(analysis.get("risk_level", "Unknown")).upper()
    
    # Dynamic severity banner injection
    if "HIGH" in risk_level or "CRITICAL" in risk_level:
        st.error(f"**Risk Level: {risk_level}** — Immediate attention recommended.")
    elif "MEDIUM" in risk_level:
        st.warning(f"**Risk Level: {risk_level}** — Moderate security impact.")
    else:
        st.success(f"**Risk Level: {risk_level}** — Low security impact detected.")

    output = ["### Security Analysis Summary\n"]
    output.append(f"{_md_escape(analysis.get('summary', 'N/A'))}\n")

    findings = analysis.get("cve_analysis", [])
    if findings:
        output.append("### Known vulnerabilities\n")
        for f in findings:
            basis = " *(declared range floor; installed version may be newer)*" if f.get("version_basis") == "range_floor" else ""
            output.append(
                f"- **{_md_escape(f.get('cve_id', 'N/A'))}** · {f.get('severity', 'Unknown')} · "
                f"`{_md_escape(f.get('package', ''))} {_md_escape(f.get('version', ''))}`{basis}\n"
                f"  {_md_escape(f.get('description', ''))}\n"
                + (f"  *Impact:* {_md_escape(f['impact'])}\n" if f.get("impact") else "")
                + f"  *Fix:* {_md_escape(f.get('remediation', ''))} [Advisory]({f.get('url', '')})"
            )
        output.append("")

    unchecked = analysis.get("dependencies_unchecked", [])
    not_analyzed = analysis.get("manifests_not_analyzed", [])
    output.append(
        f"*Checked {analysis.get('dependencies_checked', 0)} dependencies against OSV.dev. "
        f"Not checked: {len(unchecked)} dependencies without a resolvable version"
        + (f"; manifests not analyzed: {', '.join(not_analyzed)}" if not_analyzed else "")
        + ".*"
    )
    return "\n".join(output)


def format_plan(result):
    """Format implementation plan for display."""
    if result.get("status") == "error":
        return f":material/error: Error: {result.get('error_message')}"
    return f"# {result.get('issue_title', 'Plan')}\n\n{result.get('plan', 'No plan generated')}"


def format_analysis(result):
    """Format analysis results for display."""
    if result.get("status") == "error":
        return f":material/error: Error: {result.get('error_message')}"
    output = []
    if result.get("message"):
        output.append(f"**{_md_escape(result['message'])}**\n")
    for item in result.get("top_items", []):
        output.append(f"## #{item['rank']}: {_md_escape(item['headline'])}\n")
        output.append(f"**Relevance:** {item['score']}/10" + (f" — {_md_escape(item['score_reason'])}" if item.get("score_reason") else "") + "\n")
        if item.get("why_it_matters"):
            output.append(f"**Why it matters:** {_md_escape(item['why_it_matters'])}\n")
        if item.get("suggested_action"):
            output.append(f"**Suggested action:** {_md_escape(item['suggested_action'])}\n")
    if result.get("top_items") and not result.get("analysis_available", True):
        output.append("*The explanation step failed, so only scores are shown. Try again for a fuller analysis.*\n")

    others = result.get("scores", [])[len(result.get("top_items", [])):]
    if others:
        output.append("### Other items\n")
        output.extend(f"- {s['score']}/10 · {_md_escape(s['headline'])}" for s in others)
        output.append("")

    profile = "your profile" if result.get("profile_used") == "custom" else "the default profile (senior engineer: AI/ML, system design, developer tools)"
    footer = f"*Scored {result.get('analyzed_count', 0)} of {result.get('original_count', 0)} items against {profile}."
    if result.get("skipped_count"):
        footer += f" {result['skipped_count']} items beyond the first 20 were not scored."
    footer += " To use your own profile, make the first line `Profile: …`.*"
    output.append(footer)
    return "\n".join(output)


def format_opportunities(result):
    """Format opportunity analysis for display."""
    if result.get("status") == "error":
        return f":material/error: Error: {result.get('error_message')}"
    ev = result.get("evidence", {})
    output = [
        "# Career Opportunities\n",
        f"**Developer:** @{_md_escape(result.get('github_username'))}\n",
        f"*Based on {ev.get('repos_analyzed', 0)} public non-fork repositories "
        f"({ev.get('repos_pushed_last_12_months', 0)} active in the last 12 months). "
        "Private work and non-GitHub experience are not visible.*\n",
    ]

    if result.get("current_skills"):
        output.append("## Skills demonstrated\n" + ", ".join(_md_escape(s) for s in result["current_skills"]) + "\n")

    output.append("## Skill gaps\n")
    for g in result.get("skill_gaps", []):
        line = f"- **{_md_escape(g['skill'])}**: {_md_escape(g['reason'])}"
        if g.get("source_url"):
            line += f" ([source]({g['source_url']}))"
        else:
            line += " *(model judgment, not from a search result)*"
        output.append(line)

    output.append("\n## Job suggestions\n")
    for o in result.get("job_suggestions", []):
        output.append(f"- **{_md_escape(o['role'])}**: {_md_escape(o['fit'])}")

    idea = result.get("project_idea", {})
    output.append(f"\n## 3-day project idea\n**{_md_escape(idea.get('title', ''))}**\n\n{_md_escape(idea.get('description', ''))}")
    if idea.get("skills_built"):
        output.append("\n*Builds:* " + ", ".join(_md_escape(s) for s in idea["skills_built"]))

    sources = result.get("market_sources", [])
    if sources:
        output.append("\n### Market sources consulted\n")
        output.extend(f"- [{_md_escape(s['title'] or s['url'])}]({s['url']})" for s in sources)
    return "\n".join(output)


def format_audit(audit):
    """Format security audit for display."""
    if "error_message" in audit:
        return f":material/error: Error: {audit['error_message']}"
    return f"# Security Audit\n\n{audit.get('audit_summary', 'No audit generated')}"


@st.cache_resource
def get_shared_cache() -> ResultCache:
    """Shared cache across sessions."""
    return ResultCache(ttl_seconds=SHARED_CACHE_TTL_SECONDS, max_entries=SHARED_CACHE_MAX_ENTRIES)


@st.cache_resource
def get_client_limiter() -> SlidingWindowLimiter:
    """Process-wide per-client request limiter, shared by all sessions."""
    return SlidingWindowLimiter(CLIENT_MAX_REQUESTS_PER_HOUR, 3600)


def get_client_id() -> str:
    """Best-effort identity of the caller: their connection IP, or 'local' when Streamlit gives none."""
    try:
        ip = st.context.ip_address
    except Exception:
        return "local"
    return ip if isinstance(ip, str) and ip else "local"


def get_session_memory() -> SessionMemory:
    """Per-session run history."""
    if "memory" not in st.session_state:
        st.session_state["memory"] = SessionMemory(max_entries=20)
    return st.session_state["memory"]


TOOL_ICONS = {
    "LLM": ":material/psychology:",
    "Web search": ":material/search:",
    "GitHub API": ":material/code:",
    "Specialist tools (dynamic)": ":material/route:",
}

AGENT_INFO = {
    "orchestrator": {
        "description": (
            "Routes a free-text task to specialist agents and synthesizes the results. "
            "Operates via a real tool-calling loop (`core/harness.py`)."
        ),
        "tools": ["LLM", "Specialist tools (dynamic)"],
        "memory": "Session memory + recall tool",
        "memory_detail": (
            "Records runs to session memory and uses `recall_memory` for contextual follow-ups."
        ),
    },
    "blog_scout": {
        "description": "Searches the web for topics and generates blog post pitches with source links.",
        "tools": ["Web search", "LLM"],
        "memory": "Session memory",
        "memory_detail": "Saved to session history; web search results are uncached.",
    },
    "repo_onboarding": {
        "description": "Fetches GitHub repo trees and metadata to write developer onboarding guides.",
        "tools": ["GitHub API", "LLM"],
        "memory": "Session memory + shared cache",
        "memory_detail": "Cached for 1 hour per repository URL.",
    },
    "cve_impact": {
        "description": "Analyzes repo dependencies against known CVEs to evaluate security risk.",
        "tools": ["GitHub API", "Web search", "LLM"],
        "memory": "Session memory + shared cache",
        "memory_detail": "Cached for 1 hour per repository URL.",
    },
    "issue_fix_planner": {
        "description": "Generates step-by-step implementation plans for GitHub issues without code emission.",
        "tools": ["GitHub API", "Web search", "LLM"],
        "memory": "Session memory + shared cache",
        "memory_detail": "Cached for 1 hour per issue URL.",
    },
    "do_i_care": {
        "description": "Scores each headline for relevance to a profile, keeps the top few, and explains why each matters and what to do.",
        "tools": ["LLM"],
        "memory": "Session memory",
        "memory_detail": "Saved to session history.",
    },
    "opportunity_scout": {
        "description": "Compares a developer's public GitHub repositories against current job-market search results to suggest skill gaps, roles and a project.",
        "tools": ["GitHub API", "Web search", "LLM"],
        "memory": "Session memory",
        "memory_detail": "Saved to session history.",
    },
    "security_audit": {
        "description": "Consolidates repo onboarding and CVE risk evaluations into a single security report.",
        "tools": ["GitHub API", "Web search", "LLM"],
        "memory": "Session memory + shared cache",
        "memory_detail": "Cached for 1 hour per repository URL.",
    },
}


def render_agent_info(agent_key: str) -> None:
    """Render metadata headers for selected agent."""
    info = AGENT_INFO[agent_key]
    st.caption(info["description"])
    
    col1, col2 = st.columns([2, 3])
    with col1:
        st.markdown("**Tools:**")
        for tool in info["tools"]:
            st.badge(tool, icon=TOOL_ICONS.get(tool, ":material/build:"), color="gray")
    with col2:
        st.markdown("**Memory Pattern:**")
        st.badge(info["memory"], icon=":material/memory:", color="green")
    
    st.caption(f"ℹ️ {info['memory_detail']}")
    st.divider()


def _rate_limit_ok() -> bool:
    """Enforce the per-session cooldown/cap and the per-client hourly request limit."""
    count = st.session_state.get("request_count", 0)
    if count >= MAX_REQUESTS_PER_SESSION:
        st.error(
            f":material/block: Session limit reached ({MAX_REQUESTS_PER_SESSION} requests). "
            "Please try again later."
        )
        return False

    elapsed = time.time() - st.session_state.get("last_request_at", 0)
    if elapsed < MIN_SECONDS_BETWEEN_REQUESTS:
        st.warning(f"Cooldown active: Wait {MIN_SECONDS_BETWEEN_REQUESTS - elapsed:.0f}s before retrying.")
        return False

    limiter, client = get_client_limiter(), get_client_id()
    if not limiter.allow(client):
        minutes = -(-limiter.retry_after(client) // 60)
        st.error(
            f":material/block: Request limit reached ({CLIENT_MAX_REQUESTS_PER_HOUR} per hour). "
            f"Please try again in about {minutes} minute{'s' if minutes != 1 else ''}."
        )
        return False

    st.session_state["last_request_at"] = time.time()
    st.session_state["request_count"] = count + 1
    return True


def _render_pill_examples(form_key: str, input_key: str, examples: list[tuple[str, str]]) -> None:
    """Render pill chips using native st.pills for pre-filling input forms."""
    if not examples:
        return
    
    labels = [ex[0] for ex in examples]
    val_map = {ex[0]: ex[1] for ex in examples}
    
    selected_label = st.pills("Quick Examples:", options=labels, key=f"{form_key}_pills")
    if selected_label:
        st.session_state[input_key] = val_map[selected_label]


def render_agent_tab(
    *,
    agent_key,
    form_key,
    label,
    placeholder,
    button_label,
    run,
    format_result,
    multiline=False,
    empty_message,
    examples=None,
):
    """Render a form-driven agent tab."""
    render_agent_info(agent_key)
    input_key = f"{form_key}_input"
    
    _render_pill_examples(form_key, input_key, examples or [])

    with st.form(form_key, border=False):
        if multiline:
            value = st.text_area(label, placeholder=placeholder, height=140, key=input_key)
        else:
            value = st.text_input(label, placeholder=placeholder, key=input_key)
            
        submitted = st.form_submit_button(button_label, icon=":material/play_arrow:", use_container_width=True)

    if not submitted:
        return

    value = (value or "").strip()
    if not value:
        st.warning(empty_message)
        return

    with st.spinner("Running agent workflow..."):
        # Caps the model calls this action can trigger and attributes them to this client.
        with request_scope(client_id=get_client_id()):
            result, cache_hit = run_with_memory(
                agent_key, value, run, get_shared_cache(), get_session_memory(), before_run=_rate_limit_ok
            )

    if result is None:
        return

    if cache_hit:
        st.badge("Retrieved from shared cache (0 API calls)", icon=":material/memory:", color="green")

    with st.container(border=True):
        # Links: only URLs the user typed, or structured URL fields of the result (advisory and
        # source links). Images are removed; everything else is shown as inert text.
        st.markdown(sanitize_markdown(format_result(result), collect_urls(result) | urls_in_text(value)))


def render_orchestrator_tab():
    """Render orchestrator view with dynamic execution trace."""
    render_agent_info("orchestrator")

    input_key = "orchestrator_form_input"
    examples = [(task if len(task) <= 46 else task[:43] + "...", task) for task in DEFAULT_TASKS]
    _render_pill_examples("orchestrator_form", input_key, examples)

    with st.form("orchestrator_form", border=False):
        task_value = st.text_area(
            "Orchestrator Goal",
            placeholder="e.g., Review https://github.com/user/repo for onboarding and security risks",
            height=90,
            key=input_key,
        )
        submitted = st.form_submit_button("Run Orchestrator Loop", icon=":material/smart_toy:", use_container_width=True)

    if not submitted:
        return

    task_value = (task_value or "").strip()
    if not task_value:
        st.warning("Please enter a goal description.")
        return

    if not _rate_limit_ok():
        return

    status = st.status("Initializing orchestrator execution loop...", expanded=True)

    def on_event(event: str, payload: dict) -> None:
        if event == "tool_call_start" and payload["tool"] == "recall_memory":
            status.update(label="Querying session memory...")
            status.write("🔍 **Recall Tool:** Querying session vector history...")
        elif event == "tool_call_start":
            tool = str(payload["tool"]).replace("`", "")
            tool_input = str(payload["args"].get("input", "")).replace("`", "'")
            status.update(label=f"Executing tool `{tool}`...")
            status.markdown(f"🛠️ **Tool Call:** `{tool}`\n```text\nInput: {tool_input}\n```")
        elif event == "memory_hit":
            status.write(f"⚡ `{str(payload['tool']).replace('`', '')}` output loaded from shared cache.")
        elif event == "tool_call_end":
            output = payload["output"]
            if isinstance(output, dict) and "error" in output:
                status.write(f"❌ `{str(payload['tool']).replace('`', '')}` returned error: {_md_escape(output['error'])}")
            else:
                status.write(f"✅ `{str(payload['tool']).replace('`', '')}` step completed successfully.")
        elif event == "tool_limit_reached":
            status.write("⚠️ Tool call limit reached — generating response with the results gathered so far.")
        elif event == "step_limit_reached":
            status.write("⚠️ Max step limit reached — generating response with partial outputs.")
        elif event == "final":
            status.update(label="Synthesizing final report...", state="running")
            status.write("📝 **Synthesizing Final Output...**")

    session = get_session_memory()
    with request_scope(client_id=get_client_id(), max_calls=get_budget().limits.orchestrator_request_calls):
        result = run_orchestrator(task_value, on_event=on_event, session=session, cache=get_shared_cache())

    if result.get("status") == "error":
        status.update(label="Execution Failed", state="error", expanded=True)
        st.error(result.get("error_message"))
        return

    session.record("orchestrator", task_value, result.get("report", ""))
    status.update(label="Orchestration Complete", state="complete", expanded=False)

    used = result.get("specialists_used", [])
    used_memory = result.get("used_memory", False)
    if used or used_memory:
        with st.container(horizontal=True, horizontal_alignment="left"):
            st.caption("Execution Path:")
            for u in used:
                st.badge(u["specialist"], icon=":material/smart_toy:", color="blue")
            if used_memory:
                st.badge("Session Memory", icon=":material/memory:", color="green")

    with st.container(border=True):
        st.markdown(
            sanitize_markdown(
                result.get("report", "No report generated"),
                set(result.get("sources", [])) | urls_in_text(task_value),
            )
        )


def render_memory_panel() -> None:
    """Render sidebar session metrics and memory logs."""
    session, cache = get_session_memory(), get_shared_cache()
    
    st.markdown("### Session Memory")
    
    req_count = st.session_state.get('request_count', 0)
    usage_pct = min(req_count / MAX_REQUESTS_PER_SESSION, 1.0)
    
    st.caption("Per-session request quota:")
    st.progress(usage_pct, text=f"{req_count}/{MAX_REQUESTS_PER_SESSION} requests used")

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Session Runs", len(session))
    with col2:
        st.metric("Shared Cache", f"{len(cache)} entries")

    runs = session.recent(5)
    if not runs:
        st.caption("No active runs recorded.")
    for r in runs:
        label = f"{r['agent']} · {r['age_seconds']}s ago" + (" (cached)" if r["cached"] else "")
        with st.expander(label, icon=":material/history:"):
            st.caption(f"**Input:** {_md_escape(r['input'][:100])}...")
            st.caption(_md_escape(r["summary"]))
            
    if runs and st.button("Clear Session Memory", icon=":material/delete:", use_container_width=True):
        session.clear()
        st.rerun()


# ---------------------------------------------------------
# Sidebar Layout
# ---------------------------------------------------------
with st.sidebar:
    st.markdown("## 🤖 Micro-Agents")
    st.markdown("**LLM + Tools + Memory**")
    
    with st.container(border=True):
        st.markdown("**System Architecture**")
        st.caption("Standalone Python agent framework without third-party dependencies.")
        st.badge("GPT-4o-mini", icon=":material/psychology:", color="blue")
        st.badge("Tools", icon=":material/build:", color="orange")
        st.badge("Memory", icon=":material/memory:", color="green")

    memory_slot = st.container()
    
    st.divider()
    st.markdown("[📄 View Source Repository](https://github.com/chaudharyviv/micro-agents)")


# ---------------------------------------------------------
# Header & Dashboard Metrics
# ---------------------------------------------------------
st.markdown('<p class="main-title">Micro-agents Dashboard</p>', unsafe_allow_html=True)
st.caption("Multi-agent orchestration powered by plain Python, OpenAI tools, and cached session memory.")

mcol1, mcol2, mcol3 = st.columns(3)
with mcol1:
    st.metric(label="Specialist Agents", value="7 Tools", delta="Modular")
with mcol2:
    st.metric(label="Orchestrator Engine", value="ReAct Loop", delta="Autonomous")
with mcol3:
    st.metric(label="System Model", value="gpt-4o-mini", delta="OpenAI")

st.divider()

# ---------------------------------------------------------
# Categorized Tab Navigation
# ---------------------------------------------------------
tab_labels = [
    "🎯 Orchestrator",
    "📝 Blog Scout",
    "🚀 Repo Onboarding",
    "🛡️ CVE Impact",
    "📋 Fix Planner",
    "🔍 Do I Care?",
    "💼 Opportunity Scout",
    "🔒 Security Audit",
]

(
    orchestrator_tab,
    blog_tab,
    onboarding_tab,
    cve_tab,
    planner_tab,
    care_tab,
    opportunity_tab,
    audit_tab,
) = st.tabs(tab_labels)

with orchestrator_tab:
    render_orchestrator_tab()

with blog_tab:
    render_agent_tab(
        agent_key="blog_scout",
        form_key="blog_scout_form",
        label="Topic",
        placeholder="e.g., machine learning, Python, web development (leave blank for trending)",
        button_label="Scout Blog Ideas",
        run=lambda topic: scout_blog_ideas(topic or "trending"),
        format_result=format_blog_ideas,
        empty_message="Enter a topic, or leave blank to use trending.",
        examples=[("Rust programming", "Rust programming"), ("Web accessibility", "Web accessibility")],
    )

with onboarding_tab:
    render_agent_tab(
        agent_key="repo_onboarding",
        form_key="repo_onboarding_form",
        label="Repository URL",
        placeholder="https://github.com/user/repo",
        button_label="Generate Onboarding Guide",
        run=generate_onboarding_guide,
        format_result=format_guide,
        empty_message="Please enter a repository URL.",
        examples=[("anthropic-sdk-python", "https://github.com/anthropics/anthropic-sdk-python")],
    )

with cve_tab:
    render_agent_tab(
        agent_key="cve_impact",
        form_key="cve_impact_form",
        label="Repository URL",
        placeholder="https://github.com/user/repo",
        button_label="Analyze CVE Risk",
        run=analyze_cve_impact,
        format_result=format_cve,
        empty_message="Please enter a repository URL.",
        examples=[("anthropic-sdk-python", "https://github.com/anthropics/anthropic-sdk-python")],
    )

with planner_tab:
    render_agent_tab(
        agent_key="issue_fix_planner",
        form_key="issue_planner_form",
        label="Issue URL",
        placeholder="https://github.com/user/repo/issues/123",
        button_label="Generate Plan",
        run=run_issue_fix_planner,
        format_result=format_plan,
        empty_message="Please enter an issue URL.",
        examples=[("anthropic-sdk-python #1", "https://github.com/anthropics/anthropic-sdk-python/issues/1")],
    )

with care_tab:
    render_agent_tab(
        agent_key="do_i_care",
        form_key="do_i_care_form",
        label="Headlines (one per line). Optional first line: Profile: what you care about",
        placeholder="Profile: backend engineer moving into data infrastructure\nEnter news items...",
        button_label="Analyze Relevance",
        run=lambda text: run_do_i_care([line.strip() for line in text.split("\n") if line.strip()]),
        format_result=format_analysis,
        multiline=True,
        empty_message="Please enter at least one headline.",
        examples=[
            (
                "Sample headlines",
                "New GPT-5 model shows 10x improvement\nAWS launches MLOps service\nLocal bakery wins award",
            )
        ],
    )

with opportunity_tab:
    render_agent_tab(
        agent_key="opportunity_scout",
        form_key="opportunity_scout_form",
        label="GitHub Username",
        placeholder="e.g., torvalds",
        button_label="Discover Opportunities",
        run=run_opportunity_scout,
        format_result=format_opportunities,
        empty_message="Please enter a GitHub username.",
        examples=[("torvalds", "torvalds"), ("gvanrossum", "gvanrossum")],
    )

with audit_tab:
    render_agent_tab(
        agent_key="security_audit",
        form_key="security_audit_form",
        label="Repository URL",
        placeholder="https://github.com/user/repo",
        button_label="Run Security Audit",
        run=generate_security_audit,
        format_result=format_audit,
        empty_message="Please enter a repository URL.",
        examples=[("anthropic-sdk-python", "https://github.com/anthropics/anthropic-sdk-python")],
    )

# Render memory status panel in sidebar (rendered last to catch current execution stats)
with memory_slot:
    render_memory_panel()