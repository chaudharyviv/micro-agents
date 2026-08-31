"""Prompts for Repo Onboarding agent."""

SYSTEM_PROMPT = """You are an expert technical onboarding specialist. Your role is to analyze
GitHub repositories and create comprehensive, beginner-friendly contributor onboarding guides.

When given repository information (README, file structure, tech stack), you generate practical
onboarding guides that include:
1. Project overview - what does this project do?
2. Technology stack - languages, frameworks, tools
3. Getting started - setup steps for local development
4. Project structure - directory layout and key files
5. Key concepts - important architectural patterns
6. Development workflow - how to contribute (PRs, branches, testing)
7. Common tasks - how to run tests, build, deploy

Focus on clarity and actionability. Assume the reader is a competent developer but new to this project.
Do not invent information - only use facts from the provided repository data."""

USER_PROMPT_TEMPLATE = """Please create a comprehensive onboarding guide for this GitHub repository:

Repository URL: {repo_url}

Repository Data:
{repo_data}

Generate the guide as a JSON object with the following structure:
{{
  "project_name": "name of the project",
  "overview": "1-2 sentence description of what this project does",
  "tech_stack": ["language1", "framework1", "tool1", "..."],
  "setup_steps": [
    "Step 1: description with shell commands if applicable",
    "Step 2: description",
    "..."
  ],
  "project_structure": {{
    "directory": "description of what's in this directory",
    "...": "..."
  }},
  "key_concepts": [
    "Important architectural pattern or design decision",
    "..."
  ],
  "development_workflow": {{
    "branching_strategy": "description",
    "testing": "how to run tests",
    "building": "how to build the project",
    "deploying": "how to deploy (if applicable)"
  }},
  "common_tasks": {{
    "run_tests": "command",
    "start_dev_server": "command",
    "build": "command",
    "lint": "command"
  }},
  "resources": [
    "link to documentation or important files"
  ]
}}

Ensure all JSON is valid and complete. Do not invent or hallucinate information about the repository.
Only use the provided repository data to construct the guide."""

DEFAULT_REPOS = [
    "https://github.com/anthropics/anthropic-sdk-python",
    "https://github.com/gradio-app/gradio",
]
