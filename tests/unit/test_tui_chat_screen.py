"""Tests for ChatScreen static methods, MCP formatting, and command helpers."""

import inspect
import json
from unittest.mock import MagicMock, patch

from textual.events import Key

from agentnexus.skills.registry import SkillEntry, SkillRegistry
from agentnexus.skills.workflow import Workflow
from agentnexus.tui.screens.chat import ChatScreen


class TestCondenseSearchResult:
    def test_keeps_header_lines(self):
        text = "[1] Title\nURL: https://example.com\nbody content here"
        result = ChatScreen._condense_search_result(text)
        assert "[1]" in result
        assert "URL:" in result
        assert "body content" not in result

    def test_removes_body(self):
        text = (
            "[1] Result A\nURL: http://a.com\nsome body\n"
            "[2] Result B\nURL: http://b.com\nmore body"
        )
        result = ChatScreen._condense_search_result(text)
        assert "[1]" in result
        assert "[2]" in result
        assert "body" not in result

    def test_empty_result(self):
        assert ChatScreen._condense_search_result("") == ""

    def test_fallback_truncation(self):
        text = "no markers " + "x" * 500
        result = ChatScreen._condense_search_result(text)
        assert len(result) == 500
        assert result == text[:500]


class TestCondenseFileResult:
    def test_keeps_first_line(self):
        text = "[文件] path/to/file.py (10 行, 共 200 字节)\n1 | line1\n2 | line2"
        result = ChatScreen._condense_file_result(text)
        assert result == "[文件] path/to/file.py (10 行, 共 200 字节)"
        assert "line1" not in result

    def test_truncates_long(self):
        text = "x" * 300
        result = ChatScreen._condense_file_result(text)
        assert len(result) == 200

    def test_empty(self):
        assert ChatScreen._condense_file_result("") == ""


class TestFormatMcpHelpers:
    def test_format_mcp_status(self):
        result = ChatScreen._format_mcp_status({
            "started": True,
            "server_count": 2,
            "connected_count": 1,
            "failure_count": 1,
            "tool_count": 3,
            "servers": [
                {
                    "name": "docs",
                    "transport": "stdio",
                    "connected": True,
                    "tool_names": ["mcp_docs__search"],
                    "failure": None,
                },
                {
                    "name": "remote",
                    "transport": "streamable_http",
                    "connected": False,
                    "tool_names": [],
                    "failure": "boom",
                },
            ],
        })
        assert "MCP 状态" in result
        assert "1/2 已连接" in result
        assert "docs" in result
        assert "remote" in result

    def test_format_mcp_tools(self):
        result = ChatScreen._format_mcp_tools({
            "servers": [
                {"name": "docs", "tool_names": ["mcp_docs__search", "mcp_docs__open"]},
            ]
        })
        assert "MCP Tools" in result
        assert "mcp_docs__search" in result
        assert "mcp_docs__open" in result

    def test_format_mcp_resources(self):
        result = ChatScreen._format_mcp_resources({
            "servers": [
                {
                    "name": "docs",
                    "resource_count": 2,
                    "resource_template_count": 1,
                    "resource_tool_names": ["mcp_docs__read_resource"],
                },
            ]
        })
        assert "MCP Resources" in result
        assert "resources=2" in result
        assert "mcp_docs__read_resource" in result

    def test_format_mcp_prompts(self):
        result = ChatScreen._format_mcp_prompts({
            "servers": [
                {"name": "docs", "prompt_count": 1, "prompt_tool_names": ["mcp_docs__get_prompt"]},
            ]
        })
        assert "MCP Prompts" in result
        assert "prompts=1" in result
        assert "mcp_docs__get_prompt" in result

    def test_format_mcp_tools_server_not_found(self):
        result = ChatScreen._format_mcp_tools({"servers": []}, server_name="missing")
        assert "未找到 MCP server" in result

    def test_format_mcp_failures(self):
        result = ChatScreen._format_mcp_failures({
            "servers": [{"name": "remote", "failure": "timeout"}]
        })
        assert "MCP Failures" in result
        assert "timeout" in result

    def test_format_mcp_failures_empty(self):
        result = ChatScreen._format_mcp_failures({"servers": []})
        assert "当前没有 MCP 失败项" in result

    def test_format_mcp_retry_result(self):
        result = ChatScreen._format_mcp_retry_result({
            "retried": ["remote"],
            "reconnected": ["remote"],
            "skipped": ["docs"],
            "failed": {},
        })
        assert "MCP Retry" in result
        assert "remote" in result
        assert "docs" in result


