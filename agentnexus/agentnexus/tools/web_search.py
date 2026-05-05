"""Web search via Tavily API — structured results with URL dedup and degradation."""

from __future__ import annotations

from tavily import TavilyClient

from agentnexus.core.config import get_settings

_tavily_client: TavilyClient | None = None
_seen_urls: set[str] = set()


def _get_client() -> TavilyClient | None:
    global _tavily_client
    api_key = get_settings().tavily_api_key.get_secret_value()
    if not api_key:
        return None
    if _tavily_client is None:
        _tavily_client = TavilyClient(api_key=api_key)
    return _tavily_client


def _pick_depth(query: str) -> str:
    """Use 'basic' for simple factual lookups, 'advanced' for complex queries."""
    lower = query.lower()
    complex_kw = ("对比", "分析", "调研", "最新", "趋势", "评价", "优缺点",
                  "compare", "analyze", "review", "latest", "trend", "pros and cons")
    if any(kw in lower for kw in complex_kw):
        return "advanced"
    if len(query) > 30:
        return "advanced"
    if any(kw in lower for kw in ("2025", "2026", "最新", "latest")):
        return "advanced"
    return "basic"


def web_search_structured(
    query: str,
    max_results: int = 5,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    include_raw_content: bool | None = None,
) -> list[dict]:
    """Search the web and return structured results.

    Each result dict: title, url, content, score, published_date.
    Results deduplicated by URL within the session. Low-score results filtered.
    Degrades gracefully: empty list on failure.
    """
    global _seen_urls

    client = _get_client()
    if client is None:
        return []

    depth = _pick_depth(query)
    # Fetch raw page content in advanced mode for better quality
    if include_raw_content is None:
        include_raw_content = (depth == "advanced")

    for attempt in range(2):
        try:
            response = client.search(
                query,
                search_depth=depth,
                max_results=max_results,
                include_domains=include_domains or [],
                exclude_domains=exclude_domains or [],
                include_raw_content=include_raw_content,
            )
            raw_results = response.get("results", [])
            break
        except Exception:
            if attempt == 0:
                if depth == "advanced":
                    depth = "basic"
                continue
            return []

    structured: list[dict] = []
    for r in raw_results:
        score = r.get("score", 0.0)
        if score < 0.3:
            continue
        url = r.get("url", "")
        if url and url in _seen_urls:
            continue
        if url:
            _seen_urls.add(url)
        # Use raw_content when available (full page) fallback to content (snippet)
        content = r.get("raw_content") or r.get("content", "")
        structured.append({
            "title": r.get("title", ""),
            "url": url,
            "content": content,
            "score": score,
            "published_date": r.get("published_date", ""),
        })

    return structured[:max_results]


def web_search(query: str) -> str:
    """Search the web and return formatted text (for tool interface)."""
    results = web_search_structured(query)
    if not results:
        client = _get_client()
        if client is None:
            return "网络搜索未配置 (请在 config.yaml 中设置 tavily_api_key)"
        return f"未找到关于 '{query}' 的信息。"

    parts = []
    for i, r in enumerate(results):
        title = r["title"]
        url = r.get("url", "")
        content = r["content"]
        date_str = f" ({r['published_date']})" if r.get("published_date") else ""
        score_str = f" [相关度: {r['score']:.2f}]" if r.get("score") else ""
        parts.append(
            f"[{i + 1}] {title}{date_str}{score_str}\n"
            f"URL: {url}\n"
            f"{content}"
        )
    return "\n\n".join(parts)
