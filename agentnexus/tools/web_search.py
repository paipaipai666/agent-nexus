"""Web search via Tavily API — structured results with URL dedup and degradation."""

from __future__ import annotations

try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None  # type: ignore[assignment,misc]

from agentnexus.core.config import get_settings

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
    """仅作为默认兜底：LLM 未指定 search_depth 时使用。"""
    lower = query.lower()
    complex_kw = ("对比", "分析", "调研", "最新", "趋势", "评价", "优缺点",
                  "compare", "analyze", "review", "latest", "trend", "pros and cons")
    if any(kw in lower for kw in complex_kw):
        return "advanced"
    # 年份/时效性查询 → advanced
    if any(kw in lower for kw in ("2025", "2026", "最新", "latest")):
        return "advanced"
    return "basic"


def clear_seen_urls():
    """清空 URL 去重缓存，供 research_agent 和 ReAct 步级重置使用。"""
    global _seen_urls
    _seen_urls.clear()


def web_search_structured(
    query: str,
    max_results: int = 5,
    search_depth: str | None = None,
    time_range: str | None = None,
    topic: str = "general",
    include_answer: bool = False,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    include_raw_content: bool | None = None,
) -> list[dict]:
    """Search the web and return structured results.

    Each result dict: title, url, content, score, published_date.
    Results deduplicated by URL within the session. Low-score results filtered.
    Degrades gracefully: empty list on failure.

    When include_answer=True and Tavily returns an answer, it is attached
    as an 'answer' key on the first result dict.
    """
    global _seen_urls

    client = _get_client()
    if client is None:
        return []

    depth = search_depth or _pick_depth(query)
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
                time_range=time_range,
                topic=topic,
                include_answer=include_answer,
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
        content = r.get("raw_content") or r.get("content", "")
        structured.append({
            "title": r.get("title", ""),
            "url": url,
            "content": content,
            "score": score,
            "published_date": r.get("published_date", ""),
        })

    if include_answer and response.get("answer") and structured:
        structured[0]["answer"] = response["answer"]

    return structured[:max_results]


def web_search(
    query: str,
    max_results: int = 5,
    search_depth: str | None = None,
    time_range: str | None = None,
    topic: str = "general",
    include_answer: bool = False,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> str:
    """Search the web and return formatted text (for tool interface).

    Args:
        query: 搜索关键词
        max_results: 返回结果数 (1-20)
        search_depth: 搜索深度 ("basic", "advanced", None=自动)
        time_range: 时间范围 ("day", "week", "month", "year", None=不限)
        topic: 话题 ("general", "news")
        include_answer: 是否返回 Tavily 生成的直接答案
        include_domains: 限制搜索的域名列表
        exclude_domains: 排除的域名列表
    """
    max_results = max(1, min(20, max_results))
    valid_depths = {"basic", "advanced"}
    if search_depth is not None and search_depth not in valid_depths:
        search_depth = None
    if time_range not in (None, "day", "week", "month", "year"):
        time_range = None
    if topic not in ("general", "news"):
        topic = "general"

    depth = search_depth or _pick_depth(query)

    results = web_search_structured(
        query,
        max_results=max_results,
        search_depth=depth,
        time_range=time_range,
        topic=topic,
        include_answer=include_answer,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
    )

    if not results:
        client = _get_client()
        if client is None:
            return "网络搜索未配置 (请在 config.yaml 中设置 tavily_api_key)"
        return f"未找到关于 '{query}' 的信息。"

    parts = []

    if results[0].get("answer"):
        parts.append(f"[直接答案] {results[0]['answer']}\n")

    for i, r in enumerate(results):
        title = r.get("title", "")
        url = r.get("url", "")
        content = r.get("content", "")
        date_str = f" ({r['published_date']})" if r.get("published_date") else ""
        score_str = f" [相关度: {r['score']:.2f}]" if r.get("score") else ""
        parts.append(
            f"[{i + 1}] {title}{date_str}{score_str}\n"
            f"URL: {url}\n"
            f"{content}"
        )
    return "\n\n".join(parts)
