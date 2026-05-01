from serpapi import Client

from agentnexus.core.config import get_settings


def web_search(query: str) -> str:
    try:
        api_key = get_settings().serpapi_api_key.get_secret_value()
        if not api_key:
            return "网络搜索未配置 (请在 config.yaml 中设置 serpapi_api_key)"

        client = Client(api_key=api_key)
        results = client.search({
            "engine": "google",
            "q": query,
            "gl": "cn",
            "hl": "zh-cn",
        })

        if "answer_box_list" in results:
            return "\n".join(results["answer_box_list"])
        if "answer_box" in results and "answer" in results["answer_box"]:
            return results["answer_box"]["answer"]
        if "knowledge_graph" in results and "description" in results["knowledge_graph"]:
            return results["knowledge_graph"]["description"]
        if "organic_results" in results and results["organic_results"]:
            # 如果没有直接答案，则返回前三个有机结果的摘要
            snippets = [
                f"[{i+1}] {res.get('title', '')}\n{res.get('snippet', '')}"
                for i, res in enumerate(results["organic_results"][:3])
            ]
            return "\n\n".join(snippets)
        
        return f"对不起，没有找到关于 '{query}' 的信息。"

    except Exception as e:
        return f"搜索时发生错误: {e}"