class TestMcpCommandHelpers:
    def test_help_includes_mcp(self):
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._chat_area = MagicMock()
        screen.action_show_help()
        message = screen._chat_area.add_system.call_args[0][0]
        assert "/mcp" in message

    def test_handle_mcp_without_manager(self):
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("")
        assert "未启用 MCP" in screen._chat_area.add_system.call_args[0][0]

    def test_handle_mcp_retry_re_registers_tools(self):
        manager = MagicMock()
        manager.retry_failed.return_value = {
            "retried": ["remote"],
            "reconnected": ["remote"],
            "skipped": [],
            "failed": {},
        }
        agent = MagicMock()
        screen = ChatScreen(agent=agent, memory=None, version=None, mcp_manager=manager)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("retry remote")
        manager.retry_failed.assert_called_once_with(server_name="remote")
        manager.register_tools.assert_called_once_with(agent.tool_executor)

    def test_handle_mcp_retry_failed_alias(self):
        manager = MagicMock()
        manager.retry_failed.return_value = {
            "retried": ["remote"],
            "reconnected": [],
            "skipped": [],
            "failed": {"remote": "boom"},
        }
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, mcp_manager=manager)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("retry --failed")
        manager.retry_failed.assert_called_once_with(server_name=None)

    def test_handle_mcp_unknown_subcommand(self):
        manager = MagicMock()
        manager.status_snapshot.return_value = {"servers": []}
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, mcp_manager=manager)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("unknown")
        assert "用法: /mcp" in screen._chat_area.add_system.call_args[0][0]

    def test_handle_mcp_status_subcommand(self):
        manager = MagicMock()
        manager.status_snapshot.return_value = {
            "started": True, "server_count": 1, "connected_count": 1,
            "failure_count": 0, "tool_count": 2,
            "servers": [{"name": "docs", "transport": "stdio",
            "connected": True, "tool_names": ["mcp_docs__search"], "failure": None}],
        }
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, mcp_manager=manager)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("status")
        msg = screen._chat_area.add_system.call_args[0][0]
        assert "MCP 状态" in msg
        assert "1/1 已连接" in msg

    def test_handle_mcp_tools_all_servers(self):
        manager = MagicMock()
        manager.status_snapshot.return_value = {
            "servers": [
                {"name": "docs", "tool_names": ["mcp_docs__search", "mcp_docs__open"]},
                {"name": "api", "tool_names": ["mcp_api__echo"]},
            ],
        }
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, mcp_manager=manager)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("tools")
        msg = screen._chat_area.add_system.call_args[0][0]
        assert "MCP Tools" in msg
        assert "mcp_docs__search" in msg
        assert "mcp_api__echo" in msg

    def test_handle_mcp_tools_specific_server(self):
        manager = MagicMock()
        manager.status_snapshot.return_value = {
            "servers": [
                {"name": "docs", "tool_names": ["mcp_docs__search"]},
                {"name": "api", "tool_names": ["mcp_api__echo"]},
            ],
        }
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, mcp_manager=manager)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("tools api")
        msg = screen._chat_area.add_system.call_args[0][0]
        assert "api" in msg
        assert "mcp_api__echo" in msg
        assert "docs" not in msg

    def test_handle_mcp_tools_server_not_found(self):
        manager = MagicMock()
        manager.status_snapshot.return_value = {"servers": []}
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, mcp_manager=manager)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("tools nonexistent")
        msg = screen._chat_area.add_system.call_args[0][0]
        assert "未找到 MCP server" in msg

    def test_handle_mcp_failures_subcommand(self):
        manager = MagicMock()
        manager.status_snapshot.return_value = {
            "servers": [{"name": "remote", "failure": "timeout"}],
        }
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, mcp_manager=manager)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("failures")
        msg = screen._chat_area.add_system.call_args[0][0]
        assert "MCP Failures" in msg
        assert "timeout" in msg

    def test_handle_mcp_failures_none(self):
        manager = MagicMock()
        manager.status_snapshot.return_value = {"servers": [{"name": "ok", "failure": None}]}
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, mcp_manager=manager)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("failures")
        msg = screen._chat_area.add_system.call_args[0][0]
        assert "当前没有 MCP 失败项" in msg

    def test_handle_mcp_retry_re_registers_tools_on_agent(self):
        manager = MagicMock()
        manager.retry_failed.return_value = {
            "retried": ["remote"],
            "reconnected": ["remote"],
            "skipped": [],
            "failed": {},
        }
        agent = MagicMock()
        screen = ChatScreen(agent=agent, memory=None, version=None, mcp_manager=manager)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("retry")
        manager.retry_failed.assert_called_once_with(server_name=None)
        manager.register_tools.assert_called_once_with(agent.tool_executor)

    def test_handle_mcp_retry_without_agent_tool_executor(self):
        """retry should not crash when agent has no tool_executor."""
        manager = MagicMock()
        manager.retry_failed.return_value = {
            "retried": ["remote"],
            "reconnected": ["remote"],
            "skipped": [],
            "failed": {},
        }
        agent = MagicMock()
        agent.tool_executor = None
        screen = ChatScreen(agent=agent, memory=None, version=None, mcp_manager=manager)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("retry")
        manager.register_tools.assert_not_called()

    def test_handle_mcp_enable_uses_capability_runtime(self):
        manager = MagicMock()
        runtime = MagicMock()
        runtime.enable.return_value = {"mcp": "reloaded"}
        screen = ChatScreen(
            agent=MagicMock(),
            memory=None,
            version=None,
            mcp_manager=manager,
            capability_runtime=runtime,
        )
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()

        screen._handle_mcp_command("enable docs")

        runtime.enable.assert_called_once_with("mcp", "docs")
        assert "MCP enable" in screen._chat_area.add_system.call_args[0][0]

    def test_handle_mcp_no_reconnect_skips_register(self):
        """retry with no reconnected servers should not call register_tools."""
        manager = MagicMock()
        manager.retry_failed.return_value = {
            "retried": ["remote"],
            "reconnected": [],
            "skipped": [],
            "failed": {"remote": "boom"},
        }
        agent = MagicMock()
        screen = ChatScreen(agent=agent, memory=None, version=None, mcp_manager=manager)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("retry")
        manager.register_tools.assert_not_called()


