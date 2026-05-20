import json
import re
import time
from pathlib import Path

from agentnexus.core.config import get_settings
from agentnexus.core.judge_llm import get_judge_llm
from agentnexus.core.llm import AgentLLM
from agentnexus.memory.long_term import LongTermMemory
from agentnexus.memory.short_term import ShortTermMemory
from agentnexus.prompts import load_prompt
from agentnexus.rag.chroma_client import get_embedding_model


def _extract_xml_tag(text: str, tag: str) -> str | None:
    """Extract content between <tag> and </tag> from text. Returns None if not found."""
    pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(1) if match else None

EXTRACT_PROMPT = load_prompt("memory_extract")
SUMMARIZE_PROMPT = load_prompt("memory_summarize")

# Tool results that can be regenerated / re-fetched — safe to microcompact
_RECOVERABLE_TOOLS = frozenset({
    "read", "bash", "grep", "glob", "web_search", "web_fetch",
    "edit", "write", "search",
})

MEMORY_CATEGORIES = {
    "user_preference": 0.9,
    "entity_fact": 0.7,
    "conclusion": 0.8,
    "conversation": 0.5,
    "task_progress": 0.7,
    "error_pattern": 0.8,
    "tool_preference": 0.6,
}

CATEGORY_LABELS = {
    "user_preference": "偏好",
    "entity_fact": "事实",
    "conclusion": "结论",
    "conversation": "历史",
    "task_progress": "进展",
    "error_pattern": "错误模式",
    "tool_preference": "工具偏好",
}

_PII_PATTERNS = [
    re.compile(r"[\w.-]+@[\w.-]+\.\w+"),
    re.compile(r"1[3-9]\d{9}"),
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(r"\b\d{15,19}\b"),
]


