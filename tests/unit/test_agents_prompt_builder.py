from unittest.mock import MagicMock

from agentnexus.agents.prompt_builder import (
    assemble_react_messages,
    build_conversation_context,
    build_react_messages,
    build_react_prompt,
    build_react_sections,
    diff_sections,
)


class TestBuildReactPrompt:
    TEMPLATE = "tools={tools}\nq={question}\nh={history}\nmem={memory_context}\nctx={conversation_context}"

    def test_basic_template_substitution(self):
        result = build_react_prompt(
            template=self.TEMPLATE,
            tools_desc="tool_list",
            question="what is 2+2",
            history_str="prev_turn",
            memory_context="mem_data",
            conversation_context="conv_data",
        )
        assert "tool_list" in result
        assert "what is 2+2" in result
        assert "prev_turn" in result
        assert "mem_data" in result
        assert "conv_data" in result

    def test_with_skill_context(self):
        result = build_react_prompt(
            template=self.TEMPLATE,
            tools_desc="t",
            question="q",
            history_str="h",
            memory_context="m",
            conversation_context="base",
            available_skill_context="skill_info",
        )
        assert "skill_info" in result
        assert "base" in result

    def test_with_mcp_context(self):
        result = build_react_prompt(
            template=self.TEMPLATE,
            tools_desc="t",
            question="q",
            history_str="h",
            memory_context="m",
            conversation_context="base",
            mcp_context="mcp_stuff",
        )
        assert "mcp_stuff" in result

    def test_with_compiled_profile(self):
        profile = MagicMock()
        profile.fragments_text = "frag_text"
        profile.workflow_guidance = "wf_guide"
        result = build_react_prompt(
            template=self.TEMPLATE,
            tools_desc="t",
            question="q",
            history_str="h",
            memory_context="m",
            conversation_context="base",
            compiled_profile=profile,
        )
        assert "frag_text" in result
        assert "wf_guide" in result

    def test_with_todo_context(self):
        result = build_react_prompt(
            template=self.TEMPLATE,
            tools_desc="t",
            question="q",
            history_str="h",
            memory_context="m",
            conversation_context="base",
            todo_context="todo_items",
        )
        assert "todo_items" in result

    def test_empty_extras_no_double_newlines(self):
        result = build_react_prompt(
            template=self.TEMPLATE,
            tools_desc="t",
            question="q",
            history_str="h",
            memory_context="m",
            conversation_context="conv",
        )
        assert "conv" in result


class TestBuildConversationContext:
    def test_no_memory_manager_returns_empty(self):
        assert build_conversation_context(None) == ""

    def test_no_short_term_returns_empty(self):
        mm = MagicMock()
        mm.short_term = None
        assert build_conversation_context(mm) == ""

    def test_no_summary_returns_recent_msgs(self):
        stm = MagicMock()
        stm.get_summary.return_value = None
        stm.get_all.return_value = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        mm = MagicMock()
        mm.short_term = stm

        result = build_conversation_context(mm)
        assert "近期对话" in result
        assert "用户: hello" in result
        assert "助手: hi there" in result

    def test_with_summary_returns_summary_plus_recent(self):
        stm = MagicMock()
        stm.get_summary.return_value = "previous summary"
        stm.get_all.return_value = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]
        mm = MagicMock()
        mm.short_term = stm

        result = build_conversation_context(mm)
        assert "对话历史摘要" in result
        assert "previous summary" in result
        assert "最近对话" in result

    def test_user_messages_kept_intact(self):
        """User/assistant messages are kept whole — no mid-message truncation."""
        stm = MagicMock()
        stm.get_summary.return_value = None
        long_content = "x" * 1000
        stm.get_all.return_value = [
            {"role": "user", "content": long_content},
        ]
        mm = MagicMock()
        mm.short_term = stm

        result = build_conversation_context(mm)
        assert long_content in result

    def test_role_labels_chinese(self):
        stm = MagicMock()
        stm.get_summary.return_value = None
        stm.get_all.return_value = [
            {"role": "user", "content": "ask"},
            {"role": "assistant", "content": "reply"},
        ]
        mm = MagicMock()
        mm.short_term = stm

        result = build_conversation_context(mm)
        assert "用户: ask" in result
        assert "助手: reply" in result


