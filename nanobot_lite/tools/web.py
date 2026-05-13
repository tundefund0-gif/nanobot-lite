"""Web search and fetch tools."""
from __future__ import annotations

from typing import Any

from nanobot_lite.tools.base import Tool, ToolResult
from nanobot_lite.utils.helpers import web_search, get_page_content


async def search_web(query: str, num_results: int = 5) -> ToolResult:
    """
    Search the web using DuckDuckGo.

    Args:
        query: The search query
        num_results: Number of results to return (default: 5, max: 10)
    """
    try:
        num_results = min(max(1, num_results), 10)
        results = web_search(query, num_results=num_results)

        if not results:
            return ToolResult(content="No results found.")

        if results and "error" in results[0]:
            return ToolResult(content=f"Search error: {results[0]['error']}", success=False)

        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"[{i}] {r.get('title', 'No title')}")
            lines.append(f"    URL: {r.get('url', 'N/A')}")
            snippet = r.get("snippet", "")
            if snippet:
                lines.append(f"    {snippet[:200]}")
            lines.append("")

        return ToolResult(content="\n".join(lines))

    except Exception as e:
        return ToolResult(content=f"Search failed: {e}", success=False, error=str(e))


async def fetch_url(url: str, timeout: int = 10) -> ToolResult:
    """
    Fetch content from a URL.

    Args:
        url: The URL to fetch
        timeout: Timeout in seconds (default: 10)
    """
    try:
        content = get_page_content(url)
        if not content or content.startswith("Error"):
            return ToolResult(content=f"Failed to fetch: {content}", success=False)

        # Truncate if too long
        max_chars = 8000
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n[Content truncated — {len(content)} total chars]"

        return ToolResult(content=content)

    except Exception as e:
        return ToolResult(content=f"Fetch failed: {e}", success=False, error=str(e))


def create_web_tools() -> list[Tool]:
    return [
        Tool(
            name="web_search",
            description=(
                "Search the web using DuckDuckGo. "
                "Returns a list of search results with titles, URLs, and snippets. "
                "Use this when you need current information or don't know something. "
                "Best for factual queries, news, product reviews, etc."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query (be specific for better results)",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results to return (1-10, default: 5)",
                    },
                },
                "required": ["query"],
            },
            handler=search_web,
        ),
        Tool(
            name="fetch_url",
            description=(
                "Fetch and extract readable content from a URL. "
                "Use this after web_search to get more details from a specific page. "
                "Returns the page's main content, stripped of navigation and ads. "
                "Content is truncated at ~8000 characters."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 10)",
                    },
                },
                "required": ["url"],
            },
            handler=fetch_url,
        ),
    ]
