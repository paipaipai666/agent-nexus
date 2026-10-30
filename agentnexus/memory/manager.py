"""Memory manager — session-scoped facade over the memory subsystems.

Responsibilities kept here:
  - wiring and configuration (STM/LTM/embed model lifecycle, ctx resolution)
  - append orchestration (offload large tool results, compaction trigger)
  - LTM context retrieval (init_session / has_new_memories / refresh)

Compaction state and logic live in ``compaction_engine.CompactionEngine``;
extraction lives in ``extraction_pipeline.MemoryExtractionPipeline``. Both
borrow the manager as their shared context (STM/LTM/LLM/settings/session).
"""

import logging
import threading
from pathlib import Path

from agentnexus.core.llm import AgentLLM
from agentnexus.core.memory_contracts import TurnMemory, dump_snapshot, restore_snapshot
from agentnexus.core.modules import DEFAULT, ModuleContext, get_module_host
from agentnexus.memory.compaction_engine import CompactionEngine
from agentnexus.memory.extraction import CATEGORY_LABELS
from agentnexus.memory.extraction_pipeline import MemoryExtractionPipeline
from agentnexus.memory.offload import offload_large_result


def get_embedding_model():
    """Load the default embedding backend only when explicitly requested."""
    from agentnexus.rag.embeddings import get_embedding_model as load

    return load()

logger = logging.getLogger(__name__)


