"""Managed child execution and durable communication within one parent run."""

from __future__ import annotations

import contextvars
import json
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from agentnexus.core.exceptions import AgentCancelled
from agentnexus.services.subagents import SubagentEntry


@dataclass
class ChildTask:
    entry: SubagentEntry
    child_run_id: str
    ended: bool = False


class AgentMailbox:
    """Only the owning agent consumes messages and mutates its model context."""

    def __init__(self, run: CollaborationRun, child: ChildTask | None = None):
        self.run = run
        self.child = child
        self.step_id = 0

    @property
    def endpoint(self) -> str:
        return self.child.entry.subagent_id if self.child else "parent"

    def drain(self, step_id: int) -> list[dict]:
        self.step_id = step_id
        with self.run.condition:
            messages = self.run._pending(self.endpoint)
            if messages:
                self.run.store.apply_collaboration_messages([m["id"] for m in messages], step_id)
                for subagent_id in {m["subagent_id"] for m in messages}:
                    self.run._publish(subagent_id)
            return messages

    def inject(self, messages: list[dict], step_id: int) -> int:
        self.step_id = step_id
        with self.run.condition:
            pending = self.run._pending(self.endpoint)
            if pending:
                progress = {m["subagent_id"]: m for m in pending if m["kind"] == "progress"}
                important = [m for m in pending if m["kind"] != "progress"]
                projected = sorted(important, key=lambda m: m["kind"] != "question") + list(progress.values())
                messages.append({"role": "user", "content": "[代理协作消息]\n" + json.dumps(projected, ensure_ascii=False)})
                try:
                    self.run.store.apply_collaboration_messages([m["id"] for m in pending], step_id)
                except Exception:
                    messages.pop()
                    raise
                for subagent_id in {m["subagent_id"] for m in pending}:
                    self.run._publish(subagent_id)
            return len(pending)

    def report(self, content: str, request_id: str) -> dict:
        if self.child is None:
            raise ValueError("只有子代理可以汇报进展")
        return self.run._send(self.child, self.endpoint, "parent", "progress", content, request_id)

    def ask(self, content: str, request_id: str, timeout: float = 120) -> dict:
        if self.child is None:
            raise ValueError("只有子代理可以向主代理提问")
        question = self.run._send(self.child, self.endpoint, "parent", "question", content, request_id)
        deadline = time.monotonic() + min(max(timeout, 0), self.run.timeout)
        self.run.context.event(self.child.entry, "waiting", status="waiting")
        try:
            with self.run.condition:
                while True:
                    self.run._check_cancel(self.child)
                    incoming = self.run._pending(self.endpoint)
                    if incoming:
                        received = self.drain(self.step_id)
                        state = next(m["question_state"] for m in self.run._messages() if m["id"] == question["id"])
                        return {"question_id": question["id"], "question_state": state, "messages": received}
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.run.store.expire_collaboration_question(question["id"])
                        self.run._publish(self.endpoint)
                        return {"question_id": question["id"], "question_state": "expired", "messages": []}
                    self.run.condition.wait(min(remaining, .2))
        finally:
            if not self.child.entry.cancel_event.is_set():
                self.run.context.event(self.child.entry, "resumed", status="thinking")

    def before_finish(self, messages: list[dict], step_id: int) -> bool:
        if self.child:
            return bool(self.inject(messages, step_id))
        while self.run.active and not any(m["kind"] != "progress" for m in self.run._pending("parent")):
            self.run.wait(30)
        consumed = self.inject(messages, step_id)
        return bool(consumed or self.run.active)


