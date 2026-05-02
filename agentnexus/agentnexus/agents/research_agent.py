from agentnexus.core.llm import AgentLLM
from agentnexus.tools.web_search import web_search
from agentnexus.rag.router import retrieve
from agentnexus.prompts import load_prompt


RESEARCH_PROMPT = load_prompt("research")


class ResearchAgent:
    def __init__(self):
        self._llm = AgentLLM()

    def run(self, query: str) -> str:
        kb_parts = []
        for r in retrieve(query, top_k=5):
            source = f"[{r.get('source', 'local')}]"
            if "file" in r:
                kb_parts.append(f"{source} {r['file']}:{r.get('line', '')}\n{r['text']}")
            else:
                kb_parts.append(f"{source} {r['text']}")

        kb = "\n\n".join(kb_parts) if kb_parts else "本地无相关知识。"
        try:
            web = web_search(query)
        except Exception:
            web = "网络搜索不可用"

        prompt = RESEARCH_PROMPT.format(kb=kb[:2000], web=web[:2000], query=query)
        return self._llm.think([{"role": "user", "content": prompt}]) or ""
