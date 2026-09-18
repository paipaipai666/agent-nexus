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


# ── Section model ────────────────────────────────────────────────
# Context blocks are named sections in a dict, not anonymous text
# concatenated into one message. Naming makes rebuilds diffable:
# rendering is deterministic, so a group whose sections all stayed
# the same re-renders byte-identical and the provider's prefix cache
# keeps hitting up to the first changed group.
#
# Groups are ordered stable → volatile: memory and conversation
# change only on compaction; the static group is fixed for the run;
# tools text appears only on JSON strategies (degrade can add it
# mid-run); the volatile group (env timestamp, todo) churns most.

SECTION_MEMORY = "memory"
SECTION_CONVERSATION = "conversation"
SECTION_SKILLS = "skills"
SECTION_MCP = "mcp"
SECTION_PROFILE_FRAGMENTS = "profile_fragments"
SECTION_PROFILE_GUIDANCE = "profile_guidance"
SECTION_PROJECT = "project_instructions"
SECTION_APPENDIX = "append_system_prompt"
SECTION_ENVIRONMENT = "environment"
SECTION_TODO = "todo"

# Section order within the static group — user-authored content last
# (salience), mirroring the previous combined-message layout.
_STATIC_SECTIONS = (
    SECTION_SKILLS,
    SECTION_MCP,
    SECTION_PROFILE_FRAGMENTS,
    SECTION_PROFILE_GUIDANCE,
    SECTION_PROJECT,
    SECTION_APPENDIX,
)
# Volatile group: minute-precision env timestamp + todo churn.
_VOLATILE_SECTIONS = (SECTION_ENVIRONMENT, SECTION_TODO)


def build_react_sections(
    *,
    memory_context: str = "",
    conversation_context: str = "",
    available_skill_context: str = "",
    mcp_context: str = "",
    compiled_profile: Any = None,
    todo_context: str = "",
    environment_context: str = "",
    project_instructions: str = "",
    append_system_prompt: str = "",
) -> dict[str, str]:
    """Assemble named context sections; empty blocks are dropped."""
    sections: dict[str, str] = {}
    if memory_context:
        sections[SECTION_MEMORY] = memory_context
    if conversation_context:
        sections[SECTION_CONVERSATION] = conversation_context
    if available_skill_context:
        sections[SECTION_SKILLS] = available_skill_context
    if mcp_context:
        sections[SECTION_MCP] = mcp_context
    if compiled_profile is not None:
        if compiled_profile.fragments_text:
            sections[SECTION_PROFILE_FRAGMENTS] = compiled_profile.fragments_text
        if compiled_profile.workflow_guidance:
            sections[SECTION_PROFILE_GUIDANCE] = compiled_profile.workflow_guidance
    if project_instructions:
        sections[SECTION_PROJECT] = project_instructions
    if append_system_prompt:
        sections[SECTION_APPENDIX] = append_system_prompt
    if environment_context:
        sections[SECTION_ENVIRONMENT] = environment_context
    if todo_context:
        sections[SECTION_TODO] = todo_context
    return sections


def diff_sections(
    previous: dict[str, str] | None,
    current: dict[str, str],
) -> set[str]:
    """Names of sections added, removed, or modified since `previous`.

    `previous=None` (first build) counts every section as changed.
    """
    if previous is None:
        return set(current)
    changed = {name for name, text in current.items() if previous.get(name) != text}
    changed.update(name for name in previous if name not in current)
    return changed


def _join_group(sections: dict[str, str], names: tuple[str, ...]) -> str:
    return "\n\n".join(sections[name] for name in names if sections.get(name))


def assemble_react_messages(
    *,
    system_rules: str,
    tools_desc: str,
    sections: dict[str, str],
    question: str,
    workflow_context: str = "",
    include_tools_desc: bool = True,
) -> list[dict[str, str]]:
    """Render sections into messages, one message per group.

    Layout (later groups invalidate only themselves and what follows,
    so volatile content sits at the end of the system prefix):
        [0] system: fixed rules (stable, cacheable prefix)
        [1] system: memory (own message; changes on compaction)
        [2] system: conversation + persona + behavior fragments
        [3] system: static group (skills, mcp, profile, project
             instructions, user appendix)
        [4] system: tools text (JSON strategies only; native schemas
             already carry tool info)
        [5] system: volatile group (environment, todo)
        [6] system: workflow runtime context (when active)
        [7] user: question
    """
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_rules},
    ]
    if sections.get(SECTION_MEMORY):
        messages.append({"role": "system", "content": sections[SECTION_MEMORY]})
    if sections.get(SECTION_CONVERSATION):
        messages.append({"role": "system", "content": sections[SECTION_CONVERSATION]})

    static_text = _join_group(sections, _STATIC_SECTIONS)
    if static_text:
        messages.append({"role": "system", "content": static_text})

    # JSON strategies have no native schemas — the text list is their
    # only tool surface. NATIVE_TOOLS skips it to avoid double-describing.
    if include_tools_desc and tools_desc:
        messages.append({"role": "system", "content": f"== 可用工具 ==\n{tools_desc}"})

    volatile_text = _join_group(sections, _VOLATILE_SECTIONS)
    if volatile_text:
        messages.append({"role": "system", "content": volatile_text})

    if workflow_context:
        messages.append({"role": "system", "content": workflow_context})

    messages.append({"role": "user", "content": f"== 当前任务 ==\nQuestion: {question}"})
    return messages


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
    environment_context: str = "",
    project_instructions: str = "",
    append_system_prompt: str = "",
    include_tools_desc: bool = True,
) -> list[dict[str, str]]:
    """One-shot convenience wrapper: sections + assembly in one call.

    Callers that rebuild prompts repeatedly should prefer
    build_react_sections + diff_sections + assemble_react_messages so
    unchanged groups stay byte-identical across rebuilds.
    """
    sections = build_react_sections(
        memory_context=memory_context,
        conversation_context=conversation_context,
        available_skill_context=available_skill_context,
        mcp_context=mcp_context,
        compiled_profile=compiled_profile,
        todo_context=todo_context,
        environment_context=environment_context,
        project_instructions=project_instructions,
        append_system_prompt=append_system_prompt,
    )
    return assemble_react_messages(
        system_rules=system_rules,
        tools_desc=tools_desc,
        sections=sections,
        question=question,
        workflow_context=workflow_context,
        include_tools_desc=include_tools_desc,
    )


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
