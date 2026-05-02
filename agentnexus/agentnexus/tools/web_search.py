from tavily import TavilyClient

from agentnexus.core.config import get_settings

_tavily_client = None


def web_search(query: str) -> str:
    global _tavily_client
    try:
        api_key = get_settings().tavily_api_key.get_secret_value()
        if not api_key:
            return "网络搜索未配置 (请在 config.yaml 中设置 tavily_api_key)"

        if _tavily_client is None:
            _tavily_client = TavilyClient(api_key=api_key)
        response = _tavily_client.search(query, search_depth="advanced", max_results=5)
        results = response.get("results", [])

        if not results:
            return f"对不起，没有找到关于 '{query}' 的信息。"

        snippets = [
            f"[{i+1}] {r.get('title', '')}\n{r.get('content', '')[:300]}"
            for i, r in enumerate(results[:5])
        ]
        return "\n\n".join(snippets)

    except Exception as e:
        return f"搜索时发生错误: {e}"