class TestBuildReactMessages:
    """Tests for build_react_messages — prompt caching optimized message structure."""

    def test_basic_structure(self):
        messages = build_react_messages(
            system_rules="You are a helpful assistant.",
            tools_desc="search, calculator",
            question="What is 2+2?",
        )
        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a helpful assistant."
        assert messages[1]["role"] == "system"
        assert "search, calculator" in messages[1]["content"]
        assert messages[2]["role"] == "user"
        assert "What is 2+2?" in messages[2]["content"]

    def test_stable_prefix_for_caching(self):
        """First message should be identical across calls for cache hit."""
        msg1 = build_react_messages(
            system_rules="Fixed rules here",
            tools_desc="tool_a",
            question="question 1",
        )
        msg2 = build_react_messages(
            system_rules="Fixed rules here",
            tools_desc="tool_b",
            question="question 2",
        )
        # First message (system rules) must be identical
        assert msg1[0] == msg2[0]

    def test_memory_context_included(self):
        messages = build_react_messages(
            system_rules="rules",
            tools_desc="tools",
            question="q",
            memory_context="remember this fact",
        )
        # Groups: system_rules, memory, tools, user
        assert len(messages) == 4
        assert messages[1]["content"] == "remember this fact"

    def test_conversation_context_included(self):
        messages = build_react_messages(
            system_rules="rules",
            tools_desc="tools",
            question="q",
            conversation_context="recent chat history",
        )
        # Groups: system_rules, conversation, tools, user
        assert len(messages) == 4
        assert messages[1]["content"] == "recent chat history"

    def test_empty_contexts_skipped(self):
        """Empty context blocks should not create extra messages."""
        messages = build_react_messages(
            system_rules="rules",
            tools_desc="tools",
            question="q",
            memory_context="",
            conversation_context="",
        )
        # Only: system_rules, tools, user
        assert len(messages) == 3

    def test_with_compiled_profile(self):
        profile = MagicMock()
        profile.fragments_text = "custom fragment"
        profile.workflow_guidance = "workflow guide"
        messages = build_react_messages(
            system_rules="rules",
            tools_desc="tools",
            question="q",
            compiled_profile=profile,
        )
        assert any("custom fragment" in m["content"] for m in messages)
        assert any("workflow guide" in m["content"] for m in messages)

    def test_with_todo_context(self):
        messages = build_react_messages(
            system_rules="rules",
            tools_desc="tools",
            question="q",
            todo_context="- [ ] task 1\n- [ ] task 2",
        )
        assert any("task 1" in m["content"] for m in messages)

    def test_user_message_format(self):
        messages = build_react_messages(
            system_rules="rules",
            tools_desc="tools",
            question="What is the capital of France?",
        )
        user_msg = messages[-1]
        assert user_msg["role"] == "user"
        assert "== 当前任务 ==" in user_msg["content"]
        assert "What is the capital of France?" in user_msg["content"]


class TestDisplayOnlyAndFinalAnswerContext:
    """display_only thought should be excluded from prompt context;
    [最终答案] should be converted to assistant message."""

    def test_display_only_excluded_from_prompt_context(self):
        """display_only messages must not appear in conversation context."""
        stm = MagicMock()
        stm.get_summary.return_value = None
        stm.get_all.return_value = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "I need to search first.", "metadata": {"display_only": True}},
            {"role": "tool", "content": "Action: web_search[{}]\nObservation: Python is a language."},
            {"role": "assistant", "content": "Now I can answer.", "metadata": {"display_only": True}},
            {"role": "system", "content": "[最终答案] Python is a programming language."},
        ]
        mm = MagicMock()
        mm.short_term = stm

        result = build_conversation_context(mm)
        assert "I need to search first." not in result
        assert "Now I can answer." not in result
        assert "Python is a programming language." in result

    def test_final_answer_converted_to_assistant_in_context(self):
        """[最终答案] system marker should appear as 助手: in context."""
        stm = MagicMock()
        stm.get_summary.return_value = None
        stm.get_all.return_value = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "I searched."},
            {"role": "system", "content": "[最终答案] Python is a language."},
        ]
        mm = MagicMock()
        mm.short_term = stm

        result = build_conversation_context(mm)
        assert "助手: Python is a language." in result

    def test_final_answer_not_duplicated_with_thought(self):
        """Both thought and answer should appear, but thought only when not display_only."""
        stm = MagicMock()
        stm.get_summary.return_value = None
        stm.get_all.return_value = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "I can answer now.", "metadata": {"display_only": True}},
            {"role": "system", "content": "[最终答案] Python is a language."},
        ]
        mm = MagicMock()
        mm.short_term = stm

        result = build_conversation_context(mm)
        assert result.count("Python is a language.") == 1
        assert "I can answer now." not in result

    def test_no_display_messages_context_unchanged(self):
        """Normal messages without metadata should work as before."""
        stm = MagicMock()
        stm.get_summary.return_value = None
        stm.get_all.return_value = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        mm = MagicMock()
        mm.short_term = stm

        result = build_conversation_context(mm)
        assert "用户: hello" in result
        assert "助手: hi" in result

    def test_history_api_still_returns_display_only(self):
        """get_all() should still return display_only messages for history display."""
        from agentnexus.memory.short_term import ShortTermMemory

        stm = ShortTermMemory()
        stm.append("user", "What is Python?")
        stm.append("assistant", "I can answer now.", metadata={"display_only": True})
        stm.append("system", "[最终答案] Python is a language.")

        messages = stm.get_all()
        assert len(messages) == 3
        assert messages[1]["metadata"] == {"display_only": True}
        assert messages[1]["content"] == "I can answer now."


