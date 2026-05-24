"""Tests for ChatScreen static methods, MCP formatting, and command helpers."""

import json
from unittest.mock import MagicMock

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