def _parse_tool_message(content: str) -> tuple[str | None, str | None]:
    """Parse a tool message to extract tool name and params."""
    m = re.match(r"Action:\s*([\w-]+)\[([^\]]*)\]", content)
    if m:
        return m.group(1), m.group(2)
    return None, None


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
        self._compact_failures: int = 0
        self._circuit_open: bool = False
        self._compacting: bool = False
        settings = get_settings()
        if "/" in settings.chroma_persist_dir:
            base = settings.chroma_persist_dir.rsplit("/", 1)[0]
        else:
            base = str(Path(settings.chroma_persist_dir).parent)
        self._offload_dir = f"{base}/offload"
        self._settings = settings
        # Compact threshold: 40% of model context, floor 3000 tokens
        ctx_max = self._resolve_ctx_max()
        self._compact_threshold = max(3000, int(ctx_max * 0.8)) if ctx_max else 3000

    @staticmethod
    def _resolve_ctx_max() -> int | None:
        """Query LiteLLM for the current model's max input tokens."""
        try:
            from litellm import get_model_info
            model_id = get_settings().llm_model_id
            info = get_model_info(model_id)
            return info.get("max_input_tokens") or None
        except Exception:
            return None

    def init_session(self, question: str) -> str:
        if not self.long_term:
            return ""
        ltm_limit = 5
        ltm_similarity = 0.5
        # Use the question directly — don't pollute embedding with noisy concatenation
        query_text = question
        if self.short_term:
            recent = self.short_term.get_all()
            if recent:
                summary = self.short_term.get_summary()
                if summary:
                    # Prepend summary for richer context when available
                    query_text = f"{summary[:300]} {question}"
        query_vec = self._embed_model.encode(query_text, normalize_embeddings=True).tolist()
        memories = self.long_term.search(
            query_embedding=query_vec, limit=ltm_limit, min_similarity=ltm_similarity)
        if not memories:
            return ""

        parts = []
        for m in memories:
            label = CATEGORY_LABELS.get(m["category"], m["category"])
            score = m.get("_score", 0)
            star = "★★★" if score >= 0.7 else "★★☆" if score >= 0.5 else "★☆☆"
            parts.append(f"- {star} [{label}] {m['content']}")
        if not parts:
            return ""
        header = "相关历史记忆 (★越多越相关):\n" if any("★★★" in p for p in parts) else "相关历史记忆:\n"
        return header + "\n".join(parts) + "\n[提示] 用户分享个人信息时，请主动使用 memory_save 保存]\n"

    def append(self, role: str, content: str):
        # Layer 1: offload large tool results to disk
        if role == "tool" and self._settings.offload_enabled:
            threshold = self._settings.large_result_threshold
            if len(content.encode("utf-8", errors="replace")) > threshold:
                content = self._offload_large_result(content)
        self.short_term.append(role, content)
        # Recursive guard: don't trigger compaction from within compaction
        if not self._compacting:
            self.maybe_compact()

    def _offload_large_result(self, content: str) -> str:
        """Write large tool result to disk, return a stub with preview."""
        Path(self._offload_dir).mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000)
        fname = f"{self.session_id}_{ts}.txt"
        fpath = Path(self._offload_dir) / fname
        fpath.write_text(content, encoding="utf-8")
        preview = content[:500]
        return f"[工具结果已缓存] 文件: {fpath}\n预览(前500字符): {preview}"

    def microcompact(self):
        """Layer 2: clear recoverable tool results, preserving non-recoverable ones.

        GREEN/YELLOW: only clean recoverable tool results (Read, Bash, Grep, etc.)
        RED: also truncate old assistant messages to metadata stubs
        BREAK: also clean ALL tool messages (even non-recoverable)
        """
        all_msgs = self.short_term.get_all()
        cleaned = False
        for i, m in enumerate(all_msgs):
            if m["role"] == "tool":
                tool_name, _ = _parse_tool_message(m.get("content", ""))
                if tool_name and tool_name.lower() in _RECOVERABLE_TOOLS:
                    all_msgs[i] = {
                        **m,
                        "content": f"[工具结果已清理] 工具: {tool_name}",
                    }
                    cleaned = True
            elif m["role"] == "assistant":
                # Truncate long assistant messages to save space
                content = m.get("content", "")
                if len(content) > 2000:
                    all_msgs[i] = {
                        **m,
                        "content": content[:500] + "\n...[截断]...\n" + content[-500:],
                    }
                    cleaned = True
        if cleaned:
            self.short_term._messages.clear()
            for m in all_msgs:
                self.short_term._messages.append(m)

    def maybe_compact(self, threshold: int | None = None) -> int:
        """Check STM token usage and compact if over threshold.

        Returns tokens saved (positive = freed space), or 0 if no action taken.
        """
        if self._circuit_open:
            self.microcompact()
            return 0

        if threshold is None:
            threshold = self._compact_threshold

        tokens_before = self.short_term.estimate_tokens()
        if tokens_before < threshold:
            return 0

        all_msgs = self.short_term.get_all()
        if len(all_msgs) <= 4:
            return 0

        # Layer 2: microcompact before expensive LLM summarization
        self.microcompact()

        history_text = "\n".join(f"{m['role']}: {m['content']}" for m in all_msgs[:-4])
        if not history_text.strip():
            return 0

        self._compacting = True
        try:
            judge_llm = get_judge_llm()
            prompt = SUMMARIZE_PROMPT.format(history=history_text)
            response = judge_llm.think([{"role": "user", "content": prompt}]) or ""
            if not response:
                self._compact_failures += 1
                if self._compact_failures >= 3:
                    self._circuit_open = True
                return 0

            # Parse structured XML output: keep <summary>, discard <analysis>
            summary_content = _extract_xml_tag(response, "summary")
            if summary_content:
                self.short_term.compact(summary_content.strip())
            else:
                # Fallback: use the raw response as summary
                self.short_term.compact(response.strip())
            self._compact_failures = 0
        except Exception:
            self._compact_failures += 1
            if self._compact_failures >= 3:
                self._circuit_open = True
        finally:
            self._compacting = False
            tokens_after = self.short_term.estimate_tokens()
            return max(0, tokens_before - tokens_after)

    def conclude(self, question: str, answer: str, allow_memory: bool = True):
        try:
            self._conclude_impl(question, answer, allow_memory)
        except Exception:
            pass  # LTM extraction failure must never propagate to agent flow

    def _conclude_impl(self, question: str, answer: str, allow_memory: bool):
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
                if isinstance(item, dict):
                    item = item.get("content") or item.get("text") or ""
                if not isinstance(item, str) or len(item.strip()) < 5:
                    continue
                item = item.strip()
                vec = self._embed_model.encode(item, normalize_embeddings=True).tolist()
                self.long_term.save(
                    session_id=self.session_id,
                    content=item,
                    category=category,
                    importance=importance,
                    embedding=vec,
                )
