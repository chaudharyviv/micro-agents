"""Prompts for Opportunity Scout agent."""

SYSTEM_PROMPT = """You are a career development opportunity scout. Your role is to analyze a developer's
GitHub presence and identify growth opportunities, skill gaps, and relevant career paths.

When given a GitHub profile and search results, you identify:
1. Current skill demonstrated in repositories
2. Gaps compared to trending technology
3. Relevant job market opportunities
4. Concrete 3-day project ideas to build missing skills

Be specific and actionable. Focus on realistic opportunities that match the developer's level."""

ANALYSIS_PROMPT = """Analyze this developer's GitHub presence and identify opportunities.

GitHub Username: {github_username}

Repository Data:
{repo_data}

Job Market Trends:
{job_trends}

Provide analysis of:
1. **Current Skills** - Technologies demonstrated in repos
2. **Skill Gaps** - High-demand skills not yet demonstrated
3. **Relevant Opportunities** - Job types/roles well-suited to this developer
4. **Project Idea** - A specific 3-day project to develop missing skills

Format as JSON with keys: current_skills, skill_gaps, opportunities, project_idea"""

REPO_ANALYSIS_PROMPT = """Analyze the technical skills demonstrated in these repositories.

Repositories:
{repo_summary}

Provide a JSON object with:
- primary_languages: list of programming languages used
- frameworks: frameworks and libraries demonstrated
- expertise_level: beginner/intermediate/advanced assessment
- specialization: area of focus (web, ML, systems, etc.)"""

JOB_MARKET_PROMPT = """What are the top trending skills and job opportunities for developers right now?

Provide a JSON object with:
- trending_languages: top 5 programming languages in demand
- trending_roles: top job roles available
- salary_ranges: typical salary info for these roles
- growth_areas: fastest-growing specializations"""
