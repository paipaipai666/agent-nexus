"""UI-neutral turn lifecycle, journal, cancellation, and persistence."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Literal

from agentnexus.core.text_utils import collapse_and_truncate

logger = logging.getLogger(__name__)

TurnStatus = Literal["running", "finished", "failed", "interrupted", "empty_answer"]


@dataclass(frozen=True)
class TurnRecord:
    run_id: str
    session_id: str
    question: str
    status: TurnStatus
    answer: str = ""
    reason: str = ""
    detail: str = ""
    journal: tuple[str, ...] = field(default_factory=tuple)


class TurnRuntime:
    """Owns one run's semantic state independently of any UI."""

    def __init__(
        self,
        *,
        run_id: str,
        session_id: str,
        question: str,
        memory_manager: Any = None,
        version_manager: Any = None,
    ):
        self.run_id = run_id
        self.session_id = session_id
        self.question = question
        self._memory = memory_manager
        self._version = version_manager
        self._cancelled = threading.Event()
        self._journal: list[str] = []
        self._record = TurnRecord(run_id=run_id, session_id=session_id, question=question, status="running")
        self._persisted = False
        # Snapshot STM boundary at turn start so we can journal new messages
        # later. Identity-based (last message before this turn), NOT a count:
        # journal row count and STM index only coincide when STM mirrors the
        # journal, which fails after a restart (fresh STM) or with injected
        # context rows — the count-based slice then silently drops the turn's
        # assistant entries from the durable journal.
        self._stm_start_count = 0
        self._stm_boundary: dict | None = None
        if self._memory is not None:
            try:
                existing = self._memory.short_term.get_all()
                self._stm_start_count = len(existing)
                self._stm_boundary = dict(existing[-1]) if existing else None
            except Exception:
                pass
        self.record("user", f"用户请求: {question}")

    @property
    def record_snapshot(self) -> TurnRecord:
        return self._record

    def record(self, kind: str, summary: str, payload: dict | None = None) -> None:
        clean = " ".join(str(summary or "").split())
        if not clean:
            return
        prefix = str(kind or "event").strip() or "event"
        self._journal.append(f"{prefix}: {clean}"[:300])

    def finish(self, answer: str) -> TurnRecord:
        if self._persisted:
            return self._record
        status: TurnStatus = "finished" if answer else "empty_answer"
        reason = "" if answer else "Agent 未能得出最终答案"
        final_answer = answer or self._build_interrupted_answer(status=status, reason=reason)
        self._record = TurnRecord(
            run_id=self.run_id,
            session_id=self.session_id,
            question=self.question,
            status=status,
            answer=final_answer,
            reason=reason,
            journal=tuple(self._journal),
        )
        self.persist_snapshot()
        return self._record

    def fail(self, reason: str, detail: str = "") -> TurnRecord:
        if self._persisted:
            return self._record
        answer = self._build_interrupted_answer(status="failed", reason=reason, detail=detail)
        self._record = TurnRecord(
            run_id=self.run_id,
            session_id=self.session_id,
            question=self.question,
            status="failed",
            answer=answer,
            reason=reason,
            detail=detail,
            journal=tuple(self._journal),
        )
        self.persist_snapshot()
        return self._record

    def cancel(self, reason: str = "user_cancelled") -> TurnRecord:
        self._cancelled.set()
        if self._persisted:
            return self._record
        answer = self._build_interrupted_answer(status="interrupted", reason=reason)
        self._record = TurnRecord(
            run_id=self.run_id,
            session_id=self.session_id,
            question=self.question,
            status="interrupted",
            answer=answer,
            reason=reason,
            journal=tuple(self._journal),
        )
        self.persist_snapshot()
        return self._record

    def cancel_checker(self) -> bool:
        return self._cancelled.is_set()

    def persist_snapshot(self) -> None:
        if self._persisted:
            return
        self._persisted = True
        record = self._record
        # NOTE: Do NOT append the answer to memory here.
        # The agent already stores it as "system" with "[最终答案]" prefix
        # in re_act_agent.py:_on_emit_answer. Storing it again as "assistant"
        # causes a duplicate that displays as thinking in history.
        if self._version is not None and self._memory is not None:
            try:
                # Re-read STM at persist time — the start snapshot may be stale
                # if STM was cleared/compacted during the turn.
                all_msgs = self._memory.short_term.get_all()
                # Slice AFTER the turn-start boundary message (identity match,
                # scanning from the end). Count-based slicing by journal row
                # count is wrong whenever STM doesn't mirror the journal
                # (fresh STM after restart, injected context rows): it chops
                # leading real messages and the turn's entries never reach the
                # durable history.
                boundary_idx = -1
                if self._stm_boundary is not None:
                    br = self._stm_boundary.get("role")
                    bc = self._stm_boundary.get("content")
                    # The boundary existed at turn start, so its index is below
                    # the start count (compaction can only shift it lower). The
                    # bound also disambiguates duplicate (role, content) pairs —
                    # e.g. the user resending the identical question.
                    scan_hi = min(self._stm_start_count, len(all_msgs)) - 1
                    for i in range(scan_hi, -1, -1):
                        if all_msgs[i].get("role") == br and all_msgs[i].get("content") == bc:
                            boundary_idx = i
                            break
                if boundary_idx >= 0:
                    new_msgs = all_msgs[boundary_idx + 1:]
                elif self._stm_boundary is None:
                    new_msgs = all_msgs  # STM was empty at turn start
                else:
                    # Boundary row was archived mid-turn (compaction); fall
                    # back to the count snapshot, clamped to current length.
                    start = min(self._stm_start_count, len(all_msgs))
                    new_msgs = all_msgs[start:]
                messages = [dict(m) for m in new_msgs]
                # Drop blank rows — empty assistant appends (e.g. a JSON-retry
                # with an empty response) render as empty thinking cards in
                # history. Writers are guarded too; this is the durable net.
                messages = [m for m in messages if str(m.get("content", "")).strip()]
                # The turn-start commit already persisted the user question and
                # the agent appends it to STM as well — drop our own copy so
                # the journal doesn't gain a duplicate user row.
                if (messages and messages[0].get("role") == "user"
                        and messages[0].get("content") == record.question):
                    messages = messages[1:]
                # 决策 5: cancelled/failed turns leave an explicit marker in
                # the message journal so a reopened session shows what
                # happened — the answer itself is checkpoint metadata, not a
                # history message.
                if record.status == "interrupted":
                    messages.append({
                        "role": "assistant",
                        "content": f"[已取消] {record.reason}".rstrip(),
                    })
                elif record.status == "failed":
                    messages.append({
                        "role": "assistant",
                        "content": f"[执行失败] {record.reason}".rstrip(),
                    })
                # Atomically write messages + checkpoint in one transaction
                self._version.commit_with_messages(
                    messages=messages,
                    question=record.question,
                    answer=record.answer,
                )
            except Exception as e:
                logger.warning("Version commit failed: %s", e)

    def _build_interrupted_answer(self, *, status: TurnStatus, reason: str, detail: str = "") -> str:
        lines = [
            "[会话中断记录]",
            f"状态: {status}",
            f"原因: {reason}",
            f"原始请求: {self.question}",
        ]
        if detail and detail != reason:
            lines.append(f"详情: {collapse_and_truncate(detail, 500)}")
        if self._journal:
            lines.append("中断前已记录的活动:")
            for item in self._journal[-20:]:
                lines.append(f"- {item}")
        else:
            lines.append("中断前没有记录到已完成的 Agent 活动。")
        return "\n".join(lines)
