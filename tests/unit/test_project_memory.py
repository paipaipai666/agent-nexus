"""Unit tests for project-level memory (.agentnexus/ plain-text store)."""

from __future__ import annotations

import pytest

from agentnexus.memory.manager import MemoryManager
from agentnexus.memory.project import ProjectMemory


@pytest.fixture()
def pm(tmp_path):
    return ProjectMemory(tmp_path)


class TestLayout:
    def test_layout_created_on_init(self, pm):
        assert pm.root.is_dir()
        assert (pm.root / "worklog").is_dir()
        assert (pm.root / "archive").is_dir()
        for name in ("MEMORY.md", "memo.md", "decisions.md", "lessons.md", "state.md", ".gitignore"):
            assert (pm.root / name).is_file(), name

    def test_gitignore_excludes_local_state(self, pm):
        gitignore = (pm.root / ".gitignore").read_text(encoding="utf-8")
        assert "worklog/" in gitignore
        assert "archive/" in gitignore
        assert "state.md" in gitignore
        assert "memo.md" not in gitignore

    def test_layout_idempotent_preserves_content(self, tmp_path):
        pm1 = ProjectMemory(tmp_path)
        pm1.add_entry("memo", "用 pnpm 而不是 npm")
        pm2 = ProjectMemory(tmp_path)
        assert "pnpm" in (pm2.root / "memo.md").read_text(encoding="utf-8")


class TestAddEntry:
    def test_writes_topic_file_and_index(self, pm):
        assert pm.add_entry("decision", "用 SQLite 因为 WAL 并发") is True
        topic = (pm.root / "decisions.md").read_text(encoding="utf-8")
        assert "用 SQLite 因为 WAL 并发" in topic
        index = (pm.root / "MEMORY.md").read_text(encoding="utf-8")
        assert "- [decision]" in index
        assert "用 SQLite 因为 WAL 并发" in index

    def test_dedup_same_content(self, pm):
        assert pm.add_entry("memo", "构建命令是 just build") is True
        assert pm.add_entry("memo", "构建命令是 just build") is False
        topic = (pm.root / "memo.md").read_text(encoding="utf-8")
        assert topic.count("构建命令是 just build") == 1

    def test_invalid_kind_raises(self, pm):
        with pytest.raises(ValueError, match="unknown entry kind"):
            pm.add_entry("nonsense", "内容")

    def test_multiline_content_flattened(self, pm):
        pm.add_entry("lesson", "第一行\n第二行", tags="db migration")
        topic = (pm.root / "lessons.md").read_text(encoding="utf-8")
        assert "第一行 第二行 #db #migration" in topic

    def test_index_trimmed_at_max_lines(self, pm):
        pm.MAX_INDEX_LINES = 6
        for i in range(10):
            pm.add_entry("memo", f"entry-{i}")
        lines = (pm.root / "MEMORY.md").read_text(encoding="utf-8").splitlines()
        assert len(lines) <= 6
        content = "\n".join(lines)
        assert "# 项目记忆索引" in content
        assert "entry-9" in content
        assert "entry-0" not in content


class TestWorklog:
    def test_log_work_creates_daily_file(self, pm):
        pm.log_work("完成任务", "实现登录接口")
        logs = list((pm.root / "worklog").glob("*.md"))
        assert len(logs) == 1
        text = logs[0].read_text(encoding="utf-8")
        assert "完成任务" in text
        assert "实现登录接口" in text

    def test_old_worklogs_rotated_to_archive(self, pm):
        stale = pm.root / "worklog" / "2020-01-01.md"
        stale.write_text("# 工作日志 2020-01-01\n", encoding="utf-8")
        pm.log_work("触发轮转")
        assert not stale.exists()
        assert (pm.root / "archive" / "2020-01-01.md").is_file()


class TestState:
    def test_update_state_and_format_context(self, pm):
        pm.update_state("正在重构记忆模块，下一步接入 CLI")
        ctx = pm.format_context()
        assert "[项目当前状态]" in ctx
        assert "正在重构记忆模块" in ctx

    def test_read_state_empty_on_fresh_layout(self, pm):
        assert pm.read_state() == ""


class TestFormatContext:
    def test_empty_on_fresh_layout(self, pm):
        assert pm.format_context() == ""

    def test_includes_index_entries(self, pm):
        pm.add_entry("memo", "用 pnpm 而不是 npm")
        ctx = pm.format_context()
        assert "[项目记忆索引]" in ctx
        assert "pnpm" in ctx


class TestManagerIntegration:
    def _mgr_with_project(self, tmp_path):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.session_id = "test"
        mgr.long_term = None
        mgr.project = ProjectMemory(tmp_path)
        return mgr

    def test_init_session_includes_project_context(self, tmp_path):
        mgr = self._mgr_with_project(tmp_path)
        mgr.project.add_entry("memo", "用 pnpm 而不是 npm")
        ctx = mgr.init_session("怎么构建")
        assert "[项目记忆索引]" in ctx
        assert "pnpm" in ctx

    def test_init_session_without_project_unchanged(self):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.session_id = "test"
        mgr.long_term = None
        assert mgr.init_session("问题") == ""

    def test_conclude_logs_work(self, tmp_path):
        mgr = self._mgr_with_project(tmp_path)
        mgr.conclude("实现项目记忆", "已完成", allow_memory=True)
        logs = list((mgr.project.root / "worklog").glob("*.md"))
        assert len(logs) == 1
        assert "实现项目记忆" in logs[0].read_text(encoding="utf-8")

    def test_conclude_skips_worklog_when_not_allowed(self, tmp_path):
        mgr = self._mgr_with_project(tmp_path)
        mgr.conclude("子任务", "完成", allow_memory=False)
        assert list((mgr.project.root / "worklog").glob("*.md")) == []


class TestMemorySaveTool:
    def test_project_scope_writes_memo(self, tmp_path):
        from agentnexus.tools.memory_save import memory_save
        from agentnexus.tools.workspace import current_workspace

        token = current_workspace.set(str(tmp_path))
        try:
            result = memory_save("这个项目用 pnpm 构建", scope="project", kind="memo")
        finally:
            current_workspace.reset(token)
        assert "已保存到项目记忆" in result
        pm = ProjectMemory(tmp_path)
        assert "pnpm" in (pm.root / "memo.md").read_text(encoding="utf-8")

    def test_project_scope_log_kind(self, tmp_path):
        from agentnexus.tools.memory_save import memory_save
        from agentnexus.tools.workspace import current_workspace

        token = current_workspace.set(str(tmp_path))
        try:
            result = memory_save("完成了登录模块联调", scope="project", kind="log")
        finally:
            current_workspace.reset(token)
        assert "[log]" in result
        pm = ProjectMemory(tmp_path)
        logs = list((pm.root / "worklog").glob("*.md"))
        assert len(logs) == 1
        assert "登录模块联调" in logs[0].read_text(encoding="utf-8")

    def test_project_scope_invalid_kind(self, tmp_path):
        from agentnexus.tools.memory_save import memory_save
        from agentnexus.tools.workspace import current_workspace

        token = current_workspace.set(str(tmp_path))
        try:
            result = memory_save("内容足够长的一条记录", scope="project", kind="bogus")
        finally:
            current_workspace.reset(token)
        assert "无效 kind" in result

    def test_invalid_scope(self):
        from agentnexus.tools.memory_save import memory_save

        result = memory_save("内容足够长的一条记录", scope="nowhere")
        assert "无效 scope" in result
