"""Project-level memory — plain-text files under ``<workspace>/.agentnexus/``.

Layout (created on first use)::

    .agentnexus/
    ├── MEMORY.md       # 索引：一行一条，会话开始时唯一全量加载的文件
    ├── memo.md         # 项目备忘录：不可猜的命令、约定、landmines
    ├── decisions.md    # 决策记录：决策 + 理由（append-only，几乎不删）
    ├── lessons.md      # 踩坑记录：一行一坑，带 #tag 便于 grep
    ├── state.md        # 当前状态：进行中任务/下一步（压缩时重写）
    ├── worklog/        # 工作日志：append-only，按日期分文件
    └── archive/        # 超期 worklog 轮转到这里

memo/decisions/lessons/MEMORY.md 适合提交进 git（团队共享）；worklog/、
archive/、state.md 是操作者本地状态，由自动生成的 .gitignore 排除。
"""

from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_INDEX_TEMPLATE = """\
# 项目记忆索引

<!-- 一行一条：[kind] YYYY-MM-DD 内容。会话开始只加载本文件；详情见 memo.md / decisions.md / lessons.md。 -->
"""

_MEMO_TEMPLATE = """\
# 项目备忘录

<!-- 不可猜的命令、约定、landmines。只写从代码里看不出来的东西。 -->
"""

_DECISIONS_TEMPLATE = """\
# 决策记录

<!-- 决策 + 理由 + 被否方案。append-only，几乎不删。 -->
"""

_LESSONS_TEMPLATE = """\
# 踩坑记录

<!-- 一行一坑，带 #tag 便于 grep。 -->
"""

_STATE_TEMPLATE = """\
# 当前状态

（暂无）
"""

_GITIGNORE = """\
# Per-operator state — not committed to git
worklog/
archive/
state.md
"""