class TestFormatSubagentResult:
    def test_valid_json(self):
        text = json.dumps({
            "role": "coder",
            "status": "success",
            "steps_used": 5,
            "allowed_tools": ["bash", "read"],
            "answer": "done",
        })
        result = ChatScreen._format_subagent_result(text)
        assert "子代理" in result
        assert "role=coder" in result
        assert "status=success" in result
        assert "steps=5" in result
        assert "bash" in result
        assert "answer: done" in result

    def test_invalid_json(self):
        text = "this is not json"
        result = ChatScreen._format_subagent_result(text)
        assert result == "this is not json"

    def test_minimal_payload(self):
        text = json.dumps({})
        result = ChatScreen._format_subagent_result(text)
        assert "role=general" in result
        assert "status=unknown" in result
        assert "steps=0" in result
        assert "tools: -" in result

    def test_with_answer(self):
        text = json.dumps({
            "role": "researcher",
            "status": "ok",
            "answer": "found the answer",
            "summary": "ignored summary",
        })
        result = ChatScreen._format_subagent_result(text)
        assert "answer: found the answer" in result
        assert "summary:" not in result

    def test_with_summary(self):
        text = json.dumps({
            "role": "researcher",
            "status": "ok",
            "answer": "",
            "summary": "short summary",
        })
        result = ChatScreen._format_subagent_result(text)
        assert "summary: short summary" in result

    def test_with_allowed_tools(self):
        text = json.dumps({
            "role": "general",
            "status": "ok",
            "allowed_tools": ["bash", "python", "read"],
            "answer": "",
            "summary": "",
        })
        result = ChatScreen._format_subagent_result(text)
        assert "tools: bash, python, read" in result


