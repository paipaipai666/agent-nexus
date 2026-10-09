"""UI-neutral turn lifecycle, journal, cancellation, and persistence."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from agentnexus.core.text_utils import collapse_and_truncate

logger = logging.getLogger(__name__)

TurnStatus = Literal["running", "finished", "failed", "interrupted", "empty_answer"]

COMMIT_MAX_ATTEMPTS = 3
COMMIT_RETRY_DELAY_S = 0.05


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
        self.last_commit_error: str = ""
        self._stm_start_count = 0
        self._stm_boundary: dict | None = None
        self._committed_keys: set[tuple] = set()
        self._question_user_skipped = False
        if self._memory is not None:
            try:
                existing = self._memory.short_term.get_all()
                self._stm_start_count = len(existing)
                self._stm_boundary = dict(existing[-1]) if existing else None
                for m in existing:
                    self._committed_keys.add(self._msg_key(m))
            except Exception:
                pass
        self.record("user", f"用户请求: {question}")

    @staticmethod
    def _msg_key(m: dict) -> tuple:
        return (m.get("role"), m.get("content"), m.get("ts"))

    def _collect_uncommitted(self) -> list[dict]:
        """STM rows not yet written to the version store."""
        if self._memory is None:
            return []
        try:
            all_msgs = self._memory.short_term.get_all()
        except Exception:
            return []
        new_msgs: list[dict] = []
        for m in all_msgs:
            key = self._msg_key(m)
            if key in self._committed_keys:
                continue
            if (
                not self._question_user_skipped
                and m.get("role") == "user"
                and m.get("content") == self.question
            ):
                self._question_user_skipped = True
                self._committed_keys.add(key)
                continue
            new_msgs.append(dict(m))
            self._committed_keys.add(key)
        return [m for m in new_msgs if str(m.get("content", "")).strip()]

    def commit_round(self) -> bool:
        """Persist STM rows accumulated this round (atomic SQLite commit).

        Retries a bounded number of times. Returns True on success. Returns
        False after all attempts fail — callers must NOT start the next
        ReAct round (raise MemoryCommitError / emit fatal FAULT).
        """
        if self._version is None or self._persisted:
            return False
        new_msgs = self._collect_uncommitted()
        if not new_msgs:
            return True
        last_err: Exception | None = None
        for attempt in range(1, COMMIT_MAX_ATTEMPTS + 1):
            try:
                self._version.commit_with_messages(
                    messages=new_msgs,
                    question="",
                    answer="",
                )
                return True
            except Exception as e:
                last_err = e
                logger.warning(
                    "Round commit attempt %s/%s failed: %s",
                    attempt, COMMIT_MAX_ATTEMPTS, e,
                )
                for m in new_msgs:
                    self._committed_keys.discard(self._msg_key(m))
                if attempt < COMMIT_MAX_ATTEMPTS:
                    time.sleep(COMMIT_RETRY_DELAY_S * attempt)
        self.last_commit_error = str(last_err) if last_err else "unknown"
        return False

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
        if self._version is not None and self._memory is not None:
            try:
                messages = self._collect_uncommitted()
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
                last_err: Exception | None = None
                for attempt in range(1, COMMIT_MAX_ATTEMPTS + 1):
                    try:
                        self._version.commit_with_messages(
                            messages=messages,
                            question=record.question,
                            answer=record.answer,
                        )
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e
                        logger.warning(
                            "persist_snapshot attempt %s/%s failed: %s",
                            attempt, COMMIT_MAX_ATTEMPTS, e,
                        )
                        if attempt < COMMIT_MAX_ATTEMPTS:
                            time.sleep(COMMIT_RETRY_DELAY_S * attempt)
                if last_err is not None:
                    self.last_commit_error = str(last_err)
                    logger.warning("Version commit failed after retries: %s", last_err)
            except Exception as e:
                logger.warning("Version commit failed: %s", e)

    def _build_interrupted_answer(self, *, status: TurnStatus, reason: str, detail: str = "") -> str:
        suffix = f"（本轮已记录 {len(self._journal)} 项活动）" if self._journal else ""
        if status == "interrupted":
            return f"已中断（{reason}）{suffix}" if reason else f"已中断{suffix}"
        if status == "failed":
            lines = [f"执行失败：{reason}"]
            if detail and detail != reason:
                lines.append(f"详情：{collapse_and_truncate(detail, 500)}")
            return "\n".join(lines)
        return f"Agent 未能得出最终答案{suffix}"