class ProjectMemory:
    """Plain-text project memory rooted at ``<workspace>/.agentnexus/``."""

    DIR_NAME = ".agentnexus"
    INDEX_NAME = "MEMORY.md"
    KIND_FILES = {"memo": "memo.md", "decision": "decisions.md", "lesson": "lessons.md"}
    KIND_LABELS = {"memo": "备忘", "decision": "决策", "lesson": "踩坑"}
    MAX_INDEX_LINES = 200
    MAX_CONTEXT_CHARS = 8000
    MAX_STATE_CHARS = 2000
    WORKLOG_RETENTION_DAYS = 14

    def __init__(self, workspace_path: str | Path):
        self.workspace = Path(workspace_path)
        self.root = self.workspace / self.DIR_NAME
        self._lock = threading.Lock()
        self.ensure_layout()

    # ── layout ───────────────────────────────────────────────────────

    def ensure_layout(self) -> None:
        """Create the .agentnexus/ tree if missing. Never overwrites content."""
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "worklog").mkdir(exist_ok=True)
        (self.root / "archive").mkdir(exist_ok=True)
        for name, template in (
            (self.INDEX_NAME, _INDEX_TEMPLATE),
            ("memo.md", _MEMO_TEMPLATE),
            ("decisions.md", _DECISIONS_TEMPLATE),
            ("lessons.md", _LESSONS_TEMPLATE),
            ("state.md", _STATE_TEMPLATE),
            (".gitignore", _GITIGNORE),
        ):
            fpath = self.root / name
            if not fpath.exists():
                fpath.write_text(template, encoding="utf-8")

    # ── reads ────────────────────────────────────────────────────────

    def read_index(self) -> str:
        """Return the MEMORY.md index (capped), or '' if unreadable."""
        try:
            text = (self.root / self.INDEX_NAME).read_text(encoding="utf-8")
        except OSError:
            return ""
        if len(text) > self.MAX_CONTEXT_CHARS:
            text = text[: self.MAX_CONTEXT_CHARS] + "\n... (索引过长已截断)\n"
        return text.strip()

    def read_state(self) -> str:
        """Return the state.md body, or '' if empty/placeholder."""
        try:
            text = (self.root / "state.md").read_text(encoding="utf-8")
        except OSError:
            return ""
        body = re.sub(r"^#.*$", "", text, flags=re.MULTILINE).strip()
        if not body or body == "（暂无）":
            return ""
        return body[: self.MAX_STATE_CHARS]

    def format_context(self) -> str:
        """Build the prompt-injection block: index + current state.

        Returns '' when there is nothing worth injecting (fresh layout with
        no entries and no state), so callers can omit the block entirely.
        """
        parts: list[str] = []
        index = self.read_index()
        # An index with no entry lines carries no information.
        has_entries = any(line.startswith("- [") for line in index.splitlines())
        if has_entries:
            parts.append(f"[项目记忆索引] 本项目相关约定以此为准（优先级高于全局记忆），详情见 .agentnexus/ 目录\n{index}")
        state = self.read_state()
        if state:
            parts.append(f"[项目当前状态]\n{state}")
        return "\n\n".join(parts)

    # ── writes ───────────────────────────────────────────────────────

    def add_entry(self, kind: str, content: str, tags: str = "") -> bool:
        """Append a one-line entry to a topic file + index. Returns False on duplicate.

        kind: memo | decision | lesson. Content is flattened to a single line.
        """
        if kind not in self.KIND_FILES:
            raise ValueError(f"unknown entry kind: {kind!r} (valid: {sorted(self.KIND_FILES)})")
        line_content = " ".join(content.split()).strip()
        if len(line_content) < 2:
            return False
        date = datetime.now().strftime("%Y-%m-%d")
        tag_suffix = ""
        if tags:
            normalized = " ".join(
                t if t.startswith("#") else f"#{t}" for t in tags.split()
            )
            tag_suffix = f" {normalized}"

        with self._lock:
            topic_path = self.root / self.KIND_FILES[kind]
            existing = topic_path.read_text(encoding="utf-8") if topic_path.exists() else ""
            if line_content in existing:
                return False
            with open(topic_path, "a", encoding="utf-8") as f:
                f.write(f"- {date} {line_content}{tag_suffix}\n")
            self._append_index(kind, date, line_content)
        return True

    def log_work(self, event: str, details: str = "") -> None:
        """Append a timestamped event to today's worklog; rotate old logs."""
        event_line = " ".join(event.split()).strip()
        if not event_line:
            return
        now = datetime.now()
        date = now.strftime("%Y-%m-%d")
        details = " ".join(details.split()).strip()
        suffix = f" — {details}" if details else ""
        with self._lock:
            log_path = self.root / "worklog" / f"{date}.md"
            if not log_path.exists():
                log_path.write_text(f"# 工作日志 {date}\n\n", encoding="utf-8")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"- {now.strftime('%H:%M')} {event_line}{suffix}\n")
            self._rotate_worklogs(now)

    def update_state(self, summary: str) -> None:
        """Rewrite state.md with the current focus (called on compaction)."""
        summary = summary.strip()
        if not summary:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        text = f"# 当前状态\n\n更新于 {now}\n\n{summary}\n"
        with self._lock:
            state_path = self.root / "state.md"
            tmp_path = state_path.with_suffix(".md.tmp")
            tmp_path.write_text(text, encoding="utf-8")
            os.replace(tmp_path, state_path)

    # ── internals ────────────────────────────────────────────────────

    def _append_index(self, kind: str, date: str, content: str) -> None:
        index_path = self.root / self.INDEX_NAME
        entry = f"- [{kind}] {date} {content[:100]}"
        try:
            lines = index_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = _INDEX_TEMPLATE.splitlines()
        lines.append(entry)
        if len(lines) > self.MAX_INDEX_LINES:
            # Keep the header block (non-entry lines at the top), trim oldest entries.
            header_end = 0
            for i, line in enumerate(lines):
                if line.startswith("- ["):
                    header_end = i
                    break
            else:
                header_end = len(lines)
            keep = self.MAX_INDEX_LINES - header_end
            lines = lines[:header_end] + lines[-keep:]
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _rotate_worklogs(self, now: datetime) -> None:
        """Move worklogs older than WORKLOG_RETENTION_DAYS into archive/."""
        worklog_dir = self.root / "worklog"
        archive_dir = self.root / "archive"
        for f in worklog_dir.glob("*.md"):
            try:
                log_date = datetime.strptime(f.stem, "%Y-%m-%d")
            except ValueError:
                continue
            if (now - log_date).days > self.WORKLOG_RETENTION_DAYS:
                try:
                    f.rename(archive_dir / f.name)
                except OSError as e:
                    logger.debug("Worklog rotation failed for %s: %s", f, e)
