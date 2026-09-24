import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)

_tiktoken_encoding = None
_tiktoken_loaded = False


def _get_tiktoken_encoding():
    global _tiktoken_encoding, _tiktoken_loaded
    if not _tiktoken_loaded:
        import tiktoken

        _tiktoken_encoding = tiktoken.get_encoding("cl100k_base")
        _tiktoken_loaded = True
    return _tiktoken_encoding


# ── Importance scoring ────────────────────────────────────────────
_ROLE_WEIGHTS = {"system": 0.9, "user": 0.8, "assistant": 0.5, "tool": 0.3}
_IMPORTANCE_KEYWORDS = frozenset([
    "记住", "重要", "约束", "决策", "规则", "必须", "不要", "禁止",
    "优先", "关键", "核心", "目标", "需求", "偏好", "我是", "我叫",
])


def compute_importance(msg: dict, position: int, total: int) -> float:
    """Compute an importance score for a message (0.0 ~ 1.0).

    Considers role weight, recency, and content signals.
    """
    score = 0.0
    # 1. Role weight (40%)
    role = msg.get("role", "")
    score += _ROLE_WEIGHTS.get(role, 0.4) * 0.4

    # 2. Recency — newer is higher, but not the only factor (30%)
    recency = position / max(total - 1, 1) if total > 1 else 1.0
    score += recency * 0.3

    # 3. Content signals (30%)
    content = msg.get("content", "")
    if any(kw in content for kw in _IMPORTANCE_KEYWORDS):
        score += 0.2
    if len(content) > 500:
        score += 0.1

    return min(score, 1.0)


