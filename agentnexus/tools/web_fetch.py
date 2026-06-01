"""Web fetch via Tavily extract API — fetch full content from URLs."""

from __future__ import annotations

try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None  # type: ignore[assignment,misc]

from time import monotonic

from agentnexus.core.config import get_settings

_tavily_client: TavilyClient | None = None

# --- Fetch result cache (TTL-based) ---
_CACHE_TTL_SEC = 300  # 5 minutes
_cache: dict[tuple, tuple[float, str]] = {}


def _make_cache_key(
    urls: tuple[str, ...],
    extract_depth: str | None,
    fmt: str,
) -> tuple:
    """Build a hashable cache key from fetch parameters."""
    return (urls, extract_depth, fmt)


def _cache_get(key: tuple) -> str | None:
    """Return cached result if still valid, else evict and return None."""
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, result = entry
    if monotonic() - ts > _CACHE_TTL_SEC:
        del _cache[key]
        return None
    return result


def _cache_set(key: tuple, result: str) -> None:
    """Store result in cache with current timestamp."""
    _cache[key] = (monotonic(), result)
    # Evict oldest entries if cache grows too large
    if len(_cache) > 128:
        oldest_key = min(_cache, key=lambda k: _cache[k][0])
        del _cache[oldest_key]


def clear_fetch_cache() -> None:
    """Clear the entire fetch result cache."""
    _cache.clear()


def _get_client() -> TavilyClient | None:
    global _tavily_client
    api_key = get_settings().tavily_api_key.get_secret_value()
    if not api_key:
        return None
    if _tavily_client is None:
        _tavily_client = TavilyClient(api_key=api_key)
    return _tavily_client


def web_fetch_structured(
    urls: str | list[str],
    extract_depth: str | None = None,
    fmt: str = "markdown",
) -> list[dict]:
    """Fetch content from URLs and return structured results.

    Each result dict: url, title, content.
    Failed URLs are collected in a separate list.
    Degrades gracefully: empty list on failure.
    """
    client = _get_client()
    if client is None:
        return []

    if isinstance(urls, str):
        url_list = [urls]
    else:
        url_list = list(urls)

    if not url_list:
        return []

    depth = extract_depth or "basic"

    for attempt in range(2):
        try:
            response = client.extract(
                urls=url_list,
                extract_depth=depth,
                format=fmt,
            )
            break
        except Exception:
            if attempt == 0:
                if depth == "advanced":
                    depth = "basic"
                continue
            return []

    results = response.get("results", [])
    failed = response.get("failed_results", [])

    structured: list[dict] = []
    for r in results:
        url = r.get("url", "")
        content = r.get("raw_content") or r.get("content", "")
        structured.append({
            "url": url,
            "title": r.get("title", ""),
            "content": content,
        })

    # Attach failed URLs info to the last result for downstream formatting
    if failed:
        failed_urls = [f.get("url", "unknown") for f in failed if isinstance(f, dict)]
        if not failed_urls and isinstance(failed[0], str):
            failed_urls = failed
        if structured:
            structured[-1]["_failed_urls"] = failed_urls
        else:
            structured.append({"url": "", "title": "", "content": "",
                               "_failed_urls": failed_urls})

    return structured


def web_fetch(
    urls: str | list[str],
    extract_depth: str | None = None,
    format: str = "markdown",
) -> str:
    """Fetch full content from URLs and return formatted text.

    Args:
        urls: 要抓取的URL（单个字符串或URL列表）
        extract_depth: 提取深度 ("basic", "advanced", None=默认basic)
        format: 输出格式 ("markdown", "text")
    """
    if format not in ("markdown", "text"):
        format = "markdown"

    valid_depths = {"basic", "advanced"}
    if extract_depth is not None and extract_depth not in valid_depths:
        extract_depth = None

    # Check cache (single-URL only)
    url_list = [urls] if isinstance(urls, str) else list(urls)
    depth = extract_depth or "basic"
    if len(url_list) == 1:
        cache_key = _make_cache_key(tuple(url_list), depth, format)
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    results = web_fetch_structured(
        urls,
        extract_depth=extract_depth,
        fmt=format,
    )

    if not results:
        client = _get_client()
        if client is None:
            return "网页抓取未配置 (请在 config.yaml 中设置 tavily_api_key)"
        return "未能抓取到任何内容。"

    parts = []
    failed_urls: list[str] = []

    for r in results:
        # Collect failed URLs from the last result
        if "_failed_urls" in r:
            failed_urls = r.pop("_failed_urls")
            continue

        title = r.get("title", "")
        url = r.get("url", "")
        content = r.get("content", "")

        if title:
            parts.append(f"[{title}]\nURL: {url}\n\n{content}")
        else:
            parts.append(f"URL: {url}\n\n{content}")

    if failed_urls:
        parts.append(f"\n[抓取失败] 以下URL未能成功获取:\n" +
                      "\n".join(f"  - {u}" for u in failed_urls))

    result = "\n\n---\n\n".join(parts)

    # Store in cache (single-URL fetch only, to avoid stale multi-URL cache)
    if len(url_list) == 1:
        cache_key = _make_cache_key(tuple(url_list), depth, format)
        _cache_set(cache_key, result)

    return result
