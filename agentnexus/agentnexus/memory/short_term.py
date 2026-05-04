import json
import time
from collections import deque


class ShortTermMemory:
    def __init__(self, max_messages: int = 50):
        self._messages: deque[dict] = deque(maxlen=max_messages)
        self._summary: str = ""

    def append(self, role: str, content: str):
        self._messages.append({"role": role, "content": content, "ts": time.time()})

    def get_all(self) -> list[dict]:
        return list(self._messages)

    def compact(self, summary: str, keep_recent: int = 4):
        recent = list(self._messages)[-keep_recent:] if len(self._messages) > keep_recent else list(self._messages)
        self._messages.clear()
        self._messages.append({"role": "system", "content": f"[会话摘要] {summary}", "ts": time.time()})
        for e in recent:
            self._messages.append(e)
        self._summary = summary

    def estimate_tokens(self) -> int:
        total = 0
        for m in self._messages:
            content = m.get("content", "")
            try:
                import litellm
                total += litellm.token_counter(text=content) or 0
            except Exception:
                import re
                chinese_chars = len(re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', content))
                other_chars = len(content) - chinese_chars
                total += int(chinese_chars * 1.4 + other_chars * 0.4)
        return total

    def clear(self):
        self._messages.clear()
        self._summary = ""


# Alias for compatibility
get_short_term_memory = ShortTermMemory
