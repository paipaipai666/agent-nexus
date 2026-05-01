from agentnexus.core.llm import AgentLLM
from agentnexus.tools.web_search import web_search
from agentnexus.rag.retriever import search_knowledge_base


class ResearchAgent:
    def __init__(self):
        self._llm = AgentLLM()

    def run(self, query: str) -> str:
        try:
            kb = search_knowledge_base(query)
        except Exception:
            kb = "知识库不可用"
        try:
            web = web_search(query)
        except Exception:
            web = "网络搜索不可用"

        prompt = f"""基于以下信息回答用户问题。如果信息不足，说明缺少什么。

知识库结果:
{kb[:2000]}

网页搜索结果:
{web[:2000]}

问题: {query}

回答:"""
        return self._llm.think([{"role": "user", "content": prompt}]) or ""
