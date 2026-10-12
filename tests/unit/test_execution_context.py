import asyncio
import contextvars
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketDisconnect

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.agents.tool_runner import execute_tool
from agentnexus.core.config import get_settings
from agentnexus.core.hook_schemas import HookConfig
from agentnexus.core.hooks import HookManager, HookType
from agentnexus.server.routes.chat import ws_agent
from agentnexus.services.chat import ChatService
from agentnexus.tools.confirm_bridge import ConfirmBridge
from agentnexus.tools.dispatcher import ToolDispatcher
from agentnexus.tools.errors import ToolError, ToolErrorCode
from agentnexus.tools.file_ops import file_read, file_write
from agentnexus.tools.registry import ToolMeta, ToolRegistry
from agentnexus.tools.subagent import make_subagent_run
from agentnexus.tools.workspace import (
    approve_extra_path,
    clear_approved_paths,
    current_workspace,
    get_approved_extra_paths,
)


@pytest.fixture
def project_files(tmp_path, monkeypatch):
    service_dir = tmp_path / "service"
    project = tmp_path / "project"
    service_dir.mkdir()
    project.mkdir()
    (project / "pyproject.toml").write_text("project metadata", encoding="utf-8")
    monkeypatch.chdir(service_dir)
    token = current_workspace.set(str(project))
    clear_approved_paths()
    try:
        yield project
    finally:
        current_workspace.reset(token)
        clear_approved_paths()


@pytest.fixture
def file_registry():
    registry = ToolRegistry()
    registry.register(ToolMeta(
        name="file_read", description="Read a project file",
        param_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        concurrency_safe=True,
    ), file_read)
    registry.register(ToolMeta(
        name="file_write", description="Write a project file",
        param_schema={"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"},
        }, "required": ["path", "content"]},
    ), file_write)
    try:
        yield registry
    finally:
        registry.close()


def test_registry_reads_selected_project_root_from_another_process_cwd(project_files, file_registry):
    result = file_registry.invoke("file_read", {"path": str(project_files / "pyproject.toml")})
    assert "project metadata" in result


def test_registry_resolves_relative_write_in_selected_project(project_files, file_registry):
    file_registry.invoke("file_write", {"path": "result.txt", "content": "project output"})
    assert (project_files / "result.txt").read_text(encoding="utf-8") == "project output"
    assert not Path("result.txt").exists()


def test_timed_hook_reads_selected_project(project_files):
    manager = HookManager()

    def read_metadata(ctx):
        ctx.payload["metadata"] = file_read("pyproject.toml")

    manager.register(HookType.BEFORE_TOOL_CALL, read_metadata, timeout=2)
    result = manager.fire(HookType.BEFORE_TOOL_CALL, {"name": "file_read", "params": {}, "caller": "react_agent"})
    assert "project metadata" in result.payload["metadata"]


@pytest.mark.parametrize("event, asynchronous", [
    ("before_tool_call", True), ("after_tool_call", True), ("after_tool_call", False),
])
def test_command_hook_writes_in_selected_project(project_files, monkeypatch, event, asynchronous):
    settings = get_settings()
    command = f'"{sys.executable}" -c "from pathlib import Path; Path(\'hook.txt\').write_text(\'hook output\')"'
    monkeypatch.setattr(settings, "hooks_enabled", True)
    monkeypatch.setattr(settings, "hooks", [HookConfig.model_validate({
        "event": event, "command": command, "async": asynchronous,
    })])
    with ThreadPoolExecutor(max_workers=2) as pool:
        monkeypatch.setattr("agentnexus.core.hook_executor._OBSERVER_POOL", pool)
        HookManager().fire(HookType(event), {"name": "file_read", "params": {}, "caller": "react_agent", "result": "ok"})
    assert (project_files / "hook.txt").read_text(encoding="utf-8") == "hook output"
    assert not Path("hook.txt").exists()


def test_agent_tool_call_retains_workspace_through_both_worker_pools(project_files, file_registry):
    prompts = []

    def approve(summary):
        prompts.append(summary)
        return True

    result = execute_tool(
        tool_executor=file_registry, name="file_read",
        arguments={"path": str(project_files / "pyproject.toml")},
        caller="react_agent", hitl_approver=approve,
    )
    assert isinstance(result, str)
    assert "project metadata" in result
    assert prompts == []


@pytest.mark.parametrize("lane", ["", "subagent"])
def test_dispatcher_preserves_workspace_for_parallel_and_lane_tools(project_files, file_registry, lane):
    file_registry.get_meta("file_read").lane = lane

    def invoke(name, arguments):
        return execute_tool(
            tool_executor=file_registry, name=name, arguments=arguments,
            caller="react_agent", hitl_approver=lambda _summary: True,
        )

    results = ToolDispatcher(file_registry).execute([
        {"name": "file_read", "arguments": {"path": str(project_files / "pyproject.toml")}},
        {"name": "file_read", "arguments": {"path": "pyproject.toml"}},
    ], invoke)
    assert len(results) == 2
    assert all(isinstance(item.result, str) and "project metadata" in item.result for item in results)


