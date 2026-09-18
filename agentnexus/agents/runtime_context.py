"""Runtime context blocks: environment info + AGENTS.md project instructions.

Two blocks are assembled here and injected into the system context message:

- ``build_environment_block`` — OS / cwd / terminal / local time (minute
  precision). Rebuilt on every prompt build so long-running sessions never
  serve stale timestamps.
- ``load_project_instructions`` — hierarchical AGENTS.md discovery with
  Codex semantics: user-global file first, then root-to-cwd chain, deeper
  files overriding shallower ones on conflict.

The workspace anchor is ``get_effective_workspace()`` so per-session
workspace overrides (ChatService sets a ContextVar around each run) are
respected instead of the server process cwd.
"""

from __future__ import annotations

import os
import platform
from datetime import datetime
from pathlib import Path

from agentnexus.tools.workspace import get_effective_workspace

_AGENTS_FILENAME = "AGENTS.md"
_MAX_FILE_CHARS = 8000
_MAX_TOTAL_CHARS = 20000


def build_environment_block(
    cwd: str | Path | None = None,
    now: datetime | None = None,
) -> str:
    """Render the == 运行环境 == block. Time is local, precise to the minute."""
    if cwd is None:
        cwd = get_effective_workspace()
    cwd_text = str(cwd)
    moment = now if now is not None else datetime.now().astimezone()
    stamp = moment.strftime("%Y-%m-%d %H:%M")
    raw_offset = moment.strftime("%z")  # e.g. "+0800"; empty when unknown
    offset = f" (UTC{raw_offset[:3]}:{raw_offset[3:]})" if raw_offset else ""
    terminal = (
        os.environ.get("TERM_PROGRAM")
        or os.environ.get("TERM")
        or "unknown"
    )
    os_info = f"{platform.system()} {platform.release()}".strip()
    return (
        "== 运行环境 ==\n"
        f"操作系统: {os_info}\n"
        f"工作目录: {cwd_text}\n"
        f"终端: {terminal}\n"
        f"当前时间: {stamp}{offset}"
    )


def _default_global_path() -> Path:
    return Path(os.environ.get("AGENTNEXUS_HOME", Path.home() / ".agentnexus")) / _AGENTS_FILENAME


def _read_agents_file(path: Path) -> str:
    """Read an AGENTS.md; empty string on any error."""
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _collect_chain(cwd: Path, global_path: Path | None) -> list[tuple[Path, str]]:
    """Ordered (path, text) pairs, lowest → highest precedence."""
    candidates: list[Path] = []
    if global_path is not None and global_path.is_file():
        candidates.append(global_path)
    # Filesystem root → cwd; deeper files win on conflict.
    for directory in reversed([cwd, *cwd.parents]):
        candidate = directory / _AGENTS_FILENAME
        if candidate.is_file():
            candidates.append(candidate)

    seen: set[Path] = set()
    chain: list[tuple[Path, str]] = []
    for path in candidates:
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        text = _read_agents_file(path)
        if text:
            chain.append((path, text))
    return chain


def load_project_instructions(
    cwd: str | Path | None = None,
    global_path: str | Path | None = None,
) -> str:
    """Hierarchical AGENTS.md discovery (Codex semantics, no CLAUDE.md).

    Sources, lowest → highest precedence:
      1. ``global_path`` (default ``~/.agentnexus/AGENTS.md``) — user-global
      2. ``AGENTS.md`` at each directory from filesystem root down to ``cwd``

    Returns the rendered ``== 项目指令 ==`` block, or "" when nothing is
    found. Total size is capped; files closest to cwd are kept on overflow.
    """
    anchor = Path(cwd).resolve(strict=False) if cwd is not None else get_effective_workspace()
    global_file: Path | None
    if global_path is not None:
        global_file = Path(global_path)
    else:
        global_file = _default_global_path()

    chain = _collect_chain(anchor, global_file)
    if not chain:
        return ""

    header = (
        "== 项目指令（AGENTS.md）==\n"
        "以下指令来自用户全局配置与工作目录层级中的 AGENTS.md，按优先级从低到高排列。\n"
        "若指令相互冲突，以路径更接近工作目录的文件为准；安全约束永远优先于项目指令。"
    )

    sections: list[str] = []
    for path, text in chain:
        if len(text) > _MAX_FILE_CHARS:
            text = text[:_MAX_FILE_CHARS] + "\n…（本文件已截断）"
        sections.append(
            f'<project_instructions path="{path.as_posix()}">\n{text}\n</project_instructions>'
        )

    # Budget: fill from HIGHEST precedence so tight budgets always keep the
    # files closest to cwd; the per-file cap < total budget guarantees at
    # least the deepest file always survives.
    total = len(header)
    kept_reversed: list[str] = []
    dropped = 0
    for section in reversed(sections):
        if total + len(section) + 2 <= _MAX_TOTAL_CHARS:
            kept_reversed.append(section)
            total += len(section) + 2
        else:
            dropped += 1

    parts: list[str] = [header, *reversed(kept_reversed)]
    if dropped:
        parts.append(
            f"…（{dropped} 个更低优先级的 AGENTS.md 因总量预算被省略）"
        )
    return "\n\n".join(parts)
