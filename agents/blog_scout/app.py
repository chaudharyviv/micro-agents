"""Gradio app for Blog Idea Scout agent."""

import sys
from pathlib import Path

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import gradio as gr
from agents.blog_scout.logic import scout_blog_ideas


def format_ideas(ideas: list[dict]) -> str:
    """Format blog ideas for display."""
    if not ideas:
        return "No ideas generated."

    output = []
    for i, idea in enumerate(ideas, 1):
        title = idea.get("title", "N/A")
        pitch = idea.get("pitch", "N/A")
        source_title = idea.get("source_title", "N/A")
        source_url = idea.get("source_url", "N/A")

        output.append(f"### Idea {i}: {title}\n")
        output.append(f"**Pitch:** {pitch}\n")
        output.append(f"**Source:** [{source_title}]({source_url})\n")

    return "\n".join(output)


def generate_ideas(topic: str = None) -> str:
    """Generate blog ideas for the given topic."""
    if not topic or not topic.strip():
        topic = None

    ideas = scout_blog_ideas(topic=topic)
    return format_ideas(ideas)


if __name__ == "__main__":
    demo = gr.Interface(
        fn=generate_ideas,
        inputs=gr.Textbox(
            label="Topic (optional)",
            placeholder="Enter a topic, or leave blank for trending topics",
            lines=1,
        ),
        outputs=gr.Markdown(label="Blog Ideas"),
        title="Blog Idea Scout",
        description="Generate blog ideas from trending topics or a specific topic",
        examples=[
            [""],
            ["machine learning"],
            ["cloud computing trends"],
        ],
    )

    demo.launch(share=False, server_name="127.0.0.1", server_port=7860, show_error=True)
