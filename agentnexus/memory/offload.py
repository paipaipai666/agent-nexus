"""Large tool-result offloading helpers."""

from __future__ import annotations

import time
from pathlib import Path


def offload_large_result(content: str, offload_dir: str, session_id: str) -> str:
    """Write a large tool result to disk and return the short memory stub."""
    Path(offload_dir).mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time() * 1000)
    filename = f"{session_id}_{timestamp}.txt"
    path = Path(offload_dir) / filename
    path.write_text(content, encoding="utf-8")
    preview = content[:500]
    return f"[工具结果已缓存] 文件: {path}\n预览(前500字符): {preview}"
