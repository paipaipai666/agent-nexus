"""history_search tool — 检索已折叠归档的早期对话原文。

压缩产生的索引条目超预算后，最老条目折叠为归档目录，原文写入
{base}/history/{session}.jsonl（append-only，行内 i 为全局消息序号，
与索引条目的"消息X-Y"区间一致）。本工具按关键词扫描归档文件，
返回命中消息及相邻上下文，供模型取回被折叠的细节。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_OUTPUT_CHARS = 2000


def _history_dir() -> Path:
    from agentnexus.core.config import get_settings
    persist = get_settings().chroma_persist_dir
    base = persist.rsplit("/", 1)[0] if "/" in persist else str(Path(persist).parent)
    return Path(base) / "history"


def _load_archived() -> list[dict]:
    """读取全部会话归档，按 (session, 序号) 排序。"""
    hdir = _history_dir()
    if not hdir.is_dir():
        return []
    rows: list[dict] = []
    for fpath in sorted(hdir.glob("*.jsonl")):
        session = fpath.stem
        try:
            for line in fpath.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rec["session"] = session
                rows.append(rec)
        except OSError as e:
            logger.debug("history read failed for %s: %s", fpath, e)
    rows.sort(key=lambda r: (r.get("session", ""), r.get("i", 0)))
    return rows


def history_search(query: str, max_results: int = 5) -> str:
    """Search archived early conversation messages by keyword.

    Use this when the answer likely lives in the folded/archived history
    (the context shows a "[历史索引目录]" note) — e.g. exact error codes,
    IDs, file paths, config values, names, dates from earlier discussion.

    Args:
        query: Keywords to search for; space-separated terms match with OR.
        max_results: Max matching messages to return (default 5).

    Returns:
        Matching archived messages with neighbors, or a not-found notice.
    """
    terms = [t.strip().lower() for t in (query or "").split() if t.strip()]
    if not terms:
        return "[history_search] 请提供搜索关键词，如: history_search(query=\"错误码 F_IC\")"
    rows = _load_archived()
    if not rows:
        return "[history_search] 暂无归档历史（本会话还没有折叠过早期内容）"

    # 打分：命中不同词数多的优先
    scored = []
    for pos, rec in enumerate(rows):
        content = str(rec.get("content", "")).lower()
        hits = sum(1 for t in terms if t in content)
        if hits:
            scored.append((hits, pos, rec))
    if not scored:
        return f"[history_search] 归档历史中未找到: {' '.join(terms)}"
    scored.sort(key=lambda x: (-x[0], x[1]))

    # 取 top N 并扩展 ±1 相邻消息（同会话内）
    picked: dict[int, None] = {}
    for _, pos, _ in scored[:max(1, min(max_results, 20))]:
        for p in (pos - 1, pos, pos + 1):
            if 0 <= p < len(rows) and rows[p].get("session") == rows[pos].get("session"):
                picked[p] = None
    out_lines = []
    total = 0
    for p in sorted(picked):
        rec = rows[p]
        line = f"[{rec.get('session', '?')} · 消息{rec.get('i', '?')}] {rec.get('role', '?')}: {rec.get('content', '')}"
        if total + len(line) > _MAX_OUTPUT_CHARS:
            out_lines.append("...[结果截断，可用更精确的关键词缩小范围]")
            break
        out_lines.append(line)
        total += len(line)
    return "\n".join(out_lines)
