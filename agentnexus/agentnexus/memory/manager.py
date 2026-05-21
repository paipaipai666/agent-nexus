import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

from agentnexus.core.config import get_settings
from agentnexus.core.llm import AgentLLM
from agentnexus.memory.long_term import get_long_term_memory
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
        self.long_term = get_long_term_memory() if enable_long_term else None
        self._llm = llm or AgentLLM()
        self._embed_model = get_embedding_model()
        self._enable_long_term = enable_long_term
        self._compact_failures: int = 0
        self._circuit_open: bool = False
        self._microcompacts_since_open: int = 0
        self._compacting: bool = False
        self._last_api_call_ts: float = 0.0
        self._recent_reads: list[tuple[str, str, float]] = []  # (filepath, preview, ts)
        self._snip_freed_tokens: int = 0
        settings = get_settings()
        if "/" in settings.chroma_persist_dir:
            base = settings.chroma_persist_dir.rsplit("/", 1)[0]
        else:
            base = str(Path(settings.chroma_persist_dir).parent)
        self._offload_dir = f"{base}/offload"
        self._transcript_dir = f"{base}/transcripts"
        self._settings = settings
        ctx_max = self._resolve_ctx_max()
        if ctx_max:
            self._ctx_max = ctx_max
            self._compact_threshold = ctx_max - self._settings.autocompact_buffer_tokens
        else:
            self._ctx_max = 128000
            self._compact_threshold = 120000
        self._last_write_count: int = 0
        self._on_compact: Callable[[dict], None] | None = None
        self._on_after_compact: Callable[[], None] | None = None

    def estimate_stm_tokens(self) -> int:
        """Return current STM token estimate."""
        return self.short_term.estimate_tokens()

    def _fire_compact(self, event_type: str, **kwargs):
        """Fire compact event callback if set."""
        if self._on_compact:
            try:
                self._on_compact({"event": event_type, **kwargs})
            except Exception:
                pass

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

        # Always update snapshot — even if no memories match this query,
        # we need the baseline for future has_new_memories() checks.
        self._update_ltm_snapshot()

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

    def _update_ltm_snapshot(self):
        """Record the current LTM write counter as baseline for change detection."""
        if not self.long_term:
            return
        self._last_write_count = self.long_term.write_counter

    def has_new_memories(self) -> bool:
        """Check if new LTM entries exist since last init_session() / refresh.

        Pure query — does not mutate state. Snapshot is updated by
        init_session() / refresh_ltm_context() when context is actually reloaded.

        Uses write_counter since all LTM writes go through the singleton
        LongTermMemory instance (including memory_save tool).
        """
        if not self.long_term:
            return False
        return self.long_term.write_counter > self._last_write_count

    def refresh_ltm_context(self, question: str) -> str:
        """Reload LTM context after new memories are detected."""
        return self.init_session(question)

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

    def bridge_read(self, filepath: str, content_preview: str = ""):
        self._recent_reads.append((filepath, content_preview[:5000], time.time()))
        if len(self._recent_reads) > 20:
            self._recent_reads = self._recent_reads[-20:]

    def _restore_files(self):
        max_files = self._settings.post_compact_max_files
        per_file = self._settings.post_compact_token_per_file
        budget = self._settings.post_compact_token_budget
        if max_files <= 0 or not self._recent_reads:
            return
        seen: set[str] = set()
        recent: list[tuple[str, str]] = []
        for fp, preview, _ts in reversed(self._recent_reads):
            if fp not in seen:
                seen.add(fp)
                recent.insert(0, (fp, preview))
            if len(recent) >= max_files:
                break
        total_tokens = 0
        restored = 0
        for fp, preview in recent:
            if total_tokens + per_file > budget:
                break
            try:
                raw = Path(fp).read_text(encoding="utf-8")
                content = raw[:per_file * 4]
            except Exception:
                content = preview or f"[无法读取文件] {fp}"
            self.short_term.append("system", f"[恢复文件] {fp}\n{content}")
            total_tokens += per_file
            restored += 1
        if restored:
            self._fire_compact("file_restore", restored=restored, files=[fp for fp, _ in recent[:restored]])

    def _write_transcript(self):
        if not self._settings.transcript_enabled:
            return
        Path(self._transcript_dir).mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        fname = f"{self.session_id}_compact_{ts}.jsonl"
        fpath = Path(self._transcript_dir) / fname
        messages = self.short_term.get_all()
        lines = [json.dumps(m, ensure_ascii=False) for m in messages]
        fpath.write_text("\n".join(lines), encoding="utf-8")
        self._fire_compact("transcript_saved", path=str(fpath), message_count=len(messages))

    def mark_api_call(self):
        """Record that an API call just happened for time-based microcompact tracking."""
        self._last_api_call_ts = time.time()

    def snip(self, keep_recent: int = 10) -> int:
        if not self._settings.snip_enabled:
            return 0
        all_msgs = self.short_term.get_all()
        if len(all_msgs) <= keep_recent + 4:
            return 0
        tokens_before = self.short_term.estimate_tokens()
        removed = self.short_term.snip(keep_recent)
        if removed:
            tokens_after = self.short_term.estimate_tokens()
            freed = max(0, tokens_before - tokens_after)
            self._snip_freed_tokens += freed
            self._fire_compact("snip", removed=removed, freed_tokens=freed)
        return removed

    def microcompact_time_based(self, interval: int | None = None) -> bool:
        """Layer 3: time-decay based microcompact. Clears recoverable tool results
        when the last API call was more than `interval` seconds ago.

        Returns True if microcompact was performed.
        """
        if interval is None:
            interval = self._settings.time_microcompact_interval
        if self._last_api_call_ts <= 0:
            return False
        elapsed = time.time() - self._last_api_call_ts
        if elapsed < interval:
            return False
        tokens_before = self.short_term.estimate_tokens()
        self.microcompact()
        tokens_after = self.short_term.estimate_tokens()
        self._fire_compact("time_microcompact", tokens_before=tokens_before, elapsed=elapsed)
        return tokens_before != tokens_after

    def build_projection(self, messages: list[dict]) -> list[dict]:
        """Layer 4: non-destructive read-time projection. Returns a compressed view
        of messages without modifying STM. Called before every LLM API call.

        90% ctx used → mild compression. 95% → aggressive compression.
        """
        tokens = self.short_term.estimate_tokens()
        ratio = tokens / max(self._ctx_max, 1)

        if ratio < 0.90:
            return messages
        if ratio < 0.95:
            return self._project_mild(messages)
        return self._project_aggressive(messages)

    def _project_mild(self, messages: list[dict]) -> list[dict]:
        """90% threshold: truncate long messages, keep last 4 intact."""
        projected = []
        keep_recent = min(4, len(messages))
        for i, m in enumerate(messages):
            is_recent = i >= len(messages) - keep_recent
            content = m.get("content", "")
            if is_recent:
                projected.append(dict(m))
                continue
            if m["role"] in ("assistant", "tool") and len(content) > 1000:
                projected.append({
                    **m,
                    "content": content[:500] + "\n...[投影截断]...\n" + content[-500:],
                })
            else:
                projected.append(dict(m))
        return projected

    def _project_aggressive(self, messages: list[dict]) -> list[dict]:
        """95% threshold: clear recoverable tool results, truncate all assistants,
        insert boundary marker, keep last 3 intact."""
        projected = []
        keep_recent = min(3, len(messages))
        boundary_inserted = False

        for i, m in enumerate(messages):
            is_recent = i >= len(messages) - keep_recent
            role = m.get("role", "")

            if is_recent:
                if not boundary_inserted and len(projected) > 0:
                    projected.append({
                        "role": "system",
                        "content": "[上下文投影] 此标记之前的对话已被投影压缩。",
                    })
                    boundary_inserted = True
                projected.append(dict(m))
                continue

            if role == "tool":
                tool_name, _ = _parse_tool_message(m.get("content", ""))
                if tool_name and tool_name.lower() in _RECOVERABLE_TOOLS:
                    projected.append({
                        **m,
                        "content": f"[工具结果已投影清除] 工具: {tool_name}",
                    })
                else:
                    projected.append(dict(m))
            elif role == "assistant":
                content = m.get("content", "")
                projected.append({
                    **m,
                    "content": content[:500] + "\n...[投影压缩]...\n" + content[-500:] if len(content) > 1000
                    else content,
                })
            else:
                projected.append(dict(m))

        if not boundary_inserted:
            projected.insert(0, {
                "role": "system",
                "content": "[上下文投影] 对话上下文已通过读时投影压缩。",
            })
        return projected

    def microcompact(self):
        all_msgs = self.short_term.get_all()
        cleaned = False
        recoverable_indices = []
        for i, m in enumerate(all_msgs):
            if m["role"] == "tool":
                tool_name, _ = _parse_tool_message(m.get("content", ""))
                if tool_name and tool_name.lower() in _RECOVERABLE_TOOLS:
                    recoverable_indices.append(i)
        keep_last = 5
        skip_indices = set(recoverable_indices[-keep_last:]) if len(recoverable_indices) > keep_last else set(recoverable_indices)
        for i in recoverable_indices:
            if i in skip_indices:
                continue
            m = all_msgs[i]
            tool_name, _ = _parse_tool_message(m.get("content", ""))
            all_msgs[i] = {
                **m,
                "content": f"[工具结果已清理] 工具: {tool_name}",
            }
            cleaned = True
        for i, m in enumerate(all_msgs):
            if m["role"] == "assistant":
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

    def maybe_compact(self, threshold: int | None = None, custom_instructions: str = "",
                       is_auto: bool = True) -> int:
        """5-layer compaction pyramid. Returns tokens saved, or 0.

        is_auto=False enables manual /compact mode (accepts custom_instructions,
        does not suppress follow-up questions).
        """
        if self._circuit_open:
            self.microcompact()
            self._microcompacts_since_open += 1
            if self._microcompacts_since_open >= 5:
                logger.info("Circuit breaker reset after %d successful microcompacts",
                            self._microcompacts_since_open)
                self._circuit_open = False
                self._compact_failures = 0
                self._microcompacts_since_open = 0
                self._fire_compact("circuit_reset")
            else:
                tokens_after = self.short_term.estimate_tokens()
                self._fire_compact("circuit_active", tokens_after=tokens_after)
            return 0

        if threshold is None:
            threshold = self._compact_threshold
            if self._snip_freed_tokens > 0:
                threshold = max(threshold - self._snip_freed_tokens, threshold // 2)

        tokens_before = self.short_term.estimate_tokens()
        if tokens_before < threshold:
            if self._settings.time_microcompact_interval > 0:
                self.microcompact_time_based()
            return 0

        all_msgs = self.short_term.get_all()
        if len(all_msgs) <= 4:
            return 0

        # Layer 2: Snip
        self.snip()

        # Layer 3: Time-based microcompact
        if self._settings.time_microcompact_interval > 0:
            self.microcompact_time_based()

        # Layer 3b: MicroCompact before LLM summarization
        self.microcompact()

        # Full rewrite: send ALL messages to summarizer
        all_msgs_after = self.short_term.get_all()
        history_text = "\n".join(f"{m['role']}: {m['content']}" for m in all_msgs_after)
        if not history_text.strip():
            return 0

        # Layer 5: Kairos transcript backup before destructive compact
        self._write_transcript()

        augmented = history_text
        if custom_instructions:
            augmented = f"[压缩指令] {custom_instructions}\n\n{augmented}"

        self._fire_compact("start", tokens_before=tokens_before)

        self._compacting = True
        try:
            prompt = SUMMARIZE_PROMPT.format(history=augmented)
            response = self._llm.think([{"role": "user", "content": prompt}]) or ""
            if not response:
                self._compact_failures += 1
                if self._compact_failures >= 3:
                    self._circuit_open = True
                    self._microcompacts_since_open = 0
                    self._fire_compact("circuit_open")
                return 0

            summary_content = _extract_xml_tag(response, "summary")
            final_summary = (summary_content or response).strip()
            self.short_term.compact_full(final_summary, message_count=len(all_msgs_after),
                                         is_auto=is_auto)
            self._compact_failures = 0
            self._microcompacts_since_open = 0
            self._snip_freed_tokens = 0
            self._recent_reads.clear()

            # A3: File recovery after compact
            self._restore_files()

            # A6: System prompt rebuild hook
            if self._on_after_compact:
                try:
                    self._on_after_compact()
                except Exception:
                    pass

            tokens_after = self.short_term.estimate_tokens()
            self._fire_compact("complete", tokens_before=tokens_before, tokens_after=tokens_after)
            return max(0, tokens_before - tokens_after)
        except Exception:
            self._compact_failures += 1
            if self._compact_failures >= 3:
                self._circuit_open = True
                self._microcompacts_since_open = 0
                self._fire_compact("circuit_open")
            return 0
        finally:
            self._compacting = False

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
