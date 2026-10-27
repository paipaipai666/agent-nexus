import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from agentnexus.observability.timeline import TimelineStore
from agentnexus.services.collaboration import ChildTask, CollaborationRun
from agentnexus.services.subagents import SubagentRegistry, SubagentRunContext


def make_run(tmp_path):
    registry = SubagentRegistry()
    service = SimpleNamespace(_subagents=registry, _subagent_emit=lambda entry, kind, **kw: registry.update(entry, **{k: v for k, v in kw.items() if k == "status"}))
    ctx = SubagentRunContext(service, "s", "p")
    pool = ThreadPoolExecutor(2)
    run = CollaborationRun(ctx, TimelineStore(str(tmp_path / "timeline.db")), pool, lambda: False, max_tasks=8)
    return run, pool


def test_parent_answers_question_while_other_child_keeps_running(tmp_path):
    run, pool = make_run(tmp_path)
    release = threading.Event()
    answer = []
    try:
        first = run.start("question", "explorer", "first", lambda mailbox: answer.append(mailbox.ask("format?", "q", 2)) or {"answer": "ready", "steps_used": 1})
        second = run.start("work", "explorer", "second", lambda mailbox: release.wait(2) or {"answer": "done", "steps_used": 1})
        run.wait(1)
        questions = run.parent.drain(2)
        assert [m["content"] for m in questions] == ["format?"]
        receipt = run.send(first["subagent_id"], "Markdown", "reply", question_id=questions[0]["id"])
        assert receipt["delivery"] == "queued"
        assert run.send(first["subagent_id"], "Markdown", "reply", question_id=questions[0]["id"])["id"] == receipt["id"]
        release.set()
    finally:
        release.set()
        pool.shutdown()
        run.close("finished")
    assert answer[0]["messages"][0]["content"] == "Markdown"
    assert {first["subagent_id"], second["subagent_id"]} == set(run.children)


def test_instruction_wakes_child_without_answering_its_question(tmp_path):
    run, pool = make_run(tmp_path)
    response = []
    finished = threading.Event()
    try:
        child = run.start("task", "explorer", "child", lambda mailbox: response.append(mailbox.ask("missing input", "q", 2)) or finished.wait(2) or {"answer": "done"})
        run.wait(1)
        question = run.parent.drain(1)[0]
        run.send(child["subagent_id"], "check local files first", "instruction")
        for _ in range(20):
            if response:
                break
            threading.Event().wait(.01)
        assert response[0]["question_state"] == "open"
        assert response[0]["messages"][0]["kind"] == "instruction"
        assert run.store.list_collaboration_messages("s")[0]["question_state"] == "open"
    finally:
        finished.set()
        pool.shutdown()
        run.close("finished")


def test_unknown_attempt_and_cancelled_receiver_are_rejected(tmp_path):
    run, pool = make_run(tmp_path)
    release = threading.Event()
    try:
        child = run.start("task", "explorer", "child", lambda mailbox: release.wait(2) or {"answer": "done"})
        with pytest.raises(ValueError):
            run.send("unrelated", "x", "request")
        run.children[child["subagent_id"]].entry.cancel_event.set()
        with pytest.raises(ValueError):
            run.send(child["subagent_id"], "x", "request")
    finally:
        release.set()
        pool.shutdown()
        run.close("finished")


def test_receipt_failure_does_not_inject_or_continue(tmp_path, monkeypatch):
    run, pool = make_run(tmp_path)
    release = threading.Event()
    try:
        child = run.start("task", "explorer", "child", lambda mailbox: mailbox.report("ready", "progress") or release.wait(2))
        with run.condition:
            run.condition.wait_for(lambda: bool(run._pending("parent")), timeout=1)
        context = [{"role": "user", "content": "Original"}]

        def fail(*args):
            raise OSError("disk full")

        monkeypatch.setattr(run.store, "apply_collaboration_messages", fail)
        with pytest.raises(OSError):
            run.parent.inject(context, 2)
        assert context == [{"role": "user", "content": "Original"}]
        assert run.store.list_collaboration_messages("s")[0]["delivery"] == "queued"
    finally:
        release.set()
        pool.shutdown()
        run.close("finished")


def test_cancellation_keeps_worker_active_until_it_exits(tmp_path):
    run, pool = make_run(tmp_path)
    release = threading.Event()
    entered = threading.Event()

    def work(mailbox):
        entered.set()
        release.wait(2)
        return {"answer": "late answer"}

    try:
        child = run.start("task", "explorer", "child", work)
        assert entered.wait(1)
        run.close("cancelled")
        assert run.active is True
        assert run.children[child["subagent_id"]].entry.status == "cancelling"
        release.set()
        pool.shutdown()
        assert run.active is False
        assert run.children[child["subagent_id"]].entry.status == "interrupted"
        assert not any(m["content"] == "late answer" for m in run.store.list_collaboration_messages("s"))
    finally:
        release.set()
        pool.shutdown()


def test_parent_finalization_waits_without_new_model_rounds(tmp_path, monkeypatch):
    run, pool = make_run(tmp_path)
    entry = run.context.started("child", "explorer", "task")
    child = ChildTask(entry, "attempt")
    run.children[entry.subagent_id] = child
    waits = []

    def wait(timeout):
        waits.append(timeout)
        if len(waits) == 2:
            run._send(child, entry.subagent_id, "parent", "result", "done", "result")
            child.ended = True

    monkeypatch.setattr(run, "wait", wait)
    try:
        context = []
        assert run.parent.before_finish(context, 3) is True
        assert waits == [30, 30]
        assert "done" in context[0]["content"]
    finally:
        child.ended = True
        run.close("finished")
        pool.shutdown()


