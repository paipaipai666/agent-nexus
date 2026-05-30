import json
import logging
import os
import time
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)

_tiktoken_encoding = None
_tiktoken_loaded = False


def _get_tiktoken_encoding():
    global _tiktoken_encoding, _tiktoken_loaded
    if _tiktoken_loaded:
        return _tiktoken_encoding
    _tiktoken_loaded = True
    try:
        import tiktoken
        _tiktoken_encoding = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _tiktoken_encoding = None
    return _tiktoken_encoding


class ShortTermMemory:
    def __init__(self, max_messages: int = 50, wal_path: str | None = None):
        self._messages: deque[dict] = deque(maxlen=max_messages)
        self._summary: str = ""
        self._append_count: int = 0
        self._wal_path = wal_path
        if self._wal_path:
            self._recover_wal()

    def append(self, role: str, content: str):
        self._messages.append({"role": role, "content": content, "ts": time.time()})
        self._append_count += 1
        if self._wal_path and self._append_count % 5 == 0:
            self._flush_wal()

    def get_all(self) -> list[dict]:
        return list(self._messages)

    def compact(self, summary: str, keep_recent: int = 4):
        recent = list(self._messages)[-keep_recent:] if len(self._messages) > keep_recent else list(self._messages)
        self._messages.clear()
        self._messages.append({"role": "system", "content": f"[会话摘要] {summary}", "ts": time.time()})
        for e in recent:
            self._messages.append(e)
        self._summary = summary

    def compact_full(self, summary: str, message_count: int = 0, is_auto: bool = True):
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
        self._summary = summary

    def snip(self, keep_recent: int = 10) -> int:
        if len(self._messages) <= keep_recent:
            return 0
        removed = len(self._messages) - keep_recent
        recent = list(self._messages)[-keep_recent:]
        self._messages.clear()
        self._messages.append({
            "role": "system",
            "content": "[上下文已裁剪] 此标记之前的对话历史已被移除，共移除 {} 条消息。".format(removed),
            "ts": time.time(),
        })
        for msg in recent:
            self._messages.append(msg)
        return removed

    def get_last_ts(self) -> float:
        if self._messages:
            return self._messages[-1]["ts"]
        return 0.0

    def estimate_tokens(self) -> int:
        enc = _get_tiktoken_encoding()
        if enc is None:
            return self._estimate_tokens_fallback()
        total = 0
        for m in self._messages:
            content = m.get("content", "")
            total += len(enc.encode(content))
        return total

    def _estimate_tokens_fallback(self) -> int:
        import re
        total = 0
        for m in self._messages:
            content = m.get("content", "")
            chinese_chars = len(re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', content))
            ascii_chars = len(re.findall(r'[a-zA-Z0-9]', content))
            other_chars = len(content) - chinese_chars - ascii_chars
            total += int(chinese_chars * 1.8 + ascii_chars * 0.75 + other_chars * 0.3)
        return total

    def get_summary(self) -> str:
        """Return the current compressed summary, or empty string if none."""
        return self._summary

    def clear(self):
        self._messages.clear()
        self._summary = ""

    def _flush_wal(self):
        """Write current state to a lightweight WAL file for crash recovery."""
        if not self._wal_path:
            return
        try:
            wal_data = {
                "messages": list(self._messages),
                "summary": self._summary,
                "append_count": self._append_count,
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
    def from_json(cls, json_str: str, max_messages: int = 50) -> "ShortTermMemory":
        """Restore from a JSON snapshot. Unknown keys are ignored for forward compat."""
        data = json.loads(json_str)
        inst = cls(max_messages=max_messages)
        for msg in data.get("messages", []):
            inst._messages.append(msg)
        inst._summary = data.get("summary", "")
        return inst


# Alias for compatibility
get_short_term_memory = ShortTermMemory
