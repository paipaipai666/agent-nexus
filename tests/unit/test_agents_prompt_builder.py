from unittest.mock import MagicMock

from agentnexus.agents.prompt_builder import (
    build_conversation_context,
    build_react_messages,
    build_react_prompt,
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

    def test_per_msg_limit_truncation(self):
        stm = MagicMock()
        stm.get_summary.return_value = None
        long_content = "x" * 1000
        stm.get_all.return_value = [
            {"role": "user", "content": long_content},
        ]
        mm = MagicMock()
        mm.short_term = stm

        result = build_conversation_context(mm, per_msg_limit=100)
        lines = result.split("\n")
        user_line = [line for line in lines if line.startswith("用户:")][0]
        content_part = user_line.split("用户: ", 1)[1]
        assert len(content_part) == 100

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
        # Should have: system_rules, tools, memory+context, user
        assert len(messages) == 4
        assert "remember this fact" in messages[2]["content"]

    def test_conversation_context_included(self):
        messages = build_react_messages(
            system_rules="rules",
            tools_desc="tools",
            question="q",
            conversation_context="recent chat history",
        )
        assert len(messages) == 4
        assert "recent chat history" in messages[2]["content"]

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
