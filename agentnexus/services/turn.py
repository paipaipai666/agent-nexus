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
        # Snapshot STM length at turn start so we can journal new messages later
        self._stm_start_count = 0
        if self._memory is not None:
            try:
                self._stm_start_count = len(self._memory.short_term.get_all())
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
                # Write new messages to the journal
                all_msgs = self._memory.short_term.get_all()
                new_msgs = all_msgs[self._stm_start_count:]
                if new_msgs:
                    self._version.append_messages(new_msgs)
                total_count = self._version.get_message_count()
                # Commit checkpoint with message_count (stm_snapshot left empty for new format)
                self._version.commit(
                    stm_snapshot="",
                    question=record.question,
                    answer=record.answer,
                    new_ltm_ids=[],
                    message_count=total_count,
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
