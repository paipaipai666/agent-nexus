"""JSON and text parsing helpers for ReAct agent responses."""

from __future__ import annotations

import json
import re


def robust_json_parse(raw_text: str) -> dict:
    if not raw_text or not raw_text.strip():
        return {"type": "error", "reason": "empty response"}
    clean = raw_text.strip()
    markdown_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", clean, re.DOTALL)
    if markdown_match:
        clean = markdown_match.group(1).strip()
    try:
        data = json.loads(clean)
        return classify_parsed(data)
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        fixed = re.sub(r",\s*([}\]])", r"\1", clean)
        data = json.loads(fixed)
        return classify_parsed(data)
    except (json.JSONDecodeError, ValueError):
        pass
    normalized = normalize_jsonish_text(clean)
    if normalized != clean:
        try:
            data = json.loads(normalized)
            return classify_parsed(data)
        except (json.JSONDecodeError, ValueError):
            pass
    data = try_fix_json(normalized)
    if data:
        return classify_parsed(data)
    fixed = _fix_string_internals(normalized)
    if fixed != normalized:
        try:
            data = json.loads(fixed)
            return classify_parsed(data)
        except (json.JSONDecodeError, ValueError):
            pass
    data = try_fix_json(fixed)
    if data:
        return classify_parsed(data)
    return {
        "type": "error",
        "reason": "JSON parse failed after all repair attempts",
        "raw": raw_text[:500],
    }


def classify_parsed(data: dict) -> dict:
    if not isinstance(data, dict):
        return {"type": "error", "reason": "JSON is not an object"}
    if "tool" in data and "params" in data:
        tool = str(data["tool"])
        params = data["params"] if isinstance(data["params"], dict) else {}
        return {"type": "tool_call", "tool": tool, "params": params}
    if "answer" in data:
        return {"type": "answer", "text": str(data["answer"])}
    if len(data) == 1:
        key = next(iter(data))
        return {"type": "answer", "text": str(data[key])}
    return {"type": "error", "reason": "JSON missing 'tool' or 'answer' key"}


def try_fix_json(text: str) -> dict | None:
    if not text:
        return None
    s = normalize_jsonish_text(text.strip())
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    end = -1
    for index in range(start, len(s)):
        if s[index] == "{":
            depth += 1
        elif s[index] == "}":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end == -1:
        s = s + "}"
        end = len(s) - 1
    candidate = s[start:end + 1]
    candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None


def normalize_jsonish_text(text: str) -> str:
    if not text:
        return text
    translation = str.maketrans({
        "：": ":",
        "，": ",",
        "｛": "{",
        "｝": "}",
        "［": "[",
        "］": "]",
        "（": "(",
        "）": ")",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
    })
    return text.translate(translation)


def _fix_string_internals(text: str) -> str:
    """Fix literal newlines and unescaped quotes inside JSON string values."""
    result = []
    i = 0
    in_string = False
    while i < len(text):
        ch = text[i]
        if not in_string:
            result.append(ch)
            if ch == '"':
                in_string = True
        else:
            if ch == '\\' and i + 1 < len(text):
                result.append(ch)
                result.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                result.append(ch)
                in_string = False
            elif ch == '\n':
                result.append('\\n')
            elif ch == '\r':
                result.append('\\r')
            elif ch == '\t':
                result.append('\\t')
            else:
                result.append(ch)
        i += 1
    return ''.join(result)


def extract_answer_from_text(text: str) -> str:
    if not text:
        return ""
    normalized = normalize_jsonish_text(text.strip())
    parsed = try_fix_json(normalized)
    if isinstance(parsed, dict):
        if "answer" in parsed:
            return str(parsed["answer"])
        if len(parsed) == 1:
            key = next(iter(parsed))
            return str(parsed[key])
    match = re.search(r'"answer"\s*:\s*"((?:\\.|[^"\\])*)"\s*(?:,|})', normalized)
    if match:
        try:
            return json.loads(f'"{match.group(1)}"')
        except json.JSONDecodeError:
            return match.group(1).replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    return text.strip()


def parse_json_response(text: str) -> dict:
    if not text or not text.strip():
        return {"type": "error", "reason": "empty response"}
    data = try_fix_json(text)
    if data:
        return classify_parsed(data)
    fixed = _fix_string_internals(text.strip())
    if fixed != text.strip():
        data = try_fix_json(fixed)
        if data:
            return classify_parsed(data)
    return {"type": "error", "reason": "not valid JSON"}