class CollaborationRun:
    """Own child identities, delivery validation, lifecycle and condition wakeups."""

    def __init__(self, context, store, pool, cancel_checker: Callable[[], bool], *, max_tasks: int = 12, timeout: float = 1800):
        self.context = context
        self.store = store
        self.pool = pool
        self.cancel_checker = cancel_checker
        self.max_tasks = max_tasks
        self.timeout = timeout
        self.children: dict[str, ChildTask] = {}
        self._published: dict[int, int] = {}
        self.condition = threading.Condition(threading.RLock())
        self.closed = False
        self.parent = AgentMailbox(self)

    @property
    def active(self) -> bool:
        with self.condition:
            return any(not child.ended for child in self.children.values())

    def start(self, task: str, role: str, name: str | None, execute: Callable[[AgentMailbox], dict]) -> dict:
        with self.condition:
            self._check_cancel()
            if len(self.children) >= self.max_tasks:
                raise ValueError("本次运行的子任务数量已达上限")
            entry = self.context.started(name, role, task)
            child = ChildTask(entry, f"subrun_{uuid.uuid4().hex}")
            entry.collaborative = True
            entry.child_run_id = child.child_run_id
            self.children[entry.subagent_id] = child
            inherited = contextvars.copy_context()
            try:
                self.pool.submit(inherited.run, self._execute, child, execute)
            except Exception as exc:
                child.ended = True
                self.context.finished(entry, "failed", error=str(exc))
                self.condition.notify_all()
                raise
            return {"subagent_id": entry.subagent_id, "child_run_id": child.child_run_id, "name": entry.name, "status": "started"}

    def _execute(self, child: ChildTask, execute: Callable[[AgentMailbox], dict]) -> None:
        status, summary, error, steps = "failed", "", "", 0
        try:
            self._check_cancel(child)
            result = execute(AgentMailbox(self, child))
            summary = result.get("answer", "") or result.get("salvaged", "")
            steps = result.get("steps_used", 0)
            status = "completed" if summary else "failed"
            self._check_cancel(child)
        except AgentCancelled as exc:
            status, error = "interrupted", str(exc) or "cancelled"
            summary = ""
        except Exception as exc:
            if child.entry.cancel_event.is_set():
                status = "interrupted"
            error = str(exc)
        finally:
            with self.condition:
                if not self.closed and not self.cancel_checker():
                    result_text = summary if status == "completed" else f"子任务{'已中断' if status == 'interrupted' else '失败'}：{error or '未产出结果'}"
                    try:
                        self._send(child, child.entry.subagent_id, "parent", "result", result_text[:16000], f"result:{child.child_run_id}")
                    except Exception as delivery_error:
                        status = "interrupted" if child.entry.cancel_event.is_set() else "failed"
                        error = f"{error}; 结果无法投递：{delivery_error}"
                child.ended = True
                try:
                    self.store.close_collaboration(self.context._session_id, self.context._run_id, error or "child_finished", child.child_run_id)
                except Exception as exc:
                    status, error = "failed", f"无法保存任务关闭状态：{exc}"
                self.context.finished(child.entry, status, summary=summary, error=error, steps_used=steps)
                self._publish(child.entry.subagent_id)
                self.condition.notify_all()

    def _check_cancel(self, child: ChildTask | None = None) -> None:
        if self.closed or self.cancel_checker() or (child and child.entry.cancel_event.is_set()):
            raise AgentCancelled("协作运行已中断")

    def _messages(self) -> list[dict]:
        return self.store.list_collaboration_messages(self.context._session_id, limit=256 + self.max_tasks, run_id=self.context._run_id)

    def _pending(self, endpoint: str) -> list[dict]:
        return [m for m in self._messages() if m["recipient"] == endpoint and m["delivery"] == "queued"]

    def _publish(self, subagent_id: str) -> None:
        child = self.children[subagent_id]
        history = [m for m in self._messages() if m["subagent_id"] == subagent_id]
        records = [m for m in history if m["revision"] > self._published.get(m["id"], 0)]
        if records:
            self._published.update({m["id"]: m["revision"] for m in records})
            self.context.event(child.entry, "communication", messages=records, child_run_id=child.child_run_id,
                               waiting_questions=sum(m["question_state"] == "open" for m in history))

    def _send(self, child: ChildTask, sender: str, recipient: str, kind: str, content: str, request_id: str, question_id: int | None = None) -> dict:
        if not content.strip() or len(content) > 16000 or not request_id or len(request_id) > 128:
            raise ValueError("消息正文或请求 ID 无效")
        with self.condition:
            envelope = {"session_id": self.context._session_id, "run_id": self.context._run_id,
                        "request_id": request_id, "subagent_id": child.entry.subagent_id,
                        "child_run_id": child.child_run_id, "sender": sender, "recipient": recipient,
                        "kind": kind, "content": content, "question_id": question_id or ""}
            records = self._messages()
            existing = next((m for m in records if m["request_id"] == request_id and m["sender"] == sender), None)
            if existing:
                return self.store.create_collaboration_message(envelope)
            if self.closed or self.cancel_checker() or (child.entry.cancel_event.is_set() and kind != "result") or child.ended:
                raise ValueError("接收者已经结束")
            if kind != "result" and len(records) >= 256:
                raise ValueError("本次运行的协作消息已达上限")
            if kind == "reply":
                question = next((m for m in records if m["id"] == question_id), None)
                if not question or question["child_run_id"] != child.child_run_id or question["question_state"] != "open":
                    raise ValueError("问题已失效或不属于本次子任务")
            receipt = self.store.create_collaboration_message(envelope)
            self._publish(child.entry.subagent_id)
            self.condition.notify_all()
            return receipt

    def send(self, subagent_id: str, content: str, request_id: str, question_id: int | None = None, *, child_run_id: str | None = None, sender: str = "parent") -> dict:
        child = self.children.get(subagent_id)
        if child is None or (child_run_id is not None and child.child_run_id != child_run_id):
            raise ValueError("子任务不属于当前运行或执行尝试已失效")
        return self._send(child, sender, subagent_id, "reply" if question_id else "instruction", content, request_id, question_id)

    def wait(self, timeout: float = 30) -> dict:
        deadline = time.monotonic() + min(max(timeout, 0), 60)
        with self.condition:
            ended = {key for key, child in self.children.items() if child.ended}
            while self.active and not any(m["kind"] in ("question", "result") for m in self._pending("parent")):
                if any(child.ended and key not in ended for key, child in self.children.items()):
                    break
                self._check_cancel()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(min(remaining, .2))
            return {"active": self.active, "pending_messages": len(self._pending("parent")),
                    "children": [{"subagent_id": c.entry.subagent_id, "status": c.entry.status} for c in self.children.values()]}

    def close(self, reason: str) -> None:
        with self.condition:
            self.closed = True
            for child in self.children.values():
                if not child.ended:
                    child.entry.cancel_event.set()
                    self.context.event(child.entry, "cancelling", status="cancelling")
            self.store.close_collaboration(self.context._session_id, self.context._run_id, reason)
            self.condition.notify_all()
