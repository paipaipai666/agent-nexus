"""Subagent wall-clock deadline: fires cooperative cancel, no orphan thread.

Regression for the 2026-10-07 incident: the 30s lane timeout returned an error
but left the child thread running, holding the torch allocator high-water.
"""

from __future__ import annotations

import threading
import time

import pytest

import agentnexus.core.config as config_mod
from agentnexus.agents.exceptions import AgentCancelled
from agentnexus.core import llm as llm_mod
from agentnexus.tools.subagent import _run_subagent_attempt


@pytest.fixture
def tiny_deadline(monkeypatch):
    monkeypatch.setattr(config_mod.get_settings(), "subagent_timeout_sec", 2)


@pytest.fixture
def tool_then_answer(monkeypatch):
    """First think: slow tool call (8s > 2s deadline). Second: final answer."""
    calls = {"n": 0}

    def _think(self, messages, **kw):
        calls["n"] += 1
        time.sleep(8)  # exceeds the 2s deadline, finishes inside the 120s grace
        if calls["n"] == 1:
            return '{"thought": "probe", "tool": "file_list", "params": {"path": "."}}'
        return '{"answer": "done"}'

    monkeypatch.setattr(llm_mod.AgentLLM, "think", _think)


def _runner_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if "subagent-runner" in t.name]


def test_deadline_cancels_child_without_orphan(tiny_deadline, tool_then_answer):
    t0 = time.monotonic()
    payload, err = _run_subagent_attempt(
        parent_llm=None, non_interactive=True,
        task="probe", role="explorer", tool_names=["file_list"],    )
    elapsed = time.monotonic() - t0

    assert payload is None
    assert isinstance(err, AgentCancelled), f"expected AgentCancelled, got {err!r}"
    # cancel lands at the first FSM boundary after the in-flight think returns:
    # well under the 120s grace
    assert elapsed < 60, f"cancel took too long: {elapsed:.1f}s"
    # child thread must actually exit — no orphan holding allocator memory
    for _ in range(50):
        if not _runner_threads():
            break
        time.sleep(0.1)
    assert not _runner_threads(), "subagent-runner thread leaked"
