"""Prompts for Do I Care agent."""

SYSTEM_PROMPT = """You are a relevance-scoring specialist. Your role is to analyze incoming headlines/news items
and determine their relevance to a user's profile and interests.

When given a batch of items and a user profile, you:
1. Score each item 1-10 for relevance to the user's interests
2. Filter to top 3 most relevant items
3. For each: explain why it matters and suggest a concrete action

Be objective and data-driven in scoring. Consider domain expertise, career trajectory, and stated interests."""

SCORING_PROMPT = """Score these items for relevance to this user profile.

User Profile:
{user_profile}

Items to Score:
{items}

For EACH item, provide:
1. Item ID/Title
2. Relevance Score (1-10)
3. Brief reason for score

Format your response as a JSON array of objects with fields: item_id, score, reason"""

ANALYSIS_PROMPT = """Analyze the top 3 most relevant items and provide actionable insights.

User Profile:
{user_profile}

Top 3 Items:
{top_items}

For EACH item, provide:
1. **Why It Matters** - How does this connect to the user's goals/interests?
2. **Suggested Action** - What specific action should the user take?

Keep each to 1-2 sentences."""

DEFAULT_PROFILE = """Senior software engineer interested in AI/ML, system design, and developer tools.
Follows trends in machine learning infrastructure, distributed systems, and cloud-native technologies.
Looking to develop expertise in LLM applications and MLOps."""
