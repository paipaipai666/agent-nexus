from unittest.mock import MagicMock

from agentnexus.agents.prompt_builder import build_conversation_context, build_react_prompt


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
        user_line = [l for l in lines if l.startswith("用户:")][0]
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
