"""MCP SDK object and result normalization helpers."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)
NAME_SANITIZER = re.compile(r"[^a-zA-Z0-9_]+")


def get_sdk_attr(obj: Any, *names: str, default: Any = None) -> Any:
    """Get an attribute from an SDK object, trying multiple naming conventions.

    Handles camelCase vs snake_case inconsistencies in MCP SDK.
    Returns the first non-None value found, or default if none match.
    """
    for name in names:
        val = getattr(obj, name, None)
        if val is not None:
            return val
    return default


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
    structured = get_sdk_attr(result, "structuredContent", "structured_content")
    if structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))

    for block in get_sdk_attr(result, "content", default=[]) or []:
        text = content_block_to_text(block)
        if text:
            parts.append(text)

    return "\n".join(part for part in parts if part).strip() or "[mcp] 工具未返回文本内容"


def normalize_resource_result(result: Any) -> str:
    parts = []
    for block in get_sdk_attr(result, "contents", "content", default=[]) or []:
        text = content_block_to_text(block)
        if text:
            parts.append(text)
    if not parts and hasattr(result, "model_dump"):
        return json_text(result.model_dump())
    return "\n".join(parts).strip() or "[mcp] 资源未返回文本内容"


def normalize_prompt_result(result: Any) -> str:
    messages = get_sdk_attr(result, "messages", default=[]) or []
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
        mime_type = get_sdk_attr(resource, "mimeType", "mime_type", default="unknown")
        if blob is not None:
            return f"[embedded resource: {mime_type}]"
        uri = getattr(resource, "uri", None)
        if uri:
            return f"[embedded resource] {uri}"

    mime_type = get_sdk_attr(block, "mimeType", "mime_type")
    data = getattr(block, "data", None)
    if mime_type and data is not None:
        return f"[binary content: {mime_type}]"

    if hasattr(block, "model_dump"):
        try:
            return json.dumps(block.model_dump(), ensure_ascii=False, default=str)
        except Exception:
            return str(block)
    return str(block)
