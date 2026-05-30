"""Read-time projection and microcompaction helpers."""

from __future__ import annotations

from collections.abc import Callable

# Shared truncation constants for consistency across projection layers
_TRUNCATION_THRESHOLD = 1000
_TRUNCATION_KEPT = 500
_MICROCOMPACT_THRESHOLD = 2000


def project_mild(messages: list[dict]) -> list[dict]:
    projected = []
    keep_recent = min(4, len(messages))
    for index, message in enumerate(messages):
        is_recent = index >= len(messages) - keep_recent
        content = message.get("content", "")
        if is_recent:
            projected.append(dict(message))
            continue
        if message["role"] in ("assistant", "tool") and len(content) > _TRUNCATION_THRESHOLD:
            projected.append({
                **message,
                "content": content[:_TRUNCATION_KEPT] + "\n...[投影截断]...\n" + content[-_TRUNCATION_KEPT:],
            })
        else:
            projected.append(dict(message))
    return projected


def project_aggressive(
    messages: list[dict],
    *,
    parse_tool_message: Callable[[str], tuple[str | None, str | None]],
    is_recoverable_tool: Callable[[str | None], bool],
) -> list[dict]:
    projected = []
    keep_recent = min(3, len(messages))
    boundary_inserted = False

    for index, message in enumerate(messages):
        is_recent = index >= len(messages) - keep_recent
        role = message.get("role", "")

        if is_recent:
            if not boundary_inserted and projected:
                projected.append({
                    "role": "system",
                    "content": "[上下文投影] 此标记之前的对话已被投影压缩。",
                })
                boundary_inserted = True
            projected.append(dict(message))
            continue

        if role == "tool":
            tool_name, _ = parse_tool_message(message.get("content", ""))
            if is_recoverable_tool(tool_name):
                projected.append({
                    **message,
                    "content": f"[工具结果已投影清除] 工具: {tool_name}",
                })
            else:
                projected.append(dict(message))
        elif role == "assistant":
            content = message.get("content", "")
            projected.append({
                **message,
                "content": content[:_TRUNCATION_KEPT] + "\n...[投影压缩]...\n" + content[-_TRUNCATION_KEPT:] if len(content) > _TRUNCATION_THRESHOLD else content,
            })
        else:
            projected.append(dict(message))

    if not boundary_inserted:
        projected.insert(0, {
            "role": "system",
            "content": "[上下文投影] 对话上下文已通过读时投影压缩。",
        })
    return projected


def build_projection(
    messages: list[dict],
    *,
    token_count: int,
    ctx_max: int,
    parse_tool_message: Callable[[str], tuple[str | None, str | None]],
    is_recoverable_tool: Callable[[str | None], bool],
) -> list[dict]:
    ratio = token_count / max(ctx_max, 1)
    if ratio < 0.90:
        return messages
    if ratio < 0.95:
        return project_mild(messages)
    return project_aggressive(
        messages,
        parse_tool_message=parse_tool_message,
        is_recoverable_tool=is_recoverable_tool,
    )


def microcompact_messages(
    messages: list[dict],
    *,
    parse_tool_message: Callable[[str], tuple[str | None, str | None]],
    is_recoverable_tool: Callable[[str | None], bool],
) -> tuple[list[dict], bool]:
    compacted = [dict(message) for message in messages]
    cleaned = False
    recoverable_indices = []
    for index, message in enumerate(compacted):
        if message["role"] == "tool":
            tool_name, _ = parse_tool_message(message.get("content", ""))
            if is_recoverable_tool(tool_name):
                recoverable_indices.append(index)

    keep_last = 5
    skip_indices = (
        set(recoverable_indices[-keep_last:])
        if len(recoverable_indices) > keep_last
        else set(recoverable_indices)
    )
    for index in recoverable_indices:
        if index in skip_indices:
            continue
        message = compacted[index]
        tool_name, _ = parse_tool_message(message.get("content", ""))
        compacted[index] = {
            **message,
            "content": f"[工具结果已清理] 工具: {tool_name}",
        }
        cleaned = True

    for index, message in enumerate(compacted):
        if message["role"] == "assistant":
            content = message.get("content", "")
            if len(content) > _MICROCOMPACT_THRESHOLD:
                compacted[index] = {
                    **message,
                    "content": content[:_TRUNCATION_KEPT] + "\n...[截断]...\n" + content[-_TRUNCATION_KEPT:],
                }
                cleaned = True
    return compacted, cleaned
