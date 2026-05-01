from agentnexus.core.llm import AgentLLM
from agentnexus.memory.short_term import ShortTermMemory
from agentnexus.memory.long_term import LongTermMemory
from agentnexus.rag.chroma_client import get_embedding_model


EXTRACT_PROMPT = """从以下对话中提取结构化记忆。只输出 JSON，不要任何其他文字。

对话:
Q: {question}
A: {answer}

输出格式（严格的 JSON）:
{{
  "user_preference": [{content}],  // 用户明确表达的偏好（技术选型、工具选择等），没有则为空数组
  "entity_fact": [{content}],       // 关于具体实体/技术/人的事实，没有则为空数组
  "conclusion": [{content}]         // 关键结论、决策、行动结果，没有则为空数组
}}

JSON:"""


SUMMARIZE_PROMPT = """将以下对话历史总结为一段简洁摘要，保留:
1. 用户的问题和意图
2. 关键事实和数据
3. 做出的决策或结论
4. 用户的偏好（如有）

对话:
{history}

摘要:"""


class MemoryManager:
    def __init__(self, session_id: str, llm=None):
        self.session_id = session_id
        self.short_term = ShortTermMemory()
        self.long_term = LongTermMemory()
        self._llm = llm or AgentLLM()
        self._embed_model = get_embedding_model()

    def init_session(self, question: str) -> str:
        query_vec = self._embed_model.encode(question, normalize_embeddings=True).tolist()
        memories = self.long_term.search(query_embedding=query_vec, limit=3, min_similarity=0.4)
        if not memories:
            return ""

        parts = []
        for m in memories:
            label = {"user_preference": "偏好", "entity_fact": "事实", "conclusion": "结论", "conversation": "历史"}.get(m["category"], m["category"])
            parts.append(f"- [{label}] {m['content']}")
        return "相关历史记忆:\n" + "\n".join(parts) + "\n"

    def append(self, role: str, content: str):
        self.short_term.append(role, content)

    def maybe_compact(self, threshold: int = 3000):
        tokens = self.short_term.estimate_tokens()
        if tokens < threshold:
            return

        all_msgs = self.short_term.get_all()
        if len(all_msgs) <= 4:
            return

        history_text = "\n".join(f"{m['role']}: {m['content'][:600]}" for m in all_msgs[:-4])
        if not history_text.strip():
            return

        prompt = SUMMARIZE_PROMPT.format(history=history_text)
        summary = self._llm.think([{"role": "user", "content": prompt}]) or ""
        if summary:
            self.short_term.compact(summary.strip())

    def conclude(self, question: str, answer: str):
        if not answer:
            return

        prompt = EXTRACT_PROMPT.replace("{question}", question).replace("{answer}", answer[:1500])
        response = self._llm.think([{"role": "user", "content": prompt}]) or "{}"

        try:
            import json as _json
            data = _json.loads(response.strip().lstrip("```json").rstrip("```").strip())
        except Exception:
            data = {}

        for category, importance in [
            ("user_preference", 0.9),
            ("entity_fact", 0.7),
            ("conclusion", 0.8),
        ]:
            for item in data.get(category, []):
                if not isinstance(item, str) or len(item) < 5:
                    continue
                vec = self._embed_model.encode(item, normalize_embeddings=True).tolist()
                self.long_term.save(
                    session_id=self.session_id,
                    content=item,
                    category=category,
                    importance=importance,
                    embedding=vec,
                )
