from agentnexus.core.llm import AgentLLM
from agentnexus.memory.short_term import ShortTermMemory
from agentnexus.memory.long_term import LongTermMemory
from agentnexus.rag.chroma_client import get_embedding_model
from agentnexus.prompts import load_prompt


EXTRACT_PROMPT = load_prompt("memory_extract")


SUMMARIZE_PROMPT = load_prompt("memory_summarize")


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

        prompt = EXTRACT_PROMPT.format(question=question, answer=answer[:1500])
        response = self._llm.think([{"role": "user", "content": prompt}]) or "{}"

        try:
            import json
            data = json.loads(response.strip().lstrip("```json").rstrip("```").strip())
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
