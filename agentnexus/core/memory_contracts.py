"""Public conversation snapshots and a current-turn working buffer."""

from __future__ import annotations

import copy
import json
import time
from typing import Protocol


class ConversationMemory(Protocol):
    """Conversation state consumed without depending on its storage layout."""

    def append(self, role: str, content: str, metadata: dict | None = None) -> None: ...
    def get_all(self) -> list[dict]: ...
    def get_summary(self) -> str: ...
    def load_snapshot(self, messages: list[dict], summary: str = "") -> None: ...
    def replace_messages(self, messages: list[dict]) -> None: ...
    def estimate_tokens(self) -> int: ...
    def clear(self) -> None: ...


def dump_snapshot(memory: ConversationMemory) -> str:
    """Encode the existing checkpoint format from a public conversation contract."""
    return json.dumps({"messages": memory.get_all(), "summary": memory.get_summary()}, ensure_ascii=False)


def restore_snapshot(memory: ConversationMemory, snapshot: str) -> None:
    """Restore messages and summary while preserving legacy message metadata."""
    data = json.loads(snapshot)
    if not isinstance(data, dict) or not isinstance(data.get("messages", []), list):
        raise ValueError("Invalid conversation snapshot")
    messages = data.get("messages", [])
    if any(not isinstance(message, dict) for message in messages):
        raise ValueError("Invalid conversation message")
    memory.load_snapshot(messages, data.get("summary", ""))


class TurnMemory:
    """Ephemeral working messages when cross-turn conversation retention is off."""

    def __init__(self):
        self.messages: list[dict] = []
        self.summary = ""

    def append(self, role: str, content: str, metadata: dict | None = None) -> None:
        message = {"role": role, "content": content, "ts": time.time()}
        if metadata:
            message["metadata"] = copy.deepcopy(metadata)
        self.messages.append(message)

    def get_all(self) -> list[dict]:
        return copy.deepcopy(self.messages)

    def get_summary(self) -> str:
        return self.summary

    def replace_messages(self, messages: list[dict]) -> None:
        self.messages = copy.deepcopy(messages)

    def load_snapshot(self, messages: list[dict], summary: str = "") -> None:
        self.replace_messages(messages)
        self.summary = summary

    def estimate_tokens(self) -> int:
        return sum((len(message.get("content", "")) + 3) // 4 for message in self.messages)

    @property
    def token_count(self) -> int:
        return self.estimate_tokens()

    def get_last_ts(self) -> float:
        return self.messages[-1].get("ts", 0.0) if self.messages else 0.0

    def to_json(self) -> str:
        return dump_snapshot(self)

    def clear(self) -> None:
        self.messages.clear()
        self.summary = ""


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
    role = msg.get("role", "")
    score += _ROLE_WEIGHTS.get(role, 0.4) * 0.4

    recency = position / max(total - 1, 1) if total > 1 else 1.0
    score += recency * 0.3

    content = msg.get("content", "")
    if any(kw in content for kw in _IMPORTANCE_KEYWORDS):
        score += 0.2
    if len(content) > 500:
        score += 0.1

    return min(score, 1.0)
