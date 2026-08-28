"""Memory manager — session-scoped facade over the memory subsystems.

Responsibilities kept here:
  - wiring and configuration (STM/LTM/embed model lifecycle, ctx resolution)
  - append orchestration (offload large tool results, compaction trigger)
  - LTM context retrieval (init_session / has_new_memories / refresh)

Compaction lives in ``compaction_engine.CompactionEngine``; extraction lives
in ``extraction_pipeline.MemoryExtractionPipeline``. Both borrow the manager
as their shared context. State attributes historically set directly on the
manager (``_compact_circuit``, ``_on_compact``, …) are forwarded to the
owning sub-component via __getattr__/__setattr__, so existing callers and
tests that construct via ``MemoryManager.__new__`` keep working.
"""

import logging
import threading
from pathlib import Path

from agentnexus.core.config import get_settings
from agentnexus.core.llm import AgentLLM
from agentnexus.core.pii import contains_pii as _contains_pii  # noqa: F401  (re-export)
from agentnexus.core.pii import mask_pii as _mask_pii  # noqa: F401  (re-export)
from agentnexus.memory.compaction import parse_tool_message as _parse_tool_message  # noqa: F401  (re-export)
from agentnexus.memory.compaction_engine import CompactionEngine, _extract_xml_tag  # noqa: F401  (re-export)
from agentnexus.memory.extraction import CATEGORY_LABELS
from agentnexus.memory.extraction_pipeline import MemoryExtractionPipeline
from agentnexus.memory.long_term import get_long_term_memory
from agentnexus.memory.offload import offload_large_result
from agentnexus.memory.short_term import ShortTermMemory
from agentnexus.rag.embeddings import embedding_to_list, get_embedding_model

logger = logging.getLogger(__name__)


