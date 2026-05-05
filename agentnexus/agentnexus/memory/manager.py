import json
import re

from agentnexus.core.llm import AgentLLM
from agentnexus.memory.short_term import ShortTermMemory
from agentnexus.memory.long_term import LongTermMemory
from agentnexus.rag.chroma_client import get_embedding_model
from agentnexus.prompts import load_prompt

EXTRACT_PROMPT = load_prompt("memory_extract")
SUMMARIZE_PROMPT = load_prompt("memory_summarize")

MEMORY_CATEGORIES = {
    "user_preference": 0.9,
    "entity_fact": 0.7,
    "conclusion": 0.8,
    "conversation": 0.5,
}

CATEGORY_LABELS = {
    "user_preference": "偏好",
    "entity_fact": "事实",
    "conclusion": "结论",
    "conversation": "历史",
}

_PII_PATTERNS = [
    re.compile(r"[\w.-]+@[\w.-]+\.\w+"),
    re.compile(r"1[3-9]\d{9}"),
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(r"\b\d{15,19}\b"),
]


def _contains_pii(text: str) -> bool:
    return any(p.search(text) for p in _PII_PATTERNS)


class MemoryManager:
    def __init__(self, session_id: str, llm=None, enable_long_term: bool = True):
        self.session_id = session_id
        self.short_term = ShortTermMemory()
        self.long_term = LongTermMemory() if enable_long_term else None
        self._llm = llm or AgentLLM()
        self._embed_model = get_embedding_model()
        self._enable_long_term = enable_long_term

    def init_session(self, question: str) -> str:
        if not self.long_term:
            return ""
        query_vec = self._embed_model.encode(question, normalize_embeddings=True).tolist()
        memories = self.long_term.search(query_embedding=query_vec, limit=3, min_similarity=0.4)
        if not memories:
            return ""

        parts = []
        for m in memories:
            label = CATEGORY_LABELS.get(m["category"], m["category"])
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

        history_text = "\n".join(f"{m['role']}: {m['content']}" for m in all_msgs[:-4])
        if not history_text.strip():
            return

        prompt = SUMMARIZE_PROMPT.format(history=history_text)
        summary = self._llm.think([{"role": "user", "content": prompt}]) or ""
        if summary:
            self.short_term.compact(summary.strip())

    def conclude(self, question: str, answer: str, allow_memory: bool = True):
        if not answer or not self.long_term:
            return
        if not allow_memory:
            return
        if _contains_pii(question) or _contains_pii(answer):
            return

        prompt = EXTRACT_PROMPT.format(question=question, answer=answer)
        response = self._llm.think([{"role": "user", "content": prompt}]) or "{}"

        try:
            data = json.loads(response.strip().lstrip("```json").rstrip("```").strip())
        except Exception:
            data = {}

        for category, importance in MEMORY_CATEGORIES.items():
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
