"""Gradio app for Do I Care agent."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import gradio as gr
from agents.do_i_care.logic import run_do_i_care


def format_results(result: dict) -> str:
    """Format analysis results for display."""
    if result.get("status") == "error":
        return f"❌ Error: {result.get('error_message')}"

    top_items = result.get("top_items", [])
    original_count = result.get("original_count", 0)

    output = [f"# Relevance Analysis\n", f"**Analyzed:** {original_count} items\n\n"]

    for item in top_items:
        if "rank" in item:
            output.append(f"## #{item['rank']}: {item['headline']}\n")
            output.append(f"**Why It Matters:** {item.get('why_it_matters', 'N/A')}\n")
            output.append(f"**Action:** {item.get('suggested_action', 'N/A')}\n\n")

    for item in top_items:
        if "full_analysis" in item:
            output.append("---\n\n## Detailed Analysis\n")
            output.append(item["full_analysis"])

    return "\n".join(output)


def analyze_headlines(headlines_text: str) -> str:
    """Analyze headlines for relevance."""
    if not headlines_text or not headlines_text.strip():
        return "Please enter headlines (one per line)"

    headlines = [line.strip() for line in headlines_text.strip().split("\n") if line.strip()]

    if not headlines:
        return "Please enter at least one headline"

    result = run_do_i_care(headlines)
    return format_results(result)


if __name__ == "__main__":
    demo = gr.Interface(
        fn=analyze_headlines,
        inputs=gr.Textbox(
            label="Headlines/News Items",
            placeholder="Enter headlines (one per line)",
            lines=10,
        ),
        outputs=gr.Markdown(label="Relevance Analysis"),
        title="Do I Care?",
        description="Analyze news headlines for relevance to your profile",
        examples=[
            [
                """New GPT-5 model shows 10x improvement in reasoning tasks
Kubernetes 1.30 release with major networking improvements
AWS launches new MLOps service for model deployment
Local coffee shop opens downtown"""
            ]
        ],
    )

    demo.launch(share=False, server_name="127.0.0.1", server_port=7863, show_error=True)
