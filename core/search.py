"""Web search interface for micro-agents."""

import os
from dotenv import load_dotenv
from tavily import TavilyClient
from ddgs import DDGS


def search(query: str, max_results: int = 5) -> list[dict]:
    """
    Perform a web search.

    Attempts Tavily API first, falls back to DuckDuckGo if unavailable.

    Args:
        query: The search query string
        max_results: Maximum number of results to return (default: 5)

    Returns:
        A list of search result dicts with keys: title, url, snippet

    Raises:
        Exception: If both Tavily and DuckDuckGo backends fail
    """
    load_dotenv()
    tavily_key = os.getenv("TAVILY_API_KEY")

    # Try Tavily first
    if tavily_key:
        try:
            client = TavilyClient(api_key=tavily_key)
            response = client.search(query, max_results=max_results)

            results = []
            for result in response.get("results", []):
                results.append(
                    {
                        "title": result.get("title", ""),
                        "url": result.get("url", ""),
                        "snippet": result.get("content", ""),
                    }
                )

            print(f"[Search] Tavily backend served request")
            return results
        except Exception as e:
            print(f"[Search] Tavily failed: {e}, attempting DuckDuckGo fallback...")

    # Fall back to DuckDuckGo
    try:
        ddgs = DDGS()
        results = list(ddgs.text(query, max_results=max_results))

        normalized = []
        for result in results:
            normalized.append(
                {
                    "title": result.get("title", ""),
                    "url": result.get("href", ""),
                    "snippet": result.get("body", ""),
                }
            )

        print(f"[Search] DuckDuckGo backend served request")
        return normalized
    except Exception as e:
        print(f"[Search] DuckDuckGo failed: {e}")
        raise Exception(f"All search backends failed. Last error: {e}")


web_search = search


if __name__ == "__main__":
    try:
        results = search("machine learning libraries Python")
        print(f"\nSmoke test: {len(results)} results returned")
        for i, result in enumerate(results[:2], 1):
            print(f"\n{i}. {result['title']}")
            print(f"   URL: {result['url']}")
            print(f"   Snippet: {result['snippet'][:100]}...")
    except Exception as e:
        print(f"Smoke test failed: {e}")