class MemoryManager:
    """Session-scoped memory manager combining STM, LTM, compaction, and extraction.

    Owns shared resources (STM, LTM, LLM, embed model, settings) and
    delegates compaction to ``_engine`` and extraction to ``_pipeline``.
    """

    def __init__(self, session_id: str, llm=None, enable_long_term: bool = True,
                 workspace_path: str | None = None, *, modules=None,
                 short_term=DEFAULT, long_term=DEFAULT):
        self.session_id = session_id
        self.modules = modules if modules is not None else get_module_host()
        settings = self.modules.settings
        context = ModuleContext(settings=settings, llm=llm, session_id=session_id,
                                workspace_path=workspace_path)
        selected_stm = self.modules.optional("short_term", context) if short_term is DEFAULT else short_term
        self.retention_enabled = selected_stm is not None
        self.short_term = selected_stm if selected_stm is not None else TurnMemory()
        self.long_term = (
            self.modules.optional("long_term", context) if long_term is DEFAULT else long_term
        ) if enable_long_term else None
        self.project = None
        if workspace_path:
            try:
                from agentnexus.memory.project import ProjectMemory
                self.project = ProjectMemory(workspace_path)
            except Exception as e:
                logger.warning("Project memory init failed (non-fatal): %s", e)
        self._llm = llm
        self._embed_model = None
        self._embed_ready = threading.Event()
        self._enable_long_term = self.long_term is not None
        if "/" in settings.chroma_persist_dir:
            base = settings.chroma_persist_dir.rsplit("/", 1)[0]
        else:
            base = str(Path(settings.chroma_persist_dir).parent)
        self._offload_dir = f"{base}/offload"
        self._settings = settings
        self._engine = CompactionEngine(self)
        self._engine.transcript_dir = f"{base}/transcripts"
        self._engine.history_dir = f"{base}/history"
        self._pipeline = MemoryExtractionPipeline(self)
        self._last_write_count: int = 0
        if self.retention_enabled:
            threading.Thread(target=self._resolve_ctx_max_async, daemon=True, name="mem-ctx-resolve").start()

    def begin_turn(self) -> None:
        """Start with fresh working messages when conversation retention is disabled."""
        if not self.retention_enabled:
            self.short_term.clear()

    @property
    def llm(self):
        """Acquire a model client only when memory processing needs one."""
        if self._llm is None:
            self._llm = AgentLLM()
        return self._llm

    def export_snapshot(self) -> str:
        """Return a storage-compatible snapshot through the selected provider."""
        return dump_snapshot(self.short_term)

    def restore_snapshot(self, snapshot: str) -> bool:
        """Restore retained context without enabling a disabled conversation provider."""
        if not self.retention_enabled:
            return False
        restore_snapshot(self.short_term, snapshot)
        return True

    @property
    def ctx_max(self) -> int:
        """Return the model window resolved by the compaction engine."""
        return self._engine.ctx_max

    @property
    def effective_compact_threshold(self) -> int:
        """Return the threshold currently used by automatic compaction."""
        return self._engine.effective_threshold

    @property
    def compact_threshold(self) -> int:
        """Compaction token threshold. Assignment writes through to the engine."""
        return self._engine.compact_threshold

    @compact_threshold.setter
    def compact_threshold(self, value: int) -> None:
        """Set the engine's compaction threshold (mutates engine state, not manager state)."""
        self._engine.compact_threshold = value

    @property
    def on_compact(self):
        """Compact-event callback. Assignment writes through to the engine."""
        return self._engine.on_compact

    @on_compact.setter
    def on_compact(self, callback) -> None:
        """Set the engine's compact-event callback (mutates engine state, not manager state)."""
        self._engine.on_compact = callback

    @property
    def on_after_compact(self):
        """Post-compact callback. Assignment writes through to the engine."""
        return self._engine.on_after_compact

    @on_after_compact.setter
    def on_after_compact(self, callback) -> None:
        """Set the engine's post-compact callback (mutates engine state, not manager state)."""
        self._engine.on_after_compact = callback

    def _resolve_ctx_max_async(self) -> None:
        """Background thread: fill in compaction thresholds without blocking startup."""
        try:
            ctx_max = self._resolve_ctx_max()
        except Exception as exc:
            logger.debug("ctx_max resolution failed: %s", exc)
            return
        if ctx_max:
            self._engine.ctx_max = ctx_max
            self._engine.compact_threshold = ctx_max - self._settings.autocompact_buffer_tokens

    def _preload_embed_model(self):
        """Background thread: load embedding model without blocking startup."""
        try:
            self._embed_model = get_embedding_model()
        except Exception as exc:
            logger.warning("Embedding model preload failed: %s", exc)
        finally:
            self._embed_ready.set()

    def _get_embed_model(self, timeout: float = 30):
        """Load an embedding model only for legacy vector-based extraction."""
        if self._embed_model is not None:
            return self._embed_model
        self._embed_model = get_embedding_model()
        return self._embed_model

    def _resolve_ctx_max(self) -> int | None:
        """Resolve max input tokens from the capability registry."""
        try:
            from agentnexus.core.capabilities import resolve_ctx_max

            model_id, base_url = self._settings.get_active_llm_profile()[:2]
            return resolve_ctx_max(model_id, base_url or "")
        except Exception as e:
            logger.debug("Failed to resolve ctx_max: %s", e)
            return None

    def estimate_stm_tokens(self) -> int:
        """Return current STM token estimate.

        Prefers the incremental counter (O(1)) when available and populated,
        falls back to full re-encoding.
        """
        return self.short_term.estimate_tokens()

    def init_session(self, question: str) -> str:
        """Build session-start memory context: project index/state + LTM recall."""
        parts: list[str] = []
        project = getattr(self, "project", None)
        if project is not None:
            try:
                ctx = project.format_context()
                if ctx:
                    parts.append(ctx)
            except Exception as e:
                logger.debug("Project memory read failed: %s", e)
        ltm_block = self._ltm_context_block(question)
        if ltm_block:
            parts.append(ltm_block)
        return "\n".join(parts)

    def _ltm_context_block(self, question: str) -> str:
        if not self.long_term:
            return ""
        ltm_limit = 5
        ltm_similarity = 0.5
        query_text = question
        if self.short_term:
            recent = self.short_term.get_all()
            if recent:
                summary = self.short_term.get_summary()
                if summary:
                    query_text = f"{summary[:300]} {question}"
        memories = self.long_term.search_text(
            query_text, limit=ltm_limit, min_similarity=ltm_similarity)

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
        return header + "\n".join(parts) + "\n"

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

    def append(self, role: str, content: str, metadata: dict | None = None) -> None:
        from agentnexus.core.hooks import HookType, get_hook_manager

        hook_mgr = get_hook_manager()

        hook_mgr.fire(HookType.BEFORE_MEMORY_OP, {
            "op": "append", "role": role, "content": content,
        })

        if role == "tool" and self._settings.offload_enabled:
            threshold = self._settings.large_result_threshold
            if len(content.encode("utf-8", errors="replace")) > threshold:
                content = self._offload_large_result(content)
        self.short_term.append(role, content, metadata=metadata)
        if not self._engine.compacting:
            self.maybe_compact()

        hook_mgr.fire(HookType.AFTER_MEMORY_OP, {
            "op": "append", "role": role, "content": content,
        })

    def _offload_large_result(self, content: str) -> str:
        """Write large tool result to disk, return a stub with preview."""
        return offload_large_result(content, self._offload_dir, self.session_id)

    def maybe_compact(self, threshold: int | None = None, custom_instructions: str = "",
                      is_auto: bool = True) -> int:
        if not getattr(self, "retention_enabled", True):
            return 0
        saved = self._engine.maybe_compact(threshold, custom_instructions, is_auto)
        project = getattr(self, "project", None)
        if saved > 0 and project is not None:
            try:
                summary = self.short_term.get_summary()
                if summary:
                    project.update_state(summary)
                project.log_work("上下文压缩", f"释放约 {saved} tokens")
            except Exception as e:
                logger.debug("Project memory compaction hook failed: %s", e)
        return saved

    def snip(self, keep_recent: int = 10) -> int:
        if not getattr(self, "retention_enabled", True):
            return 0
        return self._engine.snip(keep_recent)

    def microcompact(self) -> None:
        self._engine.microcompact()

    def microcompact_time_based(self, interval: int | None = None) -> bool:
        return self._engine.microcompact_time_based(interval)

    def build_projection(self, messages: list[dict]) -> list[dict]:
        return self._engine.build_projection(messages)

    def mark_api_call(self) -> None:
        self._engine.mark_api_call()

    def bridge_read(self, filepath: str, content_preview: str = "") -> None:
        self._engine.bridge_read(filepath, content_preview)

    def conclude(self, question: str, answer: str, allow_memory: bool = True) -> None:
        self._pipeline.run(question, answer, allow_memory)
        project = getattr(self, "project", None)
        if allow_memory and project is not None:
            try:
                project.log_work("完成任务", question.strip()[:80])
            except Exception as e:
                logger.debug("Project memory worklog failed: %s", e)

    def _should_extract_rules(self, question: str, answer: str) -> str:
        return self._pipeline.should_extract_rules(question, answer)

    def _should_extract(self, question: str, answer: str) -> bool:
        return self._pipeline.should_extract(question, answer)
