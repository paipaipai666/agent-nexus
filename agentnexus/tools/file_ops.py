"""File operation tools — read, write, list with path sandbox security."""

from __future__ import annotations

import difflib
import fnmatch
import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any

PREVIEW_MAX_LINES = 120
PREVIEW_MAX_CHARS = 8000
INLINE_PATCH_MAX_LINES = 400
INLINE_PATCH_MAX_CHARS = 32000
DIFF_SOURCE_MAX_BYTES = 512 * 1024


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


def _read_text_if_possible(path: Path) -> tuple[str | None, bool]:
    """Read UTF-8 text when possible.

    Returns:
        (text, is_binary_like)
    """
    if not path.exists() or not path.is_file():
        return None, False
    try:
        return path.read_text(encoding="utf-8"), False
    except UnicodeDecodeError:
        return None, True
    except Exception:
        return None, True


def _build_unified_diff(path: str, before: str, after: str) -> str:
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )


def _count_diff_stats(diff_text: str) -> dict[str, int]:
    added = 0
    removed = 0
    hunks = 0
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            hunks += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {
        "added_lines": added,
        "removed_lines": removed,
        "hunks": hunks,
    }


def _truncate_diff_preview(diff_text: str) -> dict[str, Any]:
    lines = diff_text.splitlines()
    out_lines: list[str] = []
    chars = 0
    shown_hunks = 0
    total_hunks = sum(1 for line in lines if line.startswith("@@"))

    for line in lines:
        if line.startswith("@@"):
            shown_hunks += 1
        next_len = chars + len(line) + 1
        if len(out_lines) >= PREVIEW_MAX_LINES or next_len > PREVIEW_MAX_CHARS:
            break
        out_lines.append(line)
        chars = next_len

    truncated = len(out_lines) < len(lines)
    text = "\n".join(out_lines)
    if truncated:
        remaining_hunks = max(0, total_hunks - shown_hunks)
        suffix = "\n... [diff truncated"
        if remaining_hunks:
            suffix += f", remaining hunks={remaining_hunks}"
        suffix += "]"
        text += suffix

    return {
        "format": "unified_diff",
        "text": text,
        "truncated": truncated,
        "max_lines": PREVIEW_MAX_LINES,
        "max_chars": PREVIEW_MAX_CHARS,
        "shown_hunks": shown_hunks,
        "total_hunks": total_hunks,
    }


def _write_patch_artifact(diff_text: str) -> str:
    artifact_dir = Path(os.getcwd()) / ".agentnexus" / "tool_results"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(datetime.now().timestamp() * 1000)
    patch_path = artifact_dir / f"patch_{timestamp}.diff"
    patch_path.write_text(diff_text, encoding="utf-8")
    return str(patch_path)


def _error_result(path: str, mode: str, message: str, error_code: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": "error",
        "message": message,
        "path": path,
        "mode": mode,
        "changed": False,
        "error_code": error_code,
        **extra,
    }


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



def file_write(path: str, content: str, mode: str = "create", expected_version: str | None = None) -> dict[str, Any]:
    """Write content to a file.

    Args:
        path: File path relative to workspace root.
        content: File content to write.
        mode: "create" (fail if exists), "overwrite" (replace if exists, needs HITL),
              "append" (add to end).
        expected_version: Optional file fingerprint captured from an earlier read.
                          If provided and the current on-disk fingerprint differs,
                          the write is rejected to avoid blind overwrites.

    Returns structured result with compact diff preview.
    """
    if mode not in ("create", "overwrite", "append"):
        return _error_result(
            path, mode,
            f"错误: 不支持的写入模式 '{mode}'，可选: create / overwrite / append",
            "invalid_mode",
        )

    p = _resolve_safe(path)
    exists = p.exists()
    current_version = _fingerprint_file(p)

    if expected_version is not None and current_version != expected_version:
        return _error_result(
            path, mode,
            (
                f"错误: 文件版本冲突: {path}。"
                f" 期望版本={expected_version}，当前版本={current_version}。"
                " 请先重新读取文件再决定是否覆盖。"
            ),
            "version_conflict",
            version_before=current_version,
        )

    if mode == "create" and exists:
        return _error_result(
            path, mode,
            f"错误: 文件已存在: {path}。使用 mode='overwrite' 覆盖，或 mode='append' 追加。",
            "file_exists",
        )
    if mode == "append" and not exists:
        return _error_result(
            path, mode,
            f"错误: 文件不存在，无法追加: {path}。使用 mode='create' 新建。",
            "file_missing",
        )

    before_bytes = p.stat().st_size if exists else 0
    before_text = None
    before_binary = False
    if exists:
        before_text, before_binary = _read_text_if_possible(p)

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return _error_result(path, mode, f"错误: 无法创建父目录: {e}", "mkdir_failed")

    mode_flag = "w" if mode in ("create", "overwrite") else "a"
    try:
        with open(p, mode_flag, encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        return _error_result(path, mode, f"错误: 写入失败: {e}", "write_failed")

    after_bytes = p.stat().st_size
    new_version = _fingerprint_file(p)
    after_text, after_binary = _read_text_if_possible(p)
    is_binary = before_binary or after_binary

    if mode == "create" and not exists:
        change_type = "added"
    elif mode == "append":
        change_type = "appended"
    else:
        change_type = "modified"

    notes: list[str] = []
    patch = None
    patch_ref = None

    if is_binary:
        stats = {
            "added_lines": 0,
            "removed_lines": 0,
            "hunks": 0,
            "before_bytes": before_bytes,
            "after_bytes": after_bytes,
        }
        preview = {
            "format": "summary",
            "text": f"Binary file changed: {path} ({before_bytes} -> {after_bytes} bytes)",
            "truncated": False,
        }
        notes.append("Binary file diff preview is not available.")
    elif max(before_bytes, after_bytes) > DIFF_SOURCE_MAX_BYTES:
        stats = {
            "added_lines": 0,
            "removed_lines": 0,
            "hunks": 0,
            "before_bytes": before_bytes,
            "after_bytes": after_bytes,
        }
        preview = {
            "format": "summary",
            "text": f"Diff preview omitted for large file: {path} ({before_bytes} -> {after_bytes} bytes)",
            "truncated": False,
        }
        notes.append("Full diff omitted because file exceeds preview source size threshold.")
    else:
        diff_text = _build_unified_diff(path, before_text or "", after_text or "")
        stats = {
            **_count_diff_stats(diff_text),
            "before_bytes": before_bytes,
            "after_bytes": after_bytes,
        }
        preview = _truncate_diff_preview(diff_text)
        if len(diff_text) <= INLINE_PATCH_MAX_CHARS and len(diff_text.splitlines()) <= INLINE_PATCH_MAX_LINES:
            patch = diff_text
        else:
            patch_ref = _write_patch_artifact(diff_text)
            notes.append("Full diff stored externally; use patch_ref to inspect complete patch.")

    action = {"create": "已创建", "overwrite": "已覆盖", "append": "已追加"}[mode]
    message = (
        f"[file_write] {action} {path} "
        f"(+{stats['added_lines']}/-{stats['removed_lines']}, version={new_version})"
    )

    return {
        "status": "ok",
        "message": message,
        "path": path,
        "mode": mode,
        "change_type": change_type,
        "changed": True,
        "version_before": current_version,
        "version_after": new_version,
        "stats": stats,
        "preview": preview,
        "patch": patch,
        "patch_ref": patch_ref,
        "is_binary": is_binary,
        "notes": notes,
    }



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