class TestMcpCommandExceptionHandler:
    def test_exception_during_status_shows_error(self):
        """Exception in _handle_mcp_command must be caught and shown to user."""
        manager = MagicMock()
        manager.status_snapshot.side_effect = RuntimeError("snapshot failed")
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, mcp_manager=manager)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("status")
        msg = screen._chat_area.add_system.call_args[0][0]
        assert "snapshot failed" in msg

    def test_exception_during_tools_shows_error(self):
        """Exception when listing tools must be caught."""
        manager = MagicMock()
        manager.status_snapshot.side_effect = ValueError("tools error")
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, mcp_manager=manager)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("tools")
        msg = screen._chat_area.add_system.call_args[0][0]
        assert "tools error" in msg

    def test_exception_during_retry_shows_error(self):
        """Exception during retry must be caught."""
        manager = MagicMock()
        manager.retry_failed.side_effect = ConnectionError("retry failed")
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, mcp_manager=manager)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("retry")
        msg = screen._chat_area.add_system.call_args[0][0]
        assert "retry failed" in msg

    def test_exception_preserves_chat_area_functionality(self):
        """After exception, the chat area must still be usable."""
        manager = MagicMock()
        manager.status_snapshot.side_effect = RuntimeError("fail")
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, mcp_manager=manager)
        screen._chat_area = MagicMock()
        screen._handle_mcp_command("status")
        # Verify add_system was still called (error path, not crash)
        assert screen._chat_area.add_system.called


def _workflow(workflow_id: str = "code_review") -> Workflow:
    return Workflow.model_validate({
        "id": workflow_id,
        "version": "1",
        "display_name": "Code Review",
        "description": "Review code changes",
        "prompt_profile": {"system": "react"},
        "tool_policy": {"max_risk": "low"},
        "steps": [{"type": "prompt", "id": "gather", "prompt": "Inspect."}],
        "success_criteria": ["Findings are actionable."],
        "resources": [
            {"type": "reference", "path": "references/rules.md", "name": "rules.md", "size_bytes": 10},
        ],
    })