class ShortTermMemory:
    def __init__(self, max_messages: int = 50, max_tokens: int = 0,
                 wal_path: str | None = None):
        self._lock = threading.RLock()
        self._messages: deque[dict] = deque(maxlen=max_messages)
        self._summary: str = ""
        self._append_count: int = 0
        self._max_tokens: int = max_tokens  # 0 = no token budget limit
        self._token_count: int = 0  # incremental token counter
        self._wal_path = wal_path
        if self._wal_path:
            self._recover_wal()

    def append(self, role: str, content: str, metadata: dict | None = None):
        msg = {"role": role, "content": content, "ts": time.time()}
        if metadata:
            msg["metadata"] = metadata
        with self._lock:
            # Incremental token tracking
            self._token_count += self._estimate_msg_tokens(msg)
            # If deque is full, the evicted message's tokens should be subtracted
            if self._messages.maxlen and len(self._messages) == self._messages.maxlen:
                evicted = self._messages[0]
                self._token_count -= self._estimate_msg_tokens(evicted)
            self._messages.append(msg)
            self._append_count += 1
            if self._wal_path and self._append_count % 5 == 0:
                self._flush_wal()

    def get_all(self) -> list[dict]:
        with self._lock:
            return list(self._messages)

    def compact(self, summary: str, keep_recent: int = 4):
        with self._lock:
            recent = list(self._messages)[-keep_recent:] if len(self._messages) > keep_recent else list(self._messages)
            self._messages.clear()
            self._messages.append({"role": "system", "content": f"[会话摘要] {summary}", "ts": time.time()})
            for e in recent:
                self._messages.append(e)
            self._summary = summary
            self._recalc_token_count()

    def compact_full(self, summary: str, message_count: int = 0, is_auto: bool = True,
                     keep_recent: int = 6):
        """Hybrid compaction: summary system message + last keep_recent original messages.

        Preserves role structure in the tail so the LLM can distinguish
        user vs assistant messages after compaction.
        """
        with self._lock:
            recent = list(self._messages)[-keep_recent:] if keep_recent > 0 else []
            self._messages.clear()
            boundary = (
                "本会话是从之前一次因上下文耗尽而中断的对话延续过来的。"
                "以下摘要概述了之前的对话内容：\n\n"
            ) if is_auto else (
                "对话已被手动压缩。以下是压缩后的摘要：\n\n"
            )
            self._messages.append({
                "role": "system",
                "content": boundary + summary,
                "ts": time.time(),
            })
            for msg in recent:
                self._messages.append(msg)
            self._summary = summary
            self._recalc_token_count()

    def compact_indexed(self, new_entries: list[tuple[int, int, str]],
                        keep_recent: int = 6):
        """分段索引压缩：保留已有索引 + 追加新条目 + 保留最近原文。

        new_entries: [(seg_start, seg_end, summary)]，序号为全局消息序号。
        已有索引条目（metadata.memory_index=True）原样保留、绝不重述——
        索引 append-only，杜绝"摘要的摘要"造成的细节复利丢失。
        """
        with self._lock:
            all_msgs = list(self._messages)
            old_index = [m for m in all_msgs
                         if m.get("metadata", {}).get("memory_index")]
            raw = [m for m in all_msgs
                   if not m.get("metadata", {}).get("memory_index")]
            recent = raw[-keep_recent:] if keep_recent > 0 else []
            self._messages.clear()
            for m in old_index:
                self._messages.append(m)
            for seg_start, seg_end, summary in new_entries:
                self._messages.append({
                    "role": "system",
                    "content": f"[历史索引 消息{seg_start + 1}-{seg_end}] {summary}",
                    "ts": time.time(),
                    "metadata": {"memory_index": True,
                                 "seg_start": seg_start, "seg_end": seg_end},
                })
            for m in recent:
                self._messages.append(m)
            self._summary = "\n".join(s for _, _, s in new_entries)
            self._recalc_token_count()

    def fold_index(self, max_tokens: int) -> int:
        """索引超预算时把最老的索引条目折叠成一条归档目录。

        保留最新的若干条目（总 token ≤ max_tokens），更老的条目从上下文移除
        （原文已在 history 归档中，可通过 history_search 检索），替换为一条
        固定大小的目录消息。返回折叠的条目数；目录消息带 archive_dir 标记，
        不参与后续折叠，也不会被 segment_summarize 重喂。
        """
        with self._lock:
            msgs = list(self._messages)
            dirs = [m for m in msgs if m.get("metadata", {}).get("archive_dir")]
            entries = [m for m in msgs
                       if m.get("metadata", {}).get("memory_index")
                       and not m.get("metadata", {}).get("archive_dir")]
            raw = [m for m in msgs
                   if not m.get("metadata", {}).get("memory_index")]
            total = sum(self._estimate_msg_tokens(m) for m in entries)
            if total <= max_tokens:
                return 0
            kept: list[dict] = []
            acc = 0
            for m in reversed(entries):
                t = self._estimate_msg_tokens(m)
                if acc + t > max_tokens:
                    break
                kept.insert(0, m)
                acc += t
            folded = entries[:len(entries) - len(kept)]
            if not folded:
                return 0
            # 与既有目录合并：折叠区间从 0 连续增长，始终保持一条目录
            end = max(int(m["metadata"].get("seg_end", 0)) for m in folded)
            prev_count = 0
            for d in dirs:
                end = max(end, int(d["metadata"].get("seg_end", 0)))
                prev_count += int(d["metadata"].get("folded_count", 0))
            directory = {
                "role": "system",
                "content": (
                    f"[历史索引目录] 消息1-{end} 的早期对话已折叠归档，"
                    f"共 {prev_count + len(folded)} 段。"
                    "若需要其中的具体细节（错误码/ID/路径/配置/人名/时间等），"
                    "调用 history_search 工具用关键词检索原文。"
                ),
                "ts": time.time(),
                "metadata": {"memory_index": True, "archive_dir": True,
                             "seg_start": 0, "seg_end": end,
                             "folded_count": prev_count + len(folded)},
            }
            self._messages.clear()
            for m in [directory] + kept + raw:
                self._messages.append(m)
            self._recalc_token_count()
            return len(folded)

    def snip(self, keep_recent: int = 10) -> int:
        with self._lock:
            if len(self._messages) <= keep_recent:
                return 0
            all_msgs = list(self._messages)
            total = len(all_msgs)
            # Partition: recent tail is always kept
            tail = all_msgs[-keep_recent:]
            head = all_msgs[:-keep_recent]
            # Score head messages and keep those above threshold
            _IMPORTANCE_KEEP_THRESHOLD = 0.65
            preserved_head = []
            removed_count = 0
            for i, msg in enumerate(head):
                imp = compute_importance(msg, i, total)
                if imp >= _IMPORTANCE_KEEP_THRESHOLD:
                    preserved_head.append(msg)
                else:
                    removed_count += 1
            if removed_count == 0:
                return 0
            # Rebuild deque
            self._messages.clear()
            marker = {
                "role": "system",
                "content": "[上下文已裁剪] 此标记之前的对话历史已被移除，共移除 {} 条消息。".format(removed_count),
                "ts": time.time(),
            }
            self._messages.append(marker)
            for msg in preserved_head:
                self._messages.append(msg)
            for msg in tail:
                self._messages.append(msg)
        self._recalc_token_count()
        return removed_count

    def get_last_ts(self) -> float:
        with self._lock:
            if self._messages:
                return self._messages[-1]["ts"]
            return 0.0

    def estimate_tokens(self) -> int:
        with self._lock:
            enc = _get_tiktoken_encoding()
            total = 0
            for m in self._messages:
                content = m.get("content", "")
                total += len(enc.encode(content))
            return total

    def _estimate_msg_tokens(self, msg: dict) -> int:
        """Token estimate for a single message."""
        content = msg.get("content", "")
        enc = _get_tiktoken_encoding()
        return len(enc.encode(content))

    @property
    def token_count(self) -> int:
        """Return the incremental token counter (fast, no re-encoding)."""
        return self._token_count

    @property
    def max_tokens(self) -> int:
        """Return the token budget (0 = unlimited)."""
        return self._max_tokens

    def is_over_token_budget(self) -> bool:
        """Check if current tokens exceed the configured budget."""
        return self._max_tokens > 0 and self._token_count > self._max_tokens

    def _recalc_token_count(self):
        """Recalculate the token counter from scratch (used after structural changes)."""
        self._token_count = sum(self._estimate_msg_tokens(m) for m in self._messages)

    def get_summary(self) -> str:
        """Return the current compressed summary, or empty string if none."""
        return self._summary

    def replace_messages(self, messages: list[dict]) -> None:
        """Replace all messages atomically (thread-safe, triggers WAL flush)."""
        with self._lock:
            self._messages.clear()
            for msg in messages:
                self._messages.append(msg)
            self._recalc_token_count()
            if self._wal_path:
                self._flush_wal()

    def clear(self):
        with self._lock:
            self._messages.clear()
            self._summary = ""
            self._token_count = 0

    def _flush_wal(self):
        """Write current state to a lightweight WAL file for crash recovery."""
        if not self._wal_path:
            return
        try:
            wal_data = {
                "messages": list(self._messages),
                "summary": self._summary,
                "append_count": self._append_count,
                "token_count": self._token_count,
            }
            Path(self._wal_path).parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._wal_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(wal_data, f, ensure_ascii=False)
            os.replace(tmp_path, self._wal_path)
        except Exception as e:
            logger.warning("STM WAL flush failed: %s", e)

    def _recover_wal(self):
        """Recover state from WAL file if it exists."""
        if not self._wal_path:
            return
        wal_file = Path(self._wal_path)
        if not wal_file.exists():
            return
        try:
            with open(wal_file, "r", encoding="utf-8") as f:
                wal_data = json.load(f)
            for msg in wal_data.get("messages", []):
                self._messages.append(msg)
            self._summary = wal_data.get("summary", "")
            self._append_count = wal_data.get("append_count", 0)
            # Restore token count if available, otherwise recalculate
            if "token_count" in wal_data:
                self._token_count = wal_data["token_count"]
            else:
                self._recalc_token_count()
            logger.info("Recovered %d messages from STM WAL", len(self._messages))
            wal_file.unlink()
        except Exception as e:
            logger.warning("STM WAL recovery failed: %s", e)

    def to_json(self) -> str:
        """Serialize full state to JSON for checkpoint snapshots."""
        return json.dumps({
            "messages": list(self._messages),
            "summary": self._summary,
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str, max_messages: int = 50,
                  max_tokens: int = 0) -> "ShortTermMemory":
        """Restore from a JSON snapshot. Unknown keys are ignored for forward compat."""
        data = json.loads(json_str)
        inst = cls(max_messages=max_messages, max_tokens=max_tokens)
        for msg in data.get("messages", []):
            inst._messages.append(msg)
        inst._summary = data.get("summary", "")
        inst._recalc_token_count()
        return inst


# Alias for compatibility
get_short_term_memory = ShortTermMemory
