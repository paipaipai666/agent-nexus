"""Prompt construction helpers for ReActAgent."""

from __future__ import annotations

from typing import Any

from agentnexus.memory.compaction import parse_tool_message

TOOL_CONTEXT_LIMIT = 2000      # max chars for tool results in conversation context
N_TURNS_NO_SUMMARY = 3         # turns to show when no compressed summary exists
N_TURNS_WITH_SUMMARY = 2       # turns to show alongside compressed summary


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
    workflow_context: str = "",
) -> list[dict[str, str]]:
    """Build messages array with stable prefix for prompt caching.

    Structure:
        [0] system: fixed rules (stable, cacheable prefix)
        [1] system: tools description (relatively stable)
        [2] system: memory + conversation context (variable)
        [3] system: workflow runtime context (when active)
        [4] user: question (variable)

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

    # 4. Workflow runtime context — as a system message, not baked into user question
    if workflow_context:
        messages.append({"role": "system", "content": workflow_context})

    # 5. User question — always variable, raw user question only
    messages.append({"role": "user", "content": f"== 当前任务 ==\nQuestion: {question}"})

    return messages


def build_conversation_context(memory_manager) -> str:
    if not memory_manager or not memory_manager.short_term:
        return ""
    stm = memory_manager.short_term
    summary = stm.get_summary()
    messages = stm.get_all()

    relevant_msgs = [m for m in messages if m["role"] in ("user", "assistant", "tool", "system")]

    if summary:
        turns = _collect_recent_turns(relevant_msgs, N_TURNS_WITH_SUMMARY)
        parts = ["== 对话历史摘要 ==", summary]
        if turns:
            parts.append("\n== 最近对话 ==")
            parts.append(_format_turns_for_context(turns))
        return "\n".join(parts) + "\n\n"

    turns = _collect_recent_turns(relevant_msgs, N_TURNS_NO_SUMMARY)
    if not turns:
        return ""
    return "== 近期对话 ==\n" + _format_turns_for_context(turns) + "\n\n"


def _collect_recent_turns(messages: list[dict], n_turns: int) -> list[list[dict]]:
    """Collect the last N complete turns from STM messages.

    A turn starts with a ``user`` message and ends at the next ``user``
    message or a ``system`` structural marker (``[最终答案]``).

    Convention: compaction markers (``[会话摘要]``, ``[上下文已裁剪]``,
    ``[恢复文件]``) are written to STM *between* turns, so encountering
    them when ``current_turn`` is empty is safe to skip.  If compaction
    behaviour changes, this assumption must be revisited.
    """
    turns: list[list[dict]] = []
    current_turn: list[dict] = []

    for msg in messages:
        if msg["role"] == "user":
            if current_turn:
                turns.append(current_turn)
            current_turn = [msg]
        elif msg["role"] == "system" and msg["content"].startswith("["):
            if current_turn:
                current_turn.append(msg)
                turns.append(current_turn)
                current_turn = []
        elif current_turn:
            current_turn.append(msg)

    if current_turn:
        turns.append(current_turn)

    return turns[-n_turns:]


def _format_turns_for_context(turns: list[list[dict]]) -> str:
    """Format collected turns into a readable context block.

    Truncation policy (per-message-unit, not mid-message):
    - user / assistant: kept intact — these are semantic units
    - tool results: truncated at source (data, not conversation)
    """
    FINAL_ANSWER_PREFIX = "[最终答案]"
    role_label = {"user": "用户", "assistant": "助手", "tool": "工具"}
    lines = []
    for turn in turns:
        for message in turn:
            # Skip display-only messages (e.g. final-answer thought)
            if message.get("metadata", {}).get("display_only"):
                continue
            # Convert [最终答案] system marker into assistant message for context
            if message["role"] == "system" and message["content"].startswith(FINAL_ANSWER_PREFIX):
                actual_answer = message["content"][len(FINAL_ANSWER_PREFIX):].strip()
                if actual_answer:
                    lines.append(f"助手: {actual_answer}")
                continue
            if message["role"] == "system":
                continue
            label = role_label.get(message["role"], message["role"])
            if message["role"] == "tool":
                content = _format_tool_for_context(message["content"], TOOL_CONTEXT_LIMIT)
            else:
                content = message["content"]
            lines.append(f"{label}: {content}")
    return "\n".join(lines)


def _format_tool_for_context(content: str, limit: int) -> str:
    """Format a tool STM message for conversation context display.

    Tool results are data (not conversation), so they can be truncated
    at the boundary with a clear marker showing original length.
    """
    tool_name, _ = parse_tool_message(content)
    obs_idx = content.find("Observation: ")
    observation = content[obs_idx + len("Observation: "):] if obs_idx >= 0 else content
    observation = " ".join(observation.split())
    original_len = len(observation)
    if original_len > limit:
        observation = observation[:limit] + f"\n...[已截断，原始长度 {original_len} 字符]"
    label = tool_name or "工具"
    return f"[{label}] {observation}"