class TestSkillCommandHelpers:
    def test_format_skill_status_default(self):
        registry = SkillRegistry([])
        result = ChatScreen._format_skill_status(registry)
        assert "Skill 状态" in result
        assert "default/default" in result
        assert "available" in result

    def test_format_skill_list_empty(self):
        registry = SkillRegistry([])
        result = ChatScreen._format_skill_list(registry)
        assert "未发现可用 skills" in result

    def test_handle_skill_status(self):
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()
        screen._skill_registry = SkillRegistry([])
        screen._skill_registry.discover()
        screen._handle_skill_command("status")
        msg = screen._chat_area.add_system.call_args[0][0]
        assert "Skill 状态" in msg

    def test_handle_skill_use_and_reset(self):
        workflow = _workflow()
        entry = SkillEntry(
            namespace="review",
            workflow_id="code_review",
            display_name="Code Review",
            description="Review code changes",
            path=MagicMock(),
            workflow=workflow,
        )
        registry = SkillRegistry([])
        registry._entries = [entry]
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()
        screen._skill_registry = registry

        screen._handle_skill_command("use code_review")

        assert screen._current_skill == entry
        assert screen._skill_status == "selected"
        screen._agent.set_session_profile.assert_called_once()
        screen._side_panel.update_skill.assert_called_with(
            "review",
            "code_review",
            "selected",
            available=[("review/code_review", "Code Review", "Review code changes")],
        )

        screen._handle_skill_command("reset")

        assert screen._current_skill is None
        assert screen._skill_status == "idle"
        screen._agent.set_session_profile.assert_called_with(None)
        screen._side_panel.update_skill.assert_called_with(
            "default",
            "default",
            "idle",
            available=[("review/code_review", "Code Review", "Review code changes")],
        )

    def test_handle_skill_use_missing_fragment_sets_error(self):
        workflow = _workflow()
        workflow.prompt_profile.fragments = ["missing_fragment"]
        entry = SkillEntry(
            namespace="review",
            workflow_id="code_review",
            display_name="Code Review",
            description="Review code changes",
            path=MagicMock(),
            workflow=workflow,
        )
        registry = SkillRegistry([])
        registry._entries = [entry]
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()
        screen._skill_registry = registry

        screen._handle_skill_command("use code_review")

        assert screen._current_skill is None
        assert screen._skill_status == "error"
        screen._agent.set_session_profile.assert_not_called()
        screen._side_panel.update_skill.assert_called_with(
            "default",
            "default",
            "error",
            available=[("review/code_review", "Code Review", "Review code changes")],
        )
        assert "Prompt fragment not found" in screen._chat_area.add_system.call_args[0][0]

    def test_handle_skill_use_ambiguous_sets_error(self):
        first = SkillEntry("a", "code_review", "Code Review", "", MagicMock(), _workflow())
        second = SkillEntry("b", "code_review", "Code Review", "", MagicMock(), _workflow())
        registry = SkillRegistry([])
        registry._entries = [first, second]
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()
        screen._skill_registry = registry

        screen._handle_skill_command("use code_review")

        assert screen._skill_status == "error"
        assert "Ambiguous skill id" in screen._chat_area.add_system.call_args[0][0]
        screen._side_panel.update_skill.assert_called_with(
            "default",
            "default",
            "error",
            available=[("a/code_review", "Code Review", ""), ("b/code_review", "Code Review", "")],
        )

    def test_handle_skill_use_duplicate_sets_error(self):
        entry = SkillEntry("review", "code_review", "Code Review", "", MagicMock(), _workflow())
        registry = SkillRegistry([])
        registry._entries = [entry]
        registry.duplicate_ids = {"review/code_review": [MagicMock(), MagicMock()]}
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()
        screen._skill_registry = registry

        screen._handle_skill_command("use review/code_review")

        assert screen._skill_status == "error"
        assert "Duplicate skill id" in screen._chat_area.add_system.call_args[0][0]

    def test_handle_skill_list_refreshes_registry(self):
        registry = MagicMock()
        registry.list.return_value = []
        registry.errors = []
        registry.roots = []
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()
        screen._skill_registry = registry

        screen._handle_skill_command("list")

        registry.discover.assert_called_once()
        assert "未发现可用 skills" in screen._chat_area.add_system.call_args[0][0]

    def test_handle_skill_list_error_updates_panel(self):
        registry = MagicMock()
        registry.list.return_value = []
        registry.errors = ["bad workflow"]
        registry.roots = []
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()
        screen._skill_registry = registry

        screen._handle_skill_command("list")

        assert screen._skill_status == "error"
        screen._side_panel.update_skill.assert_called_with("default", "default", "error", available=[])

    def test_handle_skill_validate_success(self):
        registry = MagicMock()
        registry.errors = []
        registry.validate.return_value = []
        registry.list.return_value = [MagicMock()]
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()
        screen._skill_registry = registry

        screen._handle_skill_command("validate code_review")

        registry.discover.assert_called_once()
        registry.validate.assert_called_once_with("code_review")
        assert screen._skill_status == "idle"
        assert "validation passed" in screen._chat_area.add_system.call_args[0][0]

    def test_handle_skill_validate_failure_updates_panel(self):
        registry = MagicMock()
        registry.errors = []
        registry.validate.return_value = ["review/code_review: Prompt template not found"]
        registry.list.return_value = []
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()
        screen._skill_registry = registry

        screen._handle_skill_command("validate")

        registry.discover.assert_called_once()
        registry.validate.assert_called_once_with(None)
        assert screen._skill_status == "error"
        screen._side_panel.update_skill.assert_called_with("default", "default", "error", available=[])
        assert "validation failed" in screen._chat_area.add_system.call_args[0][0]

    def test_handle_skill_use_delegates_to_skill_service(self):
        workflow = _workflow()
        entry = SkillEntry("review", "code_review", "Code Review", "Review code changes", MagicMock(), workflow)
        registry = SkillRegistry([])
        registry._entries = [entry]
        service = MagicMock()
        service.registry = registry
        service.current = None
        def use_skill(_target):
            service.current = entry
            return entry
        service.use.side_effect = use_skill
        service.snapshot.return_value.status = "selected"
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, skill_service=service)
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()
        screen._skill_registry = registry

        screen._handle_skill_command("use code_review")

        service.use.assert_called_once_with("code_review")
        assert screen._current_skill == entry
        assert screen._skill_status == "selected"

    def test_handle_skill_disable_delegates_to_capability_runtime(self):
        runtime = MagicMock()
        runtime.disable.return_value = {"skills": "reloaded 1 skills"}
        screen = ChatScreen(
            agent=MagicMock(),
            memory=None,
            version=None,
            capability_runtime=runtime,
        )
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()
        screen._skill_registry = SkillRegistry([])

        screen._handle_skill_command("disable default/docx")

        runtime.disable.assert_called_once_with("skills", "default/docx")
        assert "Skill disable" in screen._chat_area.add_system.call_args[0][0]

    def test_handle_skill_use_default_persists_config(self, temp_agentnexus_home):
        workflow = _workflow()
        entry = SkillEntry("review", "code_review", "Code Review", "Review code changes", MagicMock(), workflow)
        registry = SkillRegistry([])
        registry._entries = [entry]
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()
        screen._skill_registry = registry

        screen._handle_skill_command("use code_review --default")

        import yaml

        data = yaml.safe_load((temp_agentnexus_home / "config.yaml").read_text(encoding="utf-8"))
        assert data["default_skill"] == "review/code_review"
        assert screen._current_skill == entry

    def test_handle_skill_default_reset_clears_config(self, temp_agentnexus_home):
        (temp_agentnexus_home / "config.yaml").write_text("default_skill: review/code_review\n", encoding="utf-8")
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()
        screen._skill_registry = SkillRegistry([])

        screen._handle_skill_command("default reset")

        import yaml

        data = yaml.safe_load((temp_agentnexus_home / "config.yaml").read_text(encoding="utf-8")) or {}
        assert "default_skill" not in data
        assert screen._current_skill is None

    def test_init_skill_registry_uses_skill_service(self):
        service = MagicMock()
        service.registry = SkillRegistry([])
        service.current = None
        service.snapshot.return_value.status = "idle"
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, skill_service=service)
        screen._init_skill_registry()
        assert screen._skill_registry is service.registry
        assert screen._skill_status == "idle"

    def test_refresh_skill_panel_syncs_service_current(self):
        workflow = _workflow()
        entry = SkillEntry("review", "code_review", "Code Review", "", MagicMock(), workflow)
        service = MagicMock()
        service.current = entry
        service.snapshot.return_value.status = "selected"
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, skill_service=service)
        screen._side_panel = MagicMock()

        screen._refresh_skill_panel()

        screen._side_panel.update_skill.assert_called_with("review", "code_review", "selected", available=[])

    def test_prepare_agent_question_applies_workflow_runtime(self):
        workflow = _workflow()
        workflow.steps[0].prompt = "Inspect workflow."
        entry = SkillEntry(
            namespace="review",
            workflow_id="code_review",
            display_name="Code Review",
            description="Review code changes",
            path=MagicMock(),
            workflow=workflow,
        )
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._current_skill = entry
        screen._side_panel = MagicMock()

        question = screen._prepare_agent_question("user question")

        assert "Workflow Runtime Context" in question
        assert "Inspect workflow." in question
        assert question.endswith("== User Question ==\nuser question")
        assert screen._side_panel.add_timeline_event.called

    def test_prepare_agent_question_uses_skill_service_runtime(self):
        workflow = _workflow()
        entry = SkillEntry("review", "code_review", "Code Review", "", MagicMock(), workflow)
        registry = SkillRegistry([])
        registry._entries = [entry]
        from agentnexus.services.skill import SkillService

        service = SkillService(registry, agent=MagicMock())
        service.current = entry
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, skill_service=service)
        screen._current_skill = entry
        screen._side_panel = MagicMock()

        question = screen._prepare_agent_question("user question")

        assert "Workflow Runtime Context" in question
        assert service.snapshot().last_run_status == "completed"
        screen._side_panel.update_skill.assert_called_with(
            "review",
            "code_review",
            "idle",
            runtime={
                "status": "completed",
                "steps": 1,
                "ok": 1,
                "errors": 0,
                "skipped": 0,
                "scripts": 0,
                "references": 1,
                "assets": 0,
                "auto_reason": "",
                "auto_source": "",
            },
            available=[("review/code_review", "Code Review", "")],
        )

    def test_prepare_agent_question_auto_selects_skill_service_skill(self):
        workflow = Workflow.model_validate({
            "id": "draft-writer",
            "version": "1",
            "display_name": "Draft Writer",
            "description": "Write concise release notes and drafts.",
            "prompt_profile": {"system": "react"},
            "tool_policy": {"max_risk": "low"},
            "steps": [{"type": "prompt", "id": "draft", "prompt": "Draft concise release notes."}],
            "success_criteria": ["Done."],
        })
        entry = SkillEntry("default", "draft-writer", "Draft Writer", workflow.description, MagicMock(), workflow)
        registry = SkillRegistry([])
        registry._entries = [entry]

        from agentnexus.services.skill import SkillService

        service = SkillService(registry, agent=MagicMock())
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, skill_service=service)
        screen._side_panel = MagicMock()

        question = screen._prepare_agent_question("Please write concise release notes.")

        assert service.current == entry
        assert "Draft concise release notes." in question
        assert screen._side_panel.add_timeline_event.called

    def test_handle_dynamic_skill_command_runs_instruction(self):
        workflow = Workflow.model_validate({
            "id": "docx",
            "version": "1",
            "display_name": "DOCX",
            "description": "Create and edit Word documents.",
            "prompt_profile": {"system": "react"},
            "tool_policy": {"max_risk": "low"},
            "steps": [{"type": "prompt", "id": "doc", "prompt": "Use DOCX skill."}],
            "success_criteria": ["Done."],
        })
        entry = SkillEntry("default", "docx", "DOCX", workflow.description, MagicMock(), workflow)
        registry = SkillRegistry([])
        registry._entries = [entry]
        service = MagicMock()
        service.registry = registry
        service.current = None
        service.use.return_value = entry
        service.snapshot.return_value.status = "selected"
        service.snapshot.return_value.available_skills = (("default/docx", "DOCX", workflow.description),)
        service.available_skill_context.return_value = "== Available Skills ==\n- default/docx: DOCX\n\n"
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, skill_service=service)
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()
        screen._skill_registry = registry
        screen._run_agent = MagicMock()

        handled = screen._handle_dynamic_skill_command("/docx-skill", "生成一份word文档")

        assert handled is True
        service.use.assert_called_once_with("default/docx")
        screen._run_agent.assert_called_once_with("生成一份word文档")

    def test_handle_short_dynamic_skill_command_runs_instruction(self):
        workflow = Workflow.model_validate({
            "id": "pdf",
            "version": "1",
            "display_name": "PDF",
            "description": "Work with PDF documents.",
            "prompt_profile": {"system": "react"},
            "tool_policy": {"max_risk": "low"},
            "steps": [{"type": "prompt", "id": "pdf", "prompt": "Use PDF skill."}],
            "success_criteria": ["Done."],
        })
        entry = SkillEntry("default", "pdf", "PDF", workflow.description, MagicMock(), workflow)
        registry = SkillRegistry([])
        registry._entries = [entry]
        service = MagicMock()
        service.registry = registry
        service.current = None
        service.use.return_value = entry
        service.snapshot.return_value.status = "selected"
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None, skill_service=service)
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()
        screen._skill_registry = registry
        screen._run_agent = MagicMock()

        handled = screen._handle_dynamic_skill_command("/pdf", "提取表格", short_form=True)

        assert handled is True
        service.use.assert_called_once_with("default/pdf")
        screen._run_agent.assert_called_once_with("提取表格")

    def test_handle_dynamic_skill_command_without_instruction_shows_usage(self):
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._chat_area = MagicMock()

        handled = screen._handle_dynamic_skill_command("/docx-skill", "")

        assert handled is True
        assert "用法" in screen._chat_area.add_system.call_args[0][0]

    def test_command_suggestions_include_matching_skills(self):
        entries = []
        for skill_id, display_name in (("docx", "DOCX"), ("pdf", "PDF")):
            workflow = Workflow.model_validate({
                "id": skill_id,
                "version": "1",
                "display_name": display_name,
                "description": f"Use {display_name}.",
                "prompt_profile": {"system": "react"},
                "tool_policy": {"max_risk": "low"},
                "steps": [{"type": "prompt", "id": skill_id, "prompt": f"Use {display_name}."}],
                "success_criteria": ["Done."],
            })
            entries.append(SkillEntry("default", skill_id, display_name, workflow.description, MagicMock(), workflow))
        registry = SkillRegistry([])
        registry._entries = entries
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._skill_registry = registry

        suggestions = screen._match_command_suggestions("/d")

        assert "\n" in suggestions
        assert "/docx" in suggestions
        assert "/pdf" in suggestions

    def test_command_suggestions_are_not_truncated(self):
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        screen._command_catalog = MagicMock(
            return_value=[
                (f"/demo-{index:02d}", f"Demo {index}", ())
                for index in range(25)
            ]
        )

        suggestions = screen._match_command_suggestions("/demo")

        assert "/demo-00" in suggestions
        assert "/demo-24" in suggestions

    def test_update_command_suggestions_updates_scroll_container(self):
        screen = ChatScreen(agent=MagicMock(), memory=None, version=None)
        palette = MagicMock()
        content = MagicMock()

        def query_one(selector, _type=None):
            return palette if selector == "#command-palette" else content

        screen.query_one = query_one
        screen._match_command_suggestions = MagicMock(return_value="[bold]Commands[/]\n/demo")

        screen._update_command_suggestions("/demo")

        content.update.assert_called_once_with("[bold]Commands[/]\n/demo")
        assert palette.styles.display == "block"
        palette.scroll_home.assert_called_once_with(animate=False)