class MemoryManager:
    """Session-scoped memory manager combining STM, LTM, compaction, and extraction.

    Facade: owns shared resources (STM, LTM, LLM, embed model, settings) and
    delegates compaction to ``_engine`` and extraction to ``_pipeline``.
    """

    # Private state attributes that live on the sub-components. Reads go
    # through __getattr__ (only fired when normal lookup fails), writes
    # through __setattr__ — both lazily create the sub-component so
    # __new__-constructed test instances work without __init__.
    _FORWARD_TO_ENGINE = {
        "_compact_circuit": "circuit",
        "_microcompacts_since_open": "microcompacts_since_open",
        "_compacting": "compacting",
        "_snip_freed_tokens": "snip_freed_tokens",
        "_recent_reads": "recent_reads",
        "_last_api_call_ts": "last_api_call_ts",
        "_on_compact": "on_compact",
        "_on_after_compact": "on_after_compact",
        "_ctx_max": "ctx_max",
        "_compact_threshold": "compact_threshold",
        "_transcript_dir": "transcript_dir",
    }
    _FORWARD_TO_PIPELINE = {
        "_gate_circuit": "gate_circuit",
    }

    def __init__(self, session_id: str, llm=None, enable_long_term: bool = True):
        self.session_id = session_id
        self.short_term = ShortTermMemory()
        self.long_term = get_long_term_memory() if enable_long_term else None
        self._llm = llm or AgentLLM()
        self._embed_model = None
        self._embed_ready = threading.Event()
        threading.Thread(target=self._preload_embed_model, daemon=True).start()
        self._enable_long_term = enable_long_term
        settings = get_settings()
        if "/" in settings.chroma_persist_dir:
            base = settings.chroma_persist_dir.rsplit("/", 1)[0]
        else:
            base = str(Path(settings.chroma_persist_dir).parent)
        self._offload_dir = f"{base}/offload"
        self._settings = settings
        self._engine = CompactionEngine(self)
        self._engine.transcript_dir = f"{base}/transcripts"
        self._pipeline = MemoryExtractionPipeline(self)
        ctx_max = self._resolve_ctx_max()
        if ctx_max:
            self._engine.ctx_max = ctx_max
            self._engine.compact_threshold = ctx_max - self._settings.autocompact_buffer_tokens
        self._last_write_count: int = 0

    # ── Sub-component access + attribute forwarding ──────────────────

    def _get_engine(self) -> CompactionEngine:
        engine = self.__dict__.get("_engine")
        if engine is None:
            engine = CompactionEngine(self)
            self.__dict__["_engine"] = engine
        return engine

    def _get_pipeline(self) -> MemoryExtractionPipeline:
        pipeline = self.__dict__.get("_pipeline")
        if pipeline is None:
            pipeline = MemoryExtractionPipeline(self)
            self.__dict__["_pipeline"] = pipeline
        return pipeline

    def __setattr__(self, name, value):
        engine_attr = self._FORWARD_TO_ENGINE.get(name)
        if engine_attr is not None:
            setattr(self._get_engine(), engine_attr, value)
            return
        pipeline_attr = self._FORWARD_TO_PIPELINE.get(name)
        if pipeline_attr is not None:
            setattr(self._get_pipeline(), pipeline_attr, value)
            return
        object.__setattr__(self, name, value)

    def __getattr__(self, name):
        # Only fired when normal lookup fails (i.e. forwarded state attrs).
        engine_attr = self._FORWARD_TO_ENGINE.get(name)
        if engine_attr is not None:
            return getattr(self._get_engine(), engine_attr)
        pipeline_attr = self._FORWARD_TO_PIPELINE.get(name)
        if pipeline_attr is not None:
            return getattr(self._get_pipeline(), pipeline_attr)
        raise AttributeError(f"{type(self).__name__} has no attribute {name!r}")

    # ── Embedding model lifecycle ────────────────────────────────────

    def _preload_embed_model(self):
        """Background thread: load embedding model without blocking startup."""
        try:
            self._embed_model = get_embedding_model()
        except Exception as exc:
            logger.warning("Embedding model preload failed: %s", exc)
        finally:
            self._embed_ready.set()

    def _get_embed_model(self, timeout: float = 30):
        """Return embedding model, waiting for background preload if needed."""
        if self._embed_model is not None:
            return self._embed_model
        if not self._embed_ready.wait(timeout=timeout):
            raise TimeoutError("Embedding model failed to load within timeout")
        if self._embed_model is None:
            raise RuntimeError("Embedding model failed to load")
        return self._embed_model

    @staticmethod
    def _resolve_ctx_max() -> int | None:
        """Query LiteLLM for the current model's max input tokens."""
        try:
            from litellm import get_model_info
            model_id = get_settings().llm_model_id
            info = get_model_info(model_id)
            return info.get("max_input_tokens") or None
        except Exception as e:
            logger.debug("Failed to resolve ctx_max from litellm: %s", e)
            return None

    def estimate_stm_tokens(self) -> int:
        """Return current STM token estimate.

        Prefers the incremental counter (O(1)) when available and populated,
        falls back to full re-encoding.
        """
        # Use incremental counter if it has been populated (non-zero or messages are empty)
        stm = self.short_term
        if stm._token_count > 0 or len(stm._messages) == 0:
            return stm._token_count
        return stm.estimate_tokens()

    # ── LTM context retrieval ────────────────────────────────────────

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
        query_vec = embedding_to_list(self._get_embed_model().encode(query_text, normalize_embeddings=True))
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

    # ── Append pipeline (offload + compaction trigger) ───────────────

    def append(self, role: str, content: str, metadata: dict | None = None) -> None:
        from agentnexus.core.hooks import HookType, get_hook_manager

        hook_mgr = get_hook_manager()

        # ── before memory hook ───────────────────────────────────
        hook_mgr.fire(HookType.BEFORE_MEMORY_OP, {
            "op": "append", "role": role, "content": content,
        })

        # Layer 1: offload large tool results to disk
        if role == "tool" and self._settings.offload_enabled:
            threshold = self._settings.large_result_threshold
            if len(content.encode("utf-8", errors="replace")) > threshold:
                content = self._offload_large_result(content)
        self.short_term.append(role, content, metadata=metadata)
        # Recursive guard: don't trigger compaction from within compaction
        if not self._compacting:
            self.maybe_compact()

        # ── after memory hook ────────────────────────────────────
        hook_mgr.fire(HookType.AFTER_MEMORY_OP, {
            "op": "append", "role": role, "content": content,
        })

    def _offload_large_result(self, content: str) -> str:
        """Write large tool result to disk, return a stub with preview."""
        return offload_large_result(content, self._offload_dir, self.session_id)

    # ── Compaction delegates (see CompactionEngine) ──────────────────

    def maybe_compact(self, threshold: int | None = None, custom_instructions: str = "",
                      is_auto: bool = True) -> int:
        return self._get_engine().maybe_compact(threshold, custom_instructions, is_auto)

    def snip(self, keep_recent: int = 10) -> int:
        return self._get_engine().snip(keep_recent)

    def microcompact(self) -> None:
        self._get_engine().microcompact()

    def microcompact_time_based(self, interval: int | None = None) -> bool:
        return self._get_engine().microcompact_time_based(interval)

    def build_projection(self, messages: list[dict]) -> list[dict]:
        return self._get_engine().build_projection(messages)

    def mark_api_call(self) -> None:
        self._get_engine().mark_api_call()

    def bridge_read(self, filepath: str, content_preview: str = "") -> None:
        self._get_engine().bridge_read(filepath, content_preview)

    def _fire_compact(self, event_type: str, **kwargs):
        self._get_engine()._fire_compact(event_type, **kwargs)

    def _write_transcript(self):
        self._get_engine()._write_transcript()

    def _restore_files(self):
        self._get_engine()._restore_files()

    def _drain_to_ltm(self, messages: list[dict]):
        self._get_engine()._drain_to_ltm(messages)

    # ── Extraction delegates (see MemoryExtractionPipeline) ──────────

    def conclude(self, question: str, answer: str, allow_memory: bool = True) -> None:
        self._get_pipeline().run(question, answer, allow_memory)

    def _should_extract_rules(self, question: str, answer: str) -> str:
        return self._get_pipeline().should_extract_rules(question, answer)

    def _should_extract(self, question: str, answer: str) -> bool:
        return self._get_pipeline().should_extract(question, answer)