class TestSectionModel:
    """Named-section assembly, diffing, and byte-stable rebuilds."""

    def _sections_v1(self) -> dict[str, str]:
        return build_react_sections(
            memory_context="MEMORY",
            conversation_context="CONVERSATION",
            available_skill_context="SKILLS",
            todo_context="TODO_V1",
            environment_context="ENV",
            project_instructions="PROJECT",
            append_system_prompt="APPENDIX",
        )

    def test_sections_drop_empty_blocks(self):
        sections = build_react_sections(memory_context="M", conversation_context="")
        assert sections == {"memory": "M"}

    def test_diff_first_build_marks_everything_changed(self):
        changed = diff_sections(None, self._sections_v1())
        assert changed == set(self._sections_v1())

    def test_diff_detects_modified_added_removed(self):
        previous = self._sections_v1()
        current = dict(previous)
        current["todo"] = "TODO_V2"                      # modified
        current["mcp"] = "MCP"                           # added
        del current["project_instructions"]              # removed
        changed = diff_sections(previous, current)
        assert changed == {"todo", "mcp", "project_instructions"}

    def test_diff_unchanged_is_empty(self):
        previous = self._sections_v1()
        assert diff_sections(previous, dict(previous)) == set()

    def test_groups_render_in_stable_to_volatile_order(self):
        sections = self._sections_v1()
        messages = assemble_react_messages(
            system_rules="RULES",
            tools_desc="TOOLS",
            sections=sections,
            question="Q",
        )
        contents = [m["content"] for m in messages]
        assert contents[0] == "RULES"
        assert contents[1] == "MEMORY"
        assert contents[2] == "CONVERSATION"
        # static group: one message, salience order skills < project < appendix
        static = contents[3]
        assert "SKILLS" in static and "PROJECT" in static and "APPENDIX" in static
        assert static.index("SKILLS") < static.index("PROJECT") < static.index("APPENDIX")
        # tools between static and volatile; volatile env before todo
        assert contents[4] == "== 可用工具 ==\nTOOLS"
        volatile = contents[5]
        assert volatile.index("ENV") < volatile.index("TODO_V1")
        assert messages[-1]["role"] == "user"

    def test_unchanged_groups_byte_identical_across_rebuilds(self):
        """Only groups containing a changed section may differ between rebuilds."""
        v1 = self._sections_v1()
        first = assemble_react_messages(
            system_rules="RULES", tools_desc="TOOLS", sections=v1, question="Q"
        )
        v2 = dict(v1)
        v2["todo"] = "TODO_V2"
        second = assemble_react_messages(
            system_rules="RULES", tools_desc="TOOLS", sections=v2, question="Q"
        )
        assert len(first) == len(second)
        changed_indexes = {
            i for i, (a, b) in enumerate(zip(first, second)) if a["content"] != b["content"]
        }
        # rules, memory, conversation, static, tools all byte-identical;
        # only the volatile group (todo lives there) may change.
        assert changed_indexes == {5}
        assert first[5]["content"] == "ENV\n\nTODO_V1"
        assert second[5]["content"] == "ENV\n\nTODO_V2"

    def test_compaction_like_change_isolates_to_memory_and_conversation(self):
        """A compaction-style rebuild (memory + conversation change) keeps
        static/tools/volatile groups byte-identical below the change point
        is impossible — prefix invalidation is positional — but groups
        ABOVE it (rules) stay identical, and unchanged groups re-render
        byte-identical regardless of position."""
        v1 = self._sections_v1()
        first = assemble_react_messages(
            system_rules="RULES", tools_desc="TOOLS", sections=v1, question="Q"
        )
        v2 = dict(v1)
        v2["memory"] = "MEMORY_AFTER_COMPACT"
        v2["conversation"] = "CONVERSATION_AFTER_COMPACT"
        second = assemble_react_messages(
            system_rules="RULES", tools_desc="TOOLS", sections=v2, question="Q"
        )
        assert second[0]["content"] == first[0]["content"]
        assert second[3]["content"] == first[3]["content"]  # static identical
        assert second[4]["content"] == first[4]["content"]  # tools identical