class TestPluginCommands:
    def test_handle_plugin_enable_uses_capability_runtime(self):
        runtime = MagicMock()
        runtime.enable.return_value = {"plugins": "reloaded 1 plugin providers"}
        screen = ChatScreen(
            agent=MagicMock(),
            memory=None,
            version=None,
            capability_runtime=runtime,
        )
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()

        screen._handle_plugin_command("enable demo")

        runtime.enable.assert_called_once_with("plugins", "demo")
        assert "Plugin enabled" in screen._chat_area.add_system.call_args[0][0]


class TestExitAndInterruptCommands:
    def test_double_escape_interrupts_agent(self):
        agent = MagicMock()
        screen = ChatScreen(agent=agent, memory=None, version=None)
        screen._chat_area = MagicMock()
        screen._side_panel = MagicMock()
        screen._stop_spinner = MagicMock()
        screen.action_focus_input = MagicMock()
        screen._last_escape_at = 100.0
        screen._current_run_id = "run_1"
        screen._chat_service = MagicMock()
        screen._chat_service.get_run_snapshot.return_value = MagicMock(
            answer="interrupted",
            question="question",
        )

        event = MagicMock(spec=Key)
        event.key = "escape"

        with patch("agentnexus.tui.screens.chat.time.monotonic", return_value=100.2):
            screen.on_key(event)

        screen._chat_service.cancel_run.assert_called_once_with(
            screen._current_run_id,
            reason="用户双击 ESC 强制中断",
        )
        screen._stop_spinner.assert_called_once()
        screen.action_focus_input.assert_not_called()
        event.stop.assert_called_once()

    def test_tui_no_longer_owns_turn_persistence(self):
        screen = ChatScreen(agent=MagicMock(), memory=MagicMock(), version=MagicMock())

        assert not hasattr(screen, "_turn_journal")
        assert not hasattr(screen, "_persist_interrupted_turn")
        assert not hasattr(screen, "_build_interrupted_answer")

    def test_tui_delegates_journal_recording_to_chat_service(self):
        run_source = inspect.getsource(ChatScreen._run_agent.__wrapped__)
        prepare_source = inspect.getsource(ChatScreen._prepare_agent_question)

        assert "record_agent_event" in run_source
        assert "record_workflow_event" in prepare_source
        assert "turn.record(" not in run_source
        assert "turn.record(" not in prepare_source
