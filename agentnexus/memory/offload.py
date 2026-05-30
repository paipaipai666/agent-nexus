"""Large tool-result offloading helpers."""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_OFFLOAD_MAX_AGE_SECONDS = 24 * 3600  # 24 hours


def offload_large_result(content: str, offload_dir: str, session_id: str) -> str:
    """Write a large tool result to disk and return the short memory stub."""
    offload_path = Path(offload_dir)
    offload_path.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_offloads(offload_path)
    timestamp = int(time.time() * 1000)
    filename = f"{session_id}_{timestamp}.txt"
    path = offload_path / filename
    path.write_text(content, encoding="utf-8")
    preview = content[:500]
    return f"[工具结果已缓存] 文件: {path}\n预览(前500字符): {preview}"


def _cleanup_stale_offloads(offload_dir: Path) -> None:
    """Delete offload files older than _OFFLOAD_MAX_AGE_SECONDS."""
    now = time.time()
    try:
        for f in offload_dir.iterdir():
            if f.is_file() and (now - f.stat().st_mtime) > _OFFLOAD_MAX_AGE_SECONDS:
                f.unlink()
    except OSError as e:
        logger.debug("Offload cleanup error (non-fatal): %s", e)
