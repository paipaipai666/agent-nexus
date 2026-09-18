"""Behavioral tests for runtime context blocks.

Covers:
- build_environment_block: OS / cwd / terminal / local time to the minute
- load_project_instructions: AGENTS.md hierarchy (root→cwd, deeper wins),
  user-global file, precedence ordering, size budgets, mtime cache
- build_react_messages: tools-desc suppression for native strategies,
  context block ordering (env early, user blocks last)
"""

from __future__ import annotations

import re
from datetime import datetime

from agentnexus.agents import runtime_context
from agentnexus.agents.prompt_builder import build_react_messages

FIXED_NOW = datetime(2026, 9, 18, 21, 47, 36, 123000).astimezone()


class TestEnvironmentBlock:
    def test_contains_all_fields(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TERM_PROGRAM", "vscode")
        block = runtime_context.build_environment_block(cwd=tmp_path, now=FIXED_NOW)
        assert "== 运行环境 ==" in block
        assert f"工作目录: {tmp_path}" in block
        assert "终端: vscode" in block
        assert "操作系统:" in block

    def test_time_precise_to_minute(self, tmp_path):
        block = runtime_context.build_environment_block(cwd=tmp_path, now=FIXED_NOW)
        m = re.search(r"当前时间: (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", block)
        assert m, f"minute-precision timestamp missing: {block}"
        rendered = m.group(1)
        assert rendered == FIXED_NOW.strftime("%Y-%m-%d %H:%M")
        # seconds must not leak into the rendered stamp
        assert rendered[17:19] != str(FIXED_NOW.second).zfill(2) or FIXED_NOW.second < 10
        assert len(rendered) == 16

    def test_terminal_fallback_to_unknown(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        block = runtime_context.build_environment_block(cwd=tmp_path, now=FIXED_NOW)
        assert "终端: unknown" in block


class TestProjectInstructions:
    def test_empty_when_no_files(self, tmp_path):
        result = runtime_context.load_project_instructions(
            cwd=tmp_path, global_path=tmp_path / "missing.md"
        )
        assert result == ""

    def test_hierarchy_root_to_cwd_with_precedence_note(self, tmp_path):
        root = tmp_path / "root"
        mid = root / "mid"
        cwd = mid / "cwd"
        cwd.mkdir(parents=True)
        (root / "AGENTS.md").write_text("ROOT RULES", encoding="utf-8")
        (mid / "AGENTS.md").write_text("MID RULES", encoding="utf-8")
        (cwd / "AGENTS.md").write_text("CWD RULES", encoding="utf-8")

        result = runtime_context.load_project_instructions(
            cwd=cwd, global_path=tmp_path / "missing.md"
        )
        assert "ROOT RULES" in result and "MID RULES" in result and "CWD RULES" in result
        # deeper file rendered later = higher precedence
        assert result.index("ROOT RULES") < result.index("MID RULES") < result.index("CWD RULES")
        # precedence semantics stated for the model
        assert "更接近工作目录" in result
        assert "安全约束永远优先" in result
        assert len(re.findall(r"<project_instructions", result)) == 3

    def test_global_file_ranks_lowest(self, tmp_path):
        cwd = tmp_path / "proj"
        cwd.mkdir()
        (cwd / "AGENTS.md").write_text("PROJECT", encoding="utf-8")
        global_file = tmp_path / "AGENTS.md"
        global_file.write_text("GLOBAL", encoding="utf-8")

        result = runtime_context.load_project_instructions(cwd=cwd, global_path=global_file)
        assert result.index("GLOBAL") < result.index("PROJECT")

    def test_duplicate_global_and_project_file_rendered_once(self, tmp_path):
        cwd = tmp_path / "proj"
        cwd.mkdir()
        agents = cwd / "AGENTS.md"
        agents.write_text("SAME FILE", encoding="utf-8")
        result = runtime_context.load_project_instructions(cwd=cwd, global_path=agents)
        assert result.count("SAME FILE") == 1

    def test_edits_picked_up_immediately(self, tmp_path):
        cwd = tmp_path / "proj"
        cwd.mkdir()
        agents = cwd / "AGENTS.md"
        agents.write_text("v1", encoding="utf-8")
        first = runtime_context.load_project_instructions(cwd=cwd, global_path=tmp_path / "x.md")
        assert "v1" in first
        agents.write_text("v2", encoding="utf-8")
        second = runtime_context.load_project_instructions(cwd=cwd, global_path=tmp_path / "x.md")
        assert "v2" in second and "v1" not in second

    def test_per_file_truncation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runtime_context, "_MAX_FILE_CHARS", 100)
        cwd = tmp_path / "proj"
        cwd.mkdir()
        (cwd / "AGENTS.md").write_text("A" * 500, encoding="utf-8")
        result = runtime_context.load_project_instructions(cwd=cwd, global_path=tmp_path / "x.md")
        assert "已截断" in result
        assert "A" * 500 not in result

    def test_total_budget_keeps_high_precedence_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runtime_context, "_MAX_TOTAL_CHARS", 900)
        monkeypatch.setattr(runtime_context, "_MAX_FILE_CHARS", 400)
        root = tmp_path / "root"
        cwd = root / "cwd"
        cwd.mkdir(parents=True)
        (root / "AGENTS.md").write_text("R" * 400, encoding="utf-8")
        (cwd / "AGENTS.md").write_text("C" * 400, encoding="utf-8")
        result = runtime_context.load_project_instructions(cwd=cwd, global_path=tmp_path / "x.md")
        # the deeper (higher-precedence) file survives the budget cut
        assert "C" * 400 in result
        assert "R" * 400 not in result
        assert "被省略" in result


class TestMessagesContextOrdering:
    def test_tools_desc_suppressed_for_native(self):
        messages = build_react_messages(
            system_rules="rules",
            tools_desc="tool_a",
            question="q",
            include_tools_desc=False,
        )
        assert all("== 可用工具 ==" not in m["content"] for m in messages)

    def test_tools_desc_present_for_json_strategies(self):
        messages = build_react_messages(
            system_rules="rules",
            tools_desc="tool_a",
            question="q",
            include_tools_desc=True,
        )
        assert any("== 可用工具 ==" in m["content"] for m in messages)

    def test_user_blocks_rank_last_in_context(self):
        messages = build_react_messages(
            system_rules="rules",
            tools_desc="TOOLS",
            question="q",
            memory_context="MEMORY",
            conversation_context="CONVERSATION",
            environment_context="ENVIRONMENT",
            todo_context="TODO",
            project_instructions="PROJECT_RULES",
            append_system_prompt="USER_APPENDIX",
        )
        contents = [m["content"] for m in messages]
        # group order: rules → memory → conversation → static → tools → volatile → user
        assert contents[0] == "rules"
        assert contents[1] == "MEMORY"
        assert contents[2] == "CONVERSATION"
        static = contents[3]
        assert "PROJECT_RULES" in static and "USER_APPENDIX" in static
        assert static.index("PROJECT_RULES") < static.index("USER_APPENDIX")
        assert contents[4] == "== 可用工具 ==\nTOOLS"
        volatile = contents[5]
        assert volatile.index("ENVIRONMENT") < volatile.index("TODO")
        # user question stays the final message
        assert messages[-1]["role"] == "user"
