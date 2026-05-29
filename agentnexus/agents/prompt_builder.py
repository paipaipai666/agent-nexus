"""Prompt construction helpers for ReActAgent."""

from __future__ import annotations

from typing import Any


def build_react_prompt(
    *,
    template: str,
    tools_desc: str,
    question: str,
    history_str: str,
    memory_context: str,
    conversation_context: str,
    available_skill_context: str = "",
    mcp_context: str = "",
    compiled_profile: Any = None,
    todo_context: str = "",
) -> str:
    blocks = [available_skill_context, mcp_context]
    if compiled_profile:
        blocks.extend([compiled_profile.fragments_text, compiled_profile.workflow_guidance])
    if todo_context:
        blocks.append(todo_context)
    extra_context = "\n\n".join(block for block in blocks if block)
    if extra_context:
        extra_context += "\n\n"
    return template.format(
        tools=tools_desc,
        question=question,
        history=history_str,
        memory_context=memory_context,
        conversation_context=conversation_context + extra_context,
    )


def build_react_messages(
    *,
    system_rules: str,
    tools_desc: str,
    question: str,
    memory_context: str = "",
    conversation_context: str = "",
    available_skill_context: str = "",
    mcp_context: str = "",
    compiled_profile: Any = None,
    todo_context: str = "",
) -> list[dict[str, str]]:
    """Build messages array with stable prefix for prompt caching.

    Structure:
        [0] system: fixed rules (stable, cacheable prefix)
        [1] system: tools description (relatively stable)
        [2] system: memory + conversation context (variable)
        [3] user: question (variable)

    This structure maximizes prompt cache hit rate by keeping
    the longest possible stable prefix at the beginning.
    """
    messages: list[dict[str, str]] = []

    # 1. Fixed system rules — always identical, best cache target
    messages.append({"role": "system", "content": system_rules})

    # 2. Tools description — changes only when tools change
    if tools_desc:
        messages.append({"role": "system", "content": f"== 可用工具 ==\n{tools_desc}"})

    # 3. Variable context blocks — combined into one message
    context_blocks: list[str] = []
    if memory_context:
        context_blocks.append(memory_context)
    if conversation_context:
        context_blocks.append(conversation_context)
    if available_skill_context:
        context_blocks.append(available_skill_context)
    if mcp_context:
        context_blocks.append(mcp_context)
    if compiled_profile:
        context_blocks.append(compiled_profile.fragments_text)
        context_blocks.append(compiled_profile.workflow_guidance)
    if todo_context:
        context_blocks.append(todo_context)

    if context_blocks:
        combined = "\n\n".join(block for block in context_blocks if block)
        if combined:
            messages.append({"role": "system", "content": combined})

    # 4. User question — always variable
    messages.append({"role": "user", "content": f"== 当前任务 ==\nQuestion: {question}"})

    return messages


def build_conversation_context(memory_manager, per_msg_limit: int = 500) -> str:
    if not memory_manager or not memory_manager.short_term:
        return ""
    stm = memory_manager.short_term
    summary = stm.get_summary()
    messages = stm.get_all()
    user_assistant_msgs = [message for message in messages if message["role"] in ("user", "assistant")]
    if summary:
        recent = user_assistant_msgs[-3:] if len(user_assistant_msgs) > 3 else user_assistant_msgs
        parts = ["== 对话历史摘要 ==", summary]
        if recent:
            parts.append("\n== 最近对话 ==")
            for message in recent:
                role_label = "用户" if message["role"] == "user" else "助手"
                content = message["content"][:per_msg_limit]
                parts.append(f"{role_label}: {content}")
        return "\n".join(parts) + "\n\n"
    if not user_assistant_msgs:
        return ""
    recent = user_assistant_msgs[-6:]
    lines = []
    for message in recent:
        role_label = "用户" if message["role"] == "user" else "助手"
        content = message["content"][:per_msg_limit]
        lines.append(f"{role_label}: {content}")
    return "== 近期对话 ==\n" + "\n".join(lines) + "\n\n"
