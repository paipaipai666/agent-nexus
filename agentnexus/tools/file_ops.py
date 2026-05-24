"""File operation tools — read, write, list with path sandbox security."""

from __future__ import annotations

import fnmatch
import hashlib
import os
from datetime import datetime
from pathlib import Path


def _resolve_safe(path: str) -> Path:
    """Resolve path and verify it stays within the workspace sandbox.

    The workspace root is the directory where nexus was launched (os.getcwd()).
    Raises ValueError if the resolved path escapes the sandbox.
    """
    workspace = Path(os.getcwd()).absolute()
    candidate = workspace / path

    def ensure_within_workspace(resolved: Path) -> None:
        try:
            resolved.relative_to(workspace)
        except ValueError:
            raise ValueError(
                f"路径越界: '{path}' 解析为 '{resolved}'，超出工作目录 '{workspace}'。"
                " 不允许通过 ../ 访问上级目录。"
            )

    existing_ancestor = candidate
    while not existing_ancestor.exists() and existing_ancestor != existing_ancestor.parent:
        existing_ancestor = existing_ancestor.parent

    ensure_within_workspace(Path(existing_ancestor.resolve()))
    resolved_candidate = Path(candidate.resolve())
    ensure_within_workspace(resolved_candidate)
    return resolved_candidate



def _fingerprint_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return "missing"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()



def file_read(path: str, offset: int = 0, limit: int | None = None) -> str:
    """Read file content with line numbers. Large files are truncated.

    Args:
        path: File path relative to workspace root.
        offset: Starting line number (0-indexed).
        limit: Max lines to read (None = all, up to 1000).
    """
    p = _resolve_safe(path)
    if not p.exists():
        return f"错误: 文件不存在: {path}"
    if not p.is_file():
        return f"错误: 路径不是文件: {path}"
    if p.stat().st_size > 10 * 1024 * 1024:
        return f"错误: 文件过大 ({p.stat().st_size / 1024 / 1024:.1f}MB)，超过 10MB 读取上限"

    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return f"错误: 无法读取文件: {e}"

    total = len(lines)
    max_lines = min(limit or 1000, 1000)
    sliced = lines[offset:offset + max_lines]
    version = _fingerprint_file(p)

    parts = [f"[文件] {path} ({total} 行, 共 {p.stat().st_size:,} 字节, version={version})"]
    for i, line in enumerate(sliced, start=offset + 1):
        parts.append(f"{i:>6} | {line.rstrip()}")

    if offset + len(sliced) < total:
        parts.append(f"... (省略 {total - offset - len(sliced)} 行)")

    return "\n".join(parts)



def file_write(path: str, content: str, mode: str = "create", expected_version: str | None = None) -> str:
    """Write content to a file.

    Args:
        path: File path relative to workspace root.
        content: File content to write.
        mode: "create" (fail if exists), "overwrite" (replace if exists, needs HITL),
              "append" (add to end).
        expected_version: Optional file fingerprint captured from an earlier read.
                          If provided and the current on-disk fingerprint differs,
                          the write is rejected to avoid blind overwrites.

    Returns confirmation or error message.
    """
    if mode not in ("create", "overwrite", "append"):
        return f"错误: 不支持的写入模式 '{mode}'，可选: create / overwrite / append"

    p = _resolve_safe(path)
    exists = p.exists()
    current_version = _fingerprint_file(p)

    if expected_version is not None and current_version != expected_version:
        return (
            f"错误: 文件版本冲突: {path}。"
            f" 期望版本={expected_version}，当前版本={current_version}。"
            " 请先重新读取文件再决定是否覆盖。"
        )

    if mode == "create" and exists:
        return f"错误: 文件已存在: {path}。使用 mode='overwrite' 覆盖，或 mode='append' 追加。"
    if mode == "append" and not exists:
        return f"错误: 文件不存在，无法追加: {path}。使用 mode='create' 新建。"

    # Create parent directories
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return f"错误: 无法创建父目录: {e}"

    mode_flag = "w" if mode in ("create", "overwrite") else "a"
    try:
        with open(p, mode_flag, encoding="utf-8") as f:
            f.write(content)
        size = p.stat().st_size
        action = {
            "create": "已创建",
            "overwrite": "已覆盖",
            "append": "已追加",
        }[mode]
        new_version = _fingerprint_file(p)
        return f"[file_write] {action} {path} ({size:,} 字节, version={new_version})"
    except Exception as e:
        return f"错误: 写入失败: {e}"



def file_list(path: str = ".", pattern: str | None = None) -> str:
    """List directory contents.

    Args:
        path: Directory path relative to workspace root (default: current dir).
        pattern: Optional glob pattern filter (e.g., "*.py", "test_*").

    Returns formatted file/directory list with size and modified time.
    """
    p = _resolve_safe(path)
    if not p.exists():
        return f"错误: 目录不存在: {path}"
    if not p.is_dir():
        return f"错误: 路径不是目录: {path}"

    try:
        entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except PermissionError:
        return f"错误: 无权限访问目录: {path}"

    if pattern:
        entries = [e for e in entries if fnmatch.fnmatch(e.name, pattern)]

    if not entries:
        return f"[目录] {path}: (空)"

    parts = [f"[目录] {path} ({len(entries)} 项):"]
    for entry in entries:
        try:
            stat = entry.stat()
            size = stat.st_size
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            size = 0
            mtime = "?"

        if entry.is_dir():
            parts.append(f"  [DIR]  {entry.name}/")
        else:
            size_str = _format_size(size)
            parts.append(f"  [FILE] {entry.name}  ({size_str}, {mtime})")

    return "\n".join(parts)



def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"