@pytest.mark.parametrize("name", ["file_read", "file_write"])
@pytest.mark.parametrize("approved", [False, True])
def test_outside_file_requires_owning_run_approval(project_files, file_registry, name, approved):
    outside = project_files.parent / "outside.txt"
    if name == "file_read":
        outside.write_text("external contents", encoding="utf-8")
    arguments = {"path": str(outside)}
    if name == "file_write":
        arguments["content"] = "approved output"
    bridge = ConfirmBridge()
    prompts = []
    bridge.set_target(lambda summary: prompts.append(summary) or approved, thread_id=threading.get_ident())
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(
                contextvars.copy_context().run, execute_tool,
                tool_executor=file_registry, name=name, arguments=arguments,
                caller="react_agent", hitl_approver=bridge,
            ).result(timeout=3)
        assert len(prompts) == 1
        assert str(outside) in prompts[0]
        assert str(project_files) in prompts[0]
        if not approved:
            assert isinstance(result, ToolError)
            assert result.error_code == ToolErrorCode.HITL_BLOCKED
            assert get_approved_extra_paths() == ()
            if name == "file_write":
                assert not outside.exists()
        elif name == "file_read":
            assert isinstance(result, str) and "external contents" in result
        else:
            assert not isinstance(result, ToolError)
            assert outside.read_text(encoding="utf-8") == "approved output"
    finally:
        bridge.set_target(None, thread_id=threading.get_ident())


def test_shared_registry_isolates_parallel_sessions_and_approved_paths(project_files, file_registry):
    other_project = project_files.parent / "other_project"
    other_project.mkdir()
    (other_project / "pyproject.toml").write_text("other project metadata", encoding="utf-8")
    outside = project_files.parent / "outside.txt"
    outside.write_text("external contents", encoding="utf-8")
    bridge = ConfirmBridge()
    both_prompts = threading.Barrier(2)
    file_registry.get_meta("file_read").lane = "subagent"

    def run(project, approved):
        token = current_workspace.set(str(project))
        prompts = []

        def confirm(summary):
            prompts.append(summary)
            both_prompts.wait(timeout=3)
            return approved

        bridge.set_target(confirm, thread_id=threading.get_ident())
        try:
            def invoke(name, arguments):
                return execute_tool(
                    tool_executor=file_registry, name=name, arguments=arguments,
                    caller="react_agent", hitl_approver=bridge,
                )

            results = ToolDispatcher(file_registry).execute([
                {"name": "file_read", "arguments": {"path": "pyproject.toml"}},
                {"name": "file_read", "arguments": {"path": str(outside)}},
            ], invoke)
            return [item.result for item in results], prompts, get_approved_extra_paths()
        finally:
            bridge.set_target(None, thread_id=threading.get_ident())
            current_workspace.reset(token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = pool.submit(run, project_files, True)
        rejected = pool.submit(run, other_project, False)
        accepted_results, accepted_prompts, accepted_paths = accepted.result(timeout=5)
        rejected_results, rejected_prompts, rejected_paths = rejected.result(timeout=5)
    assert "project metadata" in accepted_results[0]
    assert "external contents" in accepted_results[1]
    assert "other project metadata" in rejected_results[0]
    assert isinstance(rejected_results[1], ToolError)
    assert rejected_results[1].error_code == ToolErrorCode.HITL_BLOCKED
    assert len(accepted_prompts) == len(rejected_prompts) == 1
    assert str(project_files) in accepted_prompts[0]
    assert str(other_project) in rejected_prompts[0]
    assert accepted_paths == (str(outside),)
    assert rejected_paths == ()
    result = file_registry.invoke("file_read", {"path": "pyproject.toml"})
    assert "project metadata" in result and "other project metadata" not in result


def test_approved_paths_can_be_read_while_another_tool_grants_access(project_files, monkeypatch):
    from agentnexus.tools import workspace

    first = project_files.parent / "first.txt"
    second = project_files.parent / "second.txt"
    approve_extra_path(first)
    entered = threading.Event()
    release = threading.Event()

    def resolve_workspace():
        if threading.current_thread().name.startswith("authorization-reader"):
            entered.set()
            assert release.wait(timeout=2)
        return project_files

    monkeypatch.setattr(workspace, "get_effective_workspace", resolve_workspace)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="authorization-reader") as pool:
        pending = pool.submit(contextvars.copy_context().run, get_approved_extra_paths)
        try:
            assert entered.wait(timeout=2)
            approve_extra_path(second)
        finally:
            release.set()
        assert set(pending.result(timeout=2)) == {str(first), str(second)}


