"""Reproduction: assistant entries missing from conversation_messages journal
after turns that DID produce answers (checkpoints have them).

Driven by the screenshot session b629e0f2f995: turns "hello" and
"hello, what's your name?" have checkpoints with answers but the journal only
contains the user rows — [思考过程]/[最终答案]/tool entries never landed.
"""
import queue
from unittest.mock import MagicMock

import pytest

from agentnexus.services.chat import ChatService


class _FakeShortTerm:
    """List-backed STM with the ShortTermMemory surface the turn finish uses."""

    def __init__(self):
        self._messages = []

    def append(self, role, content, metadata=None):
        self._messages.append({"role": role, "content": content})

    def get_all(self):
        return list(self._messages)


class _FakeMemory:
    def __init__(self):
        self.short_term = _FakeShortTerm()

    def append(self, role, content, metadata=None):
        self.short_term.append(role, content, metadata=metadata)


class _Answer:
    def __init__(self, text):
        self.answer = text


def _service_with(tmp_path, monkeypatch, db_name="mem.db"):
    """ChatService backed by a real ConversationVersionManager on a tmp DB."""
    from agentnexus.core.config import get_settings
    monkeypatch.setattr(get_settings(), "memory_db_path", str(tmp_path / db_name))
    service = ChatService(
        agent_factory=lambda _sid=None: MagicMock(),
        memory_factory_builder=lambda _sid: lambda: _FakeMemory(),
    )
    return service


def _agent_that(appends):
    """Fake agent mimicking ReActAgent's memory usage."""
    agent = MagicMock()
    agent.llm_client.model = "mock"
    agent.agent_id = "mock"
    agent.max_steps = 5
    agent._total_usage = {}
    agent._step_count = 0

    def run(question, memory_manager=None, **kw):
        for role, content in appends:
            memory_manager.append(role, content)
        return _Answer(f"answer-to:{question}")
    agent.run.side_effect = run
    return agent


def _journal(service, sid):
    return service._get_version_manager(sid).get_messages(limit=0)


def test_first_turn_persists_assistant_entries(tmp_path, monkeypatch):
    service = _service_with(tmp_path, monkeypatch)
    sid = service.start_session().id
    service._agents[sid] = _agent_that([
        ("system", "[思考过程] thinking about it"),
        ("system", "[最终答案] final answer text"),
    ])
    service.send_message(sid, "hello, what's your name?")
    roles = [(m["role"], m["content"][:30]) for m in _journal(service, sid)]
    print("JOURNAL:", roles)
    assert any("[最终答案]" in c for _, c in roles), f"[最终答案] missing: {roles}"
    assert any("[思考过程]" in c for _, c in roles), f"[思考过程] missing: {roles}"


def test_second_turn_after_restart_persists(tmp_path, monkeypatch):
    """Simulate app restart: brand-new ChatService on the same DB."""
    db = "mem.db"
    s1 = _service_with(tmp_path, monkeypatch, db)
    sid = s1.start_session().id
    s1._agents[sid] = _agent_that([("system", "[最终答案] A1")])
    s1.send_message(sid, "hello")

    s2 = _service_with(tmp_path, monkeypatch, db)
    s2._sessions[sid] = s1._sessions[sid]  # restored session handle
    s2._agents[sid] = _agent_that([
        ("system", "[思考过程] T2 thought"),
        ("system", "[最终答案] A2"),
    ])
    s2.send_message(sid, "Do you like me?")

    roles = [(m["role"], m["content"][:30]) for m in _journal(s2, sid)]
    print("JOURNAL:", roles)
    assert any("A1" in c for _, c in roles), f"first answer missing: {roles}"
    assert any("A2" in c for _, c in roles), f"second answer missing: {roles}"
    assert any("T2" in c for _, c in roles), f"second thought missing: {roles}"


def test_turn_with_tool_and_empty_assistant_appends(tmp_path, monkeypatch):
    """The 表情包 turn pattern: empty assistant appends + tool entry."""
    service = _service_with(tmp_path, monkeypatch)
    sid = service.start_session().id
    service._agents[sid] = _agent_that([
        ("assistant", ""),   # empty display_only thought (should not be persisted)
        ("assistant", ""),   # ditto
        ("tool", "Action: express_reaction[{\"reaction\": \"like\"}]"),
        ("assistant", "summary thought"),
        ("system", "[最终答案] 当然喜欢！"),
    ])
    service.send_message(sid, "用表情包工具来回复我的这个问题")
    msgs = _journal(service, sid)
    empties = [m for m in msgs if m["role"] == "assistant" and not m["content"].strip()]
    print("JOURNAL:", [(m["role"], m["content"][:30]) for m in msgs])
    assert not empties, f"empty assistant rows persisted: {empties}"


def test_checkpoint_backfill_synthesizes_missing_answers(tmp_path, monkeypatch):
    """Sessions damaged by the count-based slice have user rows without
    [最终答案]; the answer lives in checkpoint metadata. The history reader
    must synthesize the marker row."""
    from agentnexus.memory.versioned import ConversationVersionManager
    from agentnexus.core.config import get_settings
    monkeypatch.setattr(get_settings(), "memory_db_path", str(tmp_path / "mem.db"))

    vm = ConversationVersionManager("sess_x", str(tmp_path / "mem.db"))
    vm.commit_with_messages([{"role": "user", "content": "q1"}], question="q1", answer="")
    vm.commit_with_messages([{"role": "user", "content": "q2"}], question="q2", answer="")
    vm.commit_with_messages(
        [{"role": "system", "content": "[最终答案] a2"}], question="q2", answer="a2")
    # checkpoint metadata holding q1's lost answer (as conclude() recorded it)
    vm.commit_with_messages([], question="q1", answer="a1-final")

    msgs = vm.get_messages_with_answers()
    contents = [(m["role"], m["content"]) for m in msgs]
    print("BACKFILLED:", contents)
    # q1 gains a synthesized [最终答案] right after its user row; q2 untouched
    assert ("system", "[最终答案] a1-final") in contents
    q1_idx = next(i for i, m in enumerate(msgs) if m["content"] == "q1")
    assert msgs[q1_idx + 1]["content"] == "[最终答案] a1-final"
    assert sum(1 for m in msgs if str(m["content"]).startswith("[最终答案] a2")) == 1
