"""Separate provisional model text from completed control envelopes."""

import json
import re
from dataclasses import dataclass

from agentnexus.core import json_helpers


def is_protocol_json(text: str) -> bool:
    """Identify control fields without treating arbitrary JSON as a protocol."""
    parsed = json_helpers.try_fix_json(text)
    if isinstance(parsed, dict):
        return "answer" in parsed or {"tool", "params"}.issubset(parsed)
    normalized = json_helpers.normalize_jsonish_text(text)
    return re.search(r'\{\s*"(?:thought|answer|tool|params)"\s*:', normalized) is not None


def partial_protocol_answer(text: str) -> str:
    """Decode a completed or truncated answer string, excluding its envelope."""
    normalized = json_helpers.normalize_jsonish_text(text)
    match = re.search(r'"answer"\s*:\s*"((?:\\.|[^"\\])*)', normalized)
    if match is None:
        return ""
    try:
        return json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        return ""


@dataclass
class ResponseOutput:
    """Stream plain prefixes and hold structured candidates until interpretation."""

    buffered: bool = False
    published_chars: int = 0

    def feed(self, token: str) -> str:
        """Return text that cannot begin a JSON or fenced control envelope."""
        if self.buffered:
            return ""
        boundary = next((index for index, char in enumerate(token) if char in "{｛`"), len(token))
        self.buffered = boundary < len(token)
        visible = token[:boundary]
        self.published_chars += len(visible)
        return visible

    def finish(self, response: str, text: str, *, protocol: bool) -> str:
        """Publish decoded output or the remaining ordinary response text."""
        if protocol:
            return text
        prefix = response[:self.published_chars]
        return text[len(prefix):] if text.startswith(prefix) else ""
