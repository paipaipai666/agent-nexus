"""MCP SDK object and result normalization helpers."""

from __future__ import annotations

import json
import re
from typing import Any

NAME_SANITIZER = re.compile(r"[^a-zA-Z0-9_]+")


def sanitize_name(value: str) -> str:
    cleaned = NAME_SANITIZER.sub("_", (value or "").strip().lower()).strip("_")
    return cleaned or "tool"


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def dump_sdk_object(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    if isinstance(value, dict):
        return dict(value)
    data = {}
    for attr in ("name", "uri", "uriTemplate", "uri_template", "description", "mimeType", "mime_type", "arguments"):
        if hasattr(value, attr):
            data[attr] = getattr(value, attr)
    return data or {"value": str(value)}


def normalize_tool_result(result: Any) -> str:
    parts: list[str] = []
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))

    for block in getattr(result, "content", []) or []:
        text = content_block_to_text(block)
        if text:
            parts.append(text)

    return "\n".join(part for part in parts if part).strip() or "[mcp] 工具未返回文本内容"


def normalize_resource_result(result: Any) -> str:
    parts = []
    for block in getattr(result, "contents", None) or getattr(result, "content", []) or []:
        text = content_block_to_text(block)
        if text:
            parts.append(text)
    if not parts and hasattr(result, "model_dump"):
        return json_text(result.model_dump())
    return "\n".join(parts).strip() or "[mcp] 资源未返回文本内容"


def normalize_prompt_result(result: Any) -> str:
    messages = getattr(result, "messages", []) or []
    if messages:
        return json_text([dump_sdk_object(message) for message in messages])
    if hasattr(result, "model_dump"):
        return json_text(result.model_dump())
    return str(result)


def content_block_to_text(block: Any) -> str:
    text = getattr(block, "text", None)
    if text:
        return str(text)

    resource = getattr(block, "resource", None)
    if resource is not None:
        resource_text = getattr(resource, "text", None)
        if resource_text:
            return str(resource_text)
        blob = getattr(resource, "blob", None)
        mime_type = getattr(resource, "mimeType", None) or getattr(resource, "mime_type", None) or "unknown"
        if blob is not None:
            return f"[embedded resource: {mime_type}]"
        uri = getattr(resource, "uri", None)
        if uri:
            return f"[embedded resource] {uri}"

    mime_type = getattr(block, "mimeType", None) or getattr(block, "mime_type", None)
    data = getattr(block, "data", None)
    if mime_type and data is not None:
        return f"[binary content: {mime_type}]"

    if hasattr(block, "model_dump"):
        try:
            return json.dumps(block.model_dump(), ensure_ascii=False, default=str)
        except Exception:
            return str(block)
    return str(block)