def test_terminal_result_has_reserved_capacity_and_event_projection_is_incremental(tmp_path):
    run, pool = make_run(tmp_path)
    entry = run.context.started("child", "explorer", "task")
    child = ChildTask(entry, "attempt")
    run.children[entry.subagent_id] = child
    published = []
    original = run.context.event

    def event(entry, kind, **fields):
        if kind == "communication":
            published.append(fields["messages"])
        original(entry, kind, **fields)

    run.context.event = event
    try:
        for i in range(256):
            run._send(child, entry.subagent_id, "parent", "progress", f"progress {i}", str(i))
        run._send(child, entry.subagent_id, "parent", "result", "done", "result")
        assert run._messages()[-1]["content"] == "done"
        assert all(len(delta) == 1 for delta in published)
    finally:
        child.ended = True
        run.close("finished")
        pool.shutdown()


def test_failed_worker_submission_does_not_leave_an_active_child(tmp_path):
    run, pool = make_run(tmp_path)
    pool.shutdown()
    with pytest.raises(RuntimeError):
        run.start("task", "explorer", "child", lambda mailbox: {"answer": "done"})
    assert run.active is False


def test_managed_timeout_reports_deadline_and_retains_worker_until_exit(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from agentnexus.core.config import get_settings
    from agentnexus.services.chat import ChatService
    from agentnexus.tools.subagent import _run_subagent_attempt

    store = TimelineStore(str(tmp_path / "timeline.db"))
    monkeypatch.setattr("agentnexus.services.chat.get_timeline_store", lambda: store)
    monkeypatch.setattr("agentnexus.observability.timeline.get_timeline_store", lambda: store)
    monkeypatch.setattr(get_settings(), "subagent_timeout_sec", .05)
    monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _: MagicMock())
    monkeypatch.setattr("agentnexus.tools.subagent._register_child_tools", lambda *a, **kw: None)
    release = threading.Event()
    entered = threading.Event()

    def agent_run(self, *args, **kwargs):
        entered.set()
        release.wait(2)
        return MagicMock(answer="late answer", steps=[])

    monkeypatch.setattr("agentnexus.tools.subagent.ReActAgent.run", agent_run)
    service = ChatService(agent_factory=lambda _: None, memory_factory_builder=lambda _: lambda: None)
    pool = ThreadPoolExecutor(1)
    context = SubagentRunContext(service, "s", "p")
    run = CollaborationRun(context, store, pool, lambda: False)

    def execute(mailbox):
        result, error = _run_subagent_attempt(None, True, "task", "explorer", ["file_read"], subagent_ctx=context, subagent_entry=mailbox.child.entry, mailbox=mailbox)
        if error:
            raise error
        return result

    try:
        child = run.start("task", "explorer", "child", execute)
        assert entered.wait(1)
        for _ in range(50):
            if run.children[child["subagent_id"]].entry.status == "cancelling":
                break
            threading.Event().wait(.01)
        assert run.active is True
        assert run.children[child["subagent_id"]].entry.status == "cancelling"
        release.set()
        pool.shutdown()
        entry = run.children[child["subagent_id"]].entry
        assert entry.status == "interrupted"
        assert "执行期限" in entry.error
    finally:
        release.set()
        pool.shutdown()
        run.close("finished")


def test_managed_child_holds_capacity_until_timed_out_tool_exits(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from agentnexus.services.chat import ChatService
    from agentnexus.tools.registry import ToolMeta
    from agentnexus.tools.subagent import _run_subagent_attempt

    store = TimelineStore(str(tmp_path / "timeline.db"))
    monkeypatch.setattr("agentnexus.services.chat.get_timeline_store", lambda: store)
    monkeypatch.setattr("agentnexus.observability.timeline.get_timeline_store", lambda: store)
    monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _: MagicMock())
    release, returned = threading.Event(), threading.Event()

    def register(executor, *args, **kwargs):
        executor.register(ToolMeta(name="blocked", description="blocked", param_schema={}, timeout_sec=.02), lambda: release.wait(2))

    monkeypatch.setattr("agentnexus.tools.subagent._register_child_tools", register)

    def agent_run(self, *args, **kwargs):
        try:
            self.tool_executor.invoke("blocked", {})
        except TimeoutError:
            returned.set()
        return MagicMock(answer="premature result", steps=[])

    monkeypatch.setattr("agentnexus.tools.subagent.ReActAgent.run", agent_run)
    service = ChatService(agent_factory=lambda _: None, memory_factory_builder=lambda _: lambda: None)
    pool = ThreadPoolExecutor(1)
    context = SubagentRunContext(service, "s", "p")
    run = CollaborationRun(context, store, pool, lambda: False)

    def execute(mailbox):
        result, error = _run_subagent_attempt(None, True, "task", "explorer", ["blocked"], subagent_ctx=context, subagent_entry=mailbox.child.entry, mailbox=mailbox)
        if error:
            raise error
        return result

    try:
        child = run.start("task", "explorer", "child", execute)
        assert returned.wait(1)
        with run.condition:
            run.condition.wait_for(lambda: run.children[child["subagent_id"]].entry.status == "cancelling", timeout=.5)
        assert run.active is True
        release.set()
        pool.shutdown()
        assert run.children[child["subagent_id"]].entry.status == "interrupted"
    finally:
        release.set()
        pool.shutdown()
        run.close("finished")
