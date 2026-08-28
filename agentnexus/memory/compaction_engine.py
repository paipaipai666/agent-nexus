"""Compaction engine — the 5-layer compaction pyramid, extracted from MemoryManager.

Owns all compaction state (circuit breaker, snip budget, recent file reads,
compact callbacks) and implements the pipeline:

  Layer 2: snip (drop oldest messages)
  Layer 3: time-decay microcompact
  Layer 3b: microcompact before LLM summarization
  Layer 4: read-time projection (non-destructive)
  Layer 5: full LLM summarization (transcript backup + LTM drain first)

The engine borrows session-scoped dependencies (STM, LTM, LLM, settings)
from the owning MemoryManager via ``self._mgr``; compaction state lives here.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from agentnexus.memory.circuit_breaker import CircuitBreaker
from agentnexus.memory.compaction import is_recoverable_tool
from agentnexus.memory.compaction import parse_tool_message as _parse_tool_message
from agentnexus.memory.projection import build_projection as build_projected_messages
from agentnexus.memory.projection import microcompact_messages
from agentnexus.memory.short_term import compute_importance
from agentnexus.prompts import load_prompt

if TYPE_CHECKING:
    from agentnexus.memory.manager import MemoryManager

logger = logging.getLogger(__name__)

SUMMARIZE_PROMPT = load_prompt("memory_summarize")


def _extract_xml_tag(text: str, tag: str) -> str | None:
    """Extract content between <tag> and </tag> from text. Returns None if not found."""
    pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(1) if match else None


class CompactionEngine:
    """Session-scoped compaction state machine (see module docstring for layers)."""

    def __init__(self, mgr: MemoryManager):
        self._mgr = mgr
        # Compaction circuit breaker (exponential backoff)
        self.circuit = CircuitBreaker(failure_threshold=3, exponential_backoff=True)
        self.microcompacts_since_open: int = 0
        self.compacting: bool = False
        self.snip_freed_tokens: int = 0
        self.recent_reads: list[tuple[str, str, float]] = []  # (filepath, preview, ts)
        self.last_api_call_ts: float = 0.0
        self.on_compact: Callable[[dict], None] | None = None
        self.on_after_compact: Callable[[], None] | None = None
        self.ctx_max: int = 128000
        self.compact_threshold: int = 120000
        self.transcript_dir: str = ""

    def _fire_compact(self, event_type: str, **kwargs):
        """Fire compact event callback if set."""
        if self.on_compact:
            try:
                self.on_compact({"event": event_type, **kwargs})
            except Exception as e:
                logger.debug("Compact event callback failed: %s", e)

    def _restore_files(self):
        settings = self._mgr._settings
        max_files = settings.post_compact_max_files
        per_file = settings.post_compact_token_per_file
        budget = settings.post_compact_token_budget
        if max_files <= 0 or not self.recent_reads:
            return
        seen: set[str] = set()
        recent: list[tuple[str, str]] = []
        for fp, preview, _ts in reversed(self.recent_reads):
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
            except Exception as e:
                logger.debug("Failed to read file %s for restore: %s", fp, e)
                content = preview or f"[无法读取文件] {fp}"
            self._mgr.short_term.append("system", f"[恢复文件] {fp}\n{content}")
            total_tokens += per_file
            restored += 1
        if restored:
            self._fire_compact("file_restore", restored=restored, files=[fp for fp, _ in recent[:restored]])

    def _write_transcript(self):
        if not self._mgr._settings.transcript_enabled:
            return
        Path(self.transcript_dir).mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        fname = f"{self._mgr.session_id}_compact_{ts}.jsonl"
        fpath = Path(self.transcript_dir) / fname
        messages = self._mgr.short_term.get_all()
        lines = [json.dumps(m, ensure_ascii=False) for m in messages]
        fpath.write_text("\n".join(lines), encoding="utf-8")
        self._fire_compact("transcript_saved", path=str(fpath), message_count=len(messages))

    def _drain_to_ltm(self, messages: list[dict]):
        """Sink high-importance messages to LTM before destructive compaction.

        This ensures critical information (user constraints, key decisions)
        survives even if the LLM summary loses them.
        """
        if not self._mgr.long_term:
            return
        embed_model = None
        try:
            embed_model = self._mgr._get_embed_model(timeout=5)
        except Exception:
            pass

        drained = 0
        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            # Skip tool results — not useful for LTM
            if role == "tool":
                continue
            # Only drain high-importance messages
            imp = compute_importance(msg, i, len(messages))
            if imp < 0.7:
                continue
            content = msg.get("content", "")
            if len(content) < 20:
                continue
            # Truncate very long content
            save_content = content[:2000]
            try:
                embedding = None
                if embed_model:
                    from agentnexus.rag.embeddings import embedding_to_list
                    embedding = embedding_to_list(
                        embed_model.encode(save_content, normalize_embeddings=True)
                    )
                self._mgr.long_term.save(
                    session_id=self._mgr.session_id,
                    content=save_content,
                    category="note",
                    importance=imp,
                    embedding=embedding,
                )
                drained += 1
            except Exception as e:
                logger.debug("LTM drain failed for message %d: %s", i, e)

        if drained:
            self._fire_compact("ltm_drain", drained=drained)

    def mark_api_call(self) -> None:
        """Record that an API call just happened for time-based microcompact tracking."""
        self.last_api_call_ts = time.time()

    def bridge_read(self, filepath: str, content_preview: str = "") -> None:
        self.recent_reads.append((filepath, content_preview[:5000], time.time()))
        if len(self.recent_reads) > 20:
            self.recent_reads = self.recent_reads[-20:]

    def snip(self, keep_recent: int = 10) -> int:
        if not self._mgr._settings.snip_enabled:
            return 0
        stm = self._mgr.short_term
        all_msgs = stm.get_all()
        if len(all_msgs) <= keep_recent + 4:
            return 0
        tokens_before = stm.token_count or stm.estimate_tokens()
        removed = stm.snip(keep_recent)
        if removed:
            tokens_after = stm.token_count or stm.estimate_tokens()
            freed = max(0, tokens_before - tokens_after)
            self.snip_freed_tokens += freed
            self._fire_compact("snip", removed=removed, freed_tokens=freed)
        return removed

    def microcompact_time_based(self, interval: int | None = None) -> bool:
        """Layer 3: time-decay based microcompact. Clears recoverable tool results
        when the last API call was more than `interval` seconds ago.

        Returns True if microcompact was performed.
        """
        if interval is None:
            interval = self._mgr._settings.time_microcompact_interval
        if self.last_api_call_ts <= 0:
            return False
        elapsed = time.time() - self.last_api_call_ts
        if elapsed < interval:
            return False
        stm = self._mgr.short_term
        tokens_before = stm.token_count or stm.estimate_tokens()
        self.microcompact()
        tokens_after = stm.token_count or stm.estimate_tokens()
        self._fire_compact("time_microcompact", tokens_before=tokens_before, elapsed=elapsed)
        return tokens_before != tokens_after

    def build_projection(self, messages: list[dict]) -> list[dict]:
        """Layer 4: non-destructive read-time projection. Returns a compressed view
        of messages without modifying STM. Called before every LLM API call.

        90% ctx used → mild compression. 95% → aggressive compression.
        """
        tokens = self._mgr.short_term.estimate_tokens()
        return build_projected_messages(
            messages,
            token_count=tokens,
            ctx_max=self.ctx_max,
            parse_tool_message=_parse_tool_message,
            is_recoverable_tool=is_recoverable_tool,
            importance_fn=compute_importance,
        )

    def microcompact(self) -> None:
        stm = self._mgr.short_term
        compacted, cleaned = microcompact_messages(
            stm.get_all(),
            parse_tool_message=_parse_tool_message,
            is_recoverable_tool=is_recoverable_tool,
            importance_fn=compute_importance,
        )
        if cleaned:
            stm.replace_messages(compacted)

    def maybe_compact(self, threshold: int | None = None, custom_instructions: str = "",
                      is_auto: bool = True) -> int:
        """5-layer compaction pyramid. Returns tokens saved, or 0.

        is_auto=False enables manual /compact mode (accepts custom_instructions,
        does not suppress follow-up questions).
        """
        from agentnexus.core.hooks import HookType, get_hook_manager

        mgr = self._mgr
        hook_mgr = get_hook_manager()
        hook_mgr.fire(HookType.BEFORE_COMPACT, {
            "is_auto": is_auto, "threshold": threshold,
        })

        circuit = self.circuit
        half_open_probe = False

        if circuit.is_open:
            remaining = circuit.backoff_remaining()
            if remaining > 0:
                logger.debug("Circuit breaker in cooldown (%.0fs remaining)", remaining)
                self.microcompact()
                return 0
            # Cooldown expired — enter half-open state for probe
            half_open_probe = True
            self._fire_compact("circuit_half_open", elapsed=remaining)

        if threshold is None:
            threshold = self.compact_threshold
            if self.snip_freed_tokens > 0:
                threshold = max(threshold - self.snip_freed_tokens, threshold // 2)

        stm = mgr.short_term
        # Prefer incremental counter (O(1)) over full re-encoding
        tokens_before = stm.token_count or stm.estimate_tokens()

        # Half-open probe bypasses threshold — we need to test LLM health
        if not half_open_probe:
            if tokens_before < threshold:
                if mgr._settings.time_microcompact_interval > 0:
                    self.microcompact_time_based()
                return 0

            all_msgs = stm.get_all()
            if len(all_msgs) <= 4:
                return 0
        else:
            all_msgs = stm.get_all()

        # Layer 2: Snip
        self.snip()

        # Layer 3: Time-based microcompact
        if mgr._settings.time_microcompact_interval > 0:
            self.microcompact_time_based()

        # Layer 3b: MicroCompact before LLM summarization
        self.microcompact()

        # Full rewrite: send ALL messages to summarizer
        all_msgs_after = stm.get_all()
        history_text = "\n".join(f"{m['role']}: {m['content']}" for m in all_msgs_after)
        if not history_text.strip():
            return 0

        from agentnexus.observability.tracer import get_trace_manager

        trace_mgr = get_trace_manager()

        # Layer 5: Kairos transcript backup before destructive compact
        self._write_transcript()

        # Layer 5b: Drain high-importance messages to LTM before summarization
        self._drain_to_ltm(all_msgs_after)

        augmented = history_text
        if custom_instructions:
            augmented = f"[压缩指令] {custom_instructions}\n\n{augmented}"

        self._fire_compact("start", tokens_before=tokens_before)

        self.compacting = True
        try:
            with trace_mgr.span("memory_compact", {"is_auto": is_auto}):
                prompt = SUMMARIZE_PROMPT.format(history=augmented)
                response = mgr._llm.think([{"role": "user", "content": prompt}], silent=True) or ""
                if not response:
                    circuit.record_failure()
                    if half_open_probe:
                        logger.warning("Half-open probe got empty response, re-opening circuit")
                    self._fire_compact("circuit_open")
                    return 0

                summary_content = _extract_xml_tag(response, "summary")
                final_summary = (summary_content or response).strip()
                stm.compact_full(final_summary, message_count=len(all_msgs_after),
                                 is_auto=is_auto, keep_recent=6)
                # Close circuit on success
                if half_open_probe:
                    logger.info("Circuit breaker closed after successful half-open probe")
                    self._fire_compact("circuit_reset")
                circuit.record_success()
                self.microcompacts_since_open = 0
                self.snip_freed_tokens = 0
                self.recent_reads.clear()

                # A3: File recovery after compact
                self._restore_files()

                # A6: System prompt rebuild hook
                if self.on_after_compact:
                    try:
                        self.on_after_compact()
                    except Exception as e:
                        logger.debug("After-compact callback failed: %s", e)

                tokens_after = stm.token_count or stm.estimate_tokens()
                tokens_saved = max(0, tokens_before - tokens_after)
                self._fire_compact("complete", tokens_before=tokens_before, tokens_after=tokens_after)

                hook_mgr.fire(HookType.AFTER_COMPACT, {
                    "is_auto": is_auto, "tokens_saved": tokens_saved,
                    "tokens_before": tokens_before, "tokens_after": tokens_after,
                })
                return tokens_saved
        except Exception as e:
            logger.warning("Compaction failed: %s", e)
            circuit.record_failure()
            if half_open_probe:
                logger.warning("Half-open probe failed, re-opening circuit breaker")
            self._fire_compact("circuit_open")
            return 0
        finally:
            self.compacting = False