def _child_llm(path="pyproject.toml", expected="project metadata"):
    llm = MagicMock()
    llm.model = "test/model"
    llm.base_url = ""
    llm.last_error = ""
    llm.last_truncated = False
    llm.last_tool_calls = []
    llm.last_reasoning_content = ""
    llm.last_usage = {"input_tokens": 10, "output_tokens": 5}
    llm.capabilities.supports_thinking = False
    llm.capabilities.supports_tool_calling = True
    llm.capabilities.supports_json_mode = True
    llm.capabilities.supports_json_schema = False
    llm.capabilities.supports_parallel_tool_calls = False
    calls = 0

    def think(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            llm.last_tool_calls = [{"id": "metadata_read", "name": "file_read", "arguments": {"path": path}}]
            return "Read project metadata"
        llm.last_tool_calls = []
        observations = " ".join(str(m.get("content", "")) for m in kwargs["messages"] if m["role"] == "tool")
        return "Metadata verified" if expected in observations else "Metadata inaccessible"

    llm.think.side_effect = think
    return llm


def test_real_subagent_reads_parent_project_metadata(project_files, monkeypatch):
    monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _parent: _child_llm())
    prompts = []

    def approve(summary):
        prompts.append(summary)
        return True

    result = json.loads(make_subagent_run(
        non_interactive=True, subagent_confirm=approve,
    )(task="Verify project metadata", allowed_tools=["file_read"]))
    assert result["answer"] == "Metadata verified"
    assert prompts == []


@pytest.mark.parametrize("approved", [False, True])
def test_real_subagent_requests_outside_path_approval_from_parent_run(project_files, monkeypatch, approved):
    outside = project_files.parent / "outside.txt"
    outside.write_text("external contents", encoding="utf-8")
    monkeypatch.setattr(
        "agentnexus.tools.subagent._clone_llm",
        lambda _parent: _child_llm(str(outside), "external contents"),
    )
    bridge = ConfirmBridge()
    prompts = []
    bridge.set_target(lambda summary: prompts.append(summary) or approved, thread_id=threading.get_ident())
    try:
        result = json.loads(make_subagent_run(
            non_interactive=True, subagent_confirm=bridge,
        )(task="Verify external metadata", allowed_tools=["file_read"]))
        assert result["answer"] == ("Metadata verified" if approved else "Metadata inaccessible")
        assert len(prompts) == 1
        assert str(outside) in prompts[0]
        assert str(project_files) in prompts[0]
    finally:
        bridge.set_target(None, thread_id=threading.get_ident())


@pytest.mark.asyncio
@pytest.mark.parametrize("outside_project, approved", [(False, True), (True, True), (True, False)])
async def test_websocket_delivers_real_child_path_confirmation(
    project_files, file_registry, monkeypatch, outside_project, approved,
):
    path = project_files.parent / "outside.txt" if outside_project else project_files / "pyproject.toml"
    expected = "external contents" if outside_project else "project metadata"
    if outside_project:
        path.write_text(expected, encoding="utf-8")
    monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _parent: _child_llm(str(path), expected))
    bridge = ConfirmBridge()
    file_registry.register(ToolMeta(
        name="subagent_run", description="Verify metadata with a child agent", lane="subagent",
        param_schema={"type": "object", "properties": {
            "task": {"type": "string"}, "allowed_tools": {"type": "array", "items": {"type": "string"}},
        }, "required": ["task"]},
    ), make_subagent_run(non_interactive=True, subagent_confirm=bridge))
    parent_llm = _child_llm()
    calls = 0

    def think(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            parent_llm.last_tool_calls = [{"id": "child", "name": "subagent_run", "arguments": {
                "task": "Verify metadata", "allowed_tools": ["file_read"],
            }}]
            return "Delegate metadata verification"
        parent_llm.last_tool_calls = []
        observations = " ".join(str(m.get("content", "")) for m in kwargs["messages"] if m["role"] == "tool")
        return "Access verified" if "Metadata verified" in observations else "Access blocked"

    parent_llm.think.side_effect = think
    agent = ReActAgent(llm_client=parent_llm, tool_executor=file_registry, confirm_fn=bridge, max_steps=3)
    service = ChatService(
        agent_factory=lambda _sid=None: agent,
        memory_factory_builder=lambda _sid: lambda: None,
    )
    session = service.start_session(workspace=str(project_files))
    monkeypatch.setattr("agentnexus.server.app._get_runtime", lambda: SimpleNamespace(
        chat=service, subagent_confirm=bridge,
    ))
    ws = AsyncMock()
    incoming = asyncio.Queue()
    await incoming.put({"type": "send_message", "content": "Verify project metadata"})
    sent = []

    async def receive_json():
        message = await asyncio.wait_for(incoming.get(), timeout=5)
        if isinstance(message, Exception):
            raise message
        return message

    async def send_json(payload):
        sent.append(payload)
        if payload.get("type") == "confirm_request":
            await incoming.put({"type": "confirm", "approved": approved})
        elif payload.get("type") in {"done", "error"}:
            await incoming.put(WebSocketDisconnect())

    ws.receive_json.side_effect = receive_json
    ws.send_json.side_effect = send_json
    await asyncio.wait_for(ws_agent(ws, session.id), timeout=8)
    assert not [event for event in sent if event.get("type") == "error"]
    confirmations = [event for event in sent if event.get("type") == "confirm_request"]
    assert len(confirmations) == int(outside_project)
    if outside_project:
        assert str(path) in confirmations[0]["summary"]
        assert str(project_files) in confirmations[0]["summary"]
    answer = next(event for event in sent if event.get("type") == "answer")
    assert answer["content"] == ("Access verified" if approved else "Access blocked")
