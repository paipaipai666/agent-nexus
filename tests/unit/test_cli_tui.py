from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from agentnexus.cli import app

runner = CliRunner()


class TestTuiCommand:
    def test_tui_launch(self):
        mock_llm = MagicMock()
        mock_executor = MagicMock()
        mock_confirm = MagicMock()
        mock_settings = MagicMock()
        mock_settings.memory_db_path = "/tmp/test.db"
        mock_settings.traces_dir = "/tmp/traces"
        mock_memory = MagicMock()
        mock_version = MagicMock()
        mock_agent = MagicMock()
        mock_tui_app = MagicMock()
        mock_trace = MagicMock()
        mock_skill_service = MagicMock()

        with patch("agentnexus.core.llm.AgentLLM", return_value=mock_llm), \
             patch("agentnexus.tools.tool_executor.ToolExecutor", return_value=mock_executor), \
             patch("agentnexus.tools.confirm_bridge.ConfirmBridge", return_value=mock_confirm), \
             patch("agentnexus.tools.mcp_adapter.create_mcp_manager_from_settings", return_value=None), \
             patch("agentnexus.tools.register_all_tools") as mock_reg, \
             patch("agentnexus.memory.manager.MemoryManager", return_value=mock_memory), \
             patch("agentnexus.memory.versioned.ConversationVersionManager", return_value=mock_version), \
             patch("agentnexus.agents.re_act_agent.ReActAgent", return_value=mock_agent), \
             patch("agentnexus.tui.app.AgentNexusTUI", return_value=mock_tui_app) as mock_tui_cls, \
             patch("agentnexus.core.config.get_settings", return_value=mock_settings), \
             patch("agentnexus.observability.tracer.trace_manager", mock_trace):

            result = runner.invoke(app, ["tui"])
            assert result.exit_code == 0
            mock_tui_app.run.assert_called_once()
            mock_reg.assert_called_once_with(
                mock_executor, llm_client=mock_llm, subagent_confirm=mock_confirm, mcp_manager=None
            )
            mock_tui_cls.assert_called_once_with(
                agent=mock_agent,
                memory=mock_memory,
                version=mock_version,
                mcp_manager=None,
            )
            mock_trace.configure.assert_called_once_with("/tmp/traces")

    def test_tui_sets_subagent_confirm(self):
        mock_llm = MagicMock()
        mock_executor = MagicMock()
        mock_confirm = MagicMock()
        mock_settings = MagicMock()
        mock_settings.memory_db_path = "/tmp/test.db"
        mock_settings.traces_dir = "/tmp/traces"
        mock_memory = MagicMock()
        mock_version = MagicMock()
        mock_agent = MagicMock()
        mock_tui_app = MagicMock()

        with patch("agentnexus.core.llm.AgentLLM", return_value=mock_llm), \
             patch("agentnexus.tools.tool_executor.ToolExecutor", return_value=mock_executor), \
             patch("agentnexus.tools.confirm_bridge.ConfirmBridge", return_value=mock_confirm), \
             patch("agentnexus.tools.register_all_tools"), \
             patch("agentnexus.memory.manager.MemoryManager", return_value=mock_memory), \
             patch("agentnexus.memory.versioned.ConversationVersionManager", return_value=mock_version), \
             patch("agentnexus.agents.re_act_agent.ReActAgent", return_value=mock_agent), \
             patch("agentnexus.tui.app.AgentNexusTUI", return_value=mock_tui_app), \
             patch("agentnexus.core.config.get_settings", return_value=mock_settings), \
             patch("agentnexus.observability.tracer.trace_manager"):

            runner.invoke(app, ["tui"])
            assert mock_tui_app._subagent_confirm == mock_confirm

    def test_tui_creates_versioned_session_id(self):
        mock_llm = MagicMock()
        mock_executor = MagicMock()
        mock_confirm = MagicMock()
        mock_settings = MagicMock()
        mock_settings.memory_db_path = "/tmp/test.db"
        mock_settings.traces_dir = "/tmp/traces"
        mock_memory = MagicMock()
        mock_version = MagicMock()
        mock_agent = MagicMock()
        mock_tui_app = MagicMock()

        with patch("agentnexus.core.llm.AgentLLM", return_value=mock_llm), \
             patch("agentnexus.tools.tool_executor.ToolExecutor", return_value=mock_executor), \
             patch("agentnexus.tools.confirm_bridge.ConfirmBridge", return_value=mock_confirm), \
             patch("agentnexus.tools.register_all_tools"), \
             patch("agentnexus.memory.manager.MemoryManager", return_value=mock_memory) as mock_mm, \
             patch("agentnexus.memory.versioned.ConversationVersionManager", return_value=mock_version) as mock_cv, \
             patch("agentnexus.agents.re_act_agent.ReActAgent", return_value=mock_agent), \
             patch("agentnexus.tui.app.AgentNexusTUI", return_value=mock_tui_app), \
             patch("agentnexus.core.config.get_settings", return_value=mock_settings), \
             patch("agentnexus.observability.tracer.trace_manager"):

            runner.invoke(app, ["tui"])
            session_id = mock_mm.call_args[0][0]
            assert session_id.startswith("tui_")
            assert len(session_id) == 4 + 12
            mock_cv.assert_called_once_with(session_id, mock_settings.memory_db_path)

    def test_tui_creates_react_agent_with_conversation_mode(self):
        mock_llm = MagicMock()
        mock_executor = MagicMock()
        mock_confirm = MagicMock()
        mock_settings = MagicMock()
        mock_settings.memory_db_path = "/tmp/test.db"
        mock_settings.traces_dir = "/tmp/traces"
        mock_memory = MagicMock()
        mock_version = MagicMock()
        mock_agent = MagicMock()
        mock_tui_app = MagicMock()

        with patch("agentnexus.core.llm.AgentLLM", return_value=mock_llm), \
             patch("agentnexus.tools.tool_executor.ToolExecutor", return_value=mock_executor), \
             patch("agentnexus.tools.confirm_bridge.ConfirmBridge", return_value=mock_confirm), \
             patch("agentnexus.tools.mcp_adapter.create_mcp_manager_from_settings", return_value=None), \
             patch("agentnexus.tools.register_all_tools"), \
             patch("agentnexus.memory.manager.MemoryManager", return_value=mock_memory), \
             patch("agentnexus.memory.versioned.ConversationVersionManager", return_value=mock_version), \
             patch("agentnexus.agents.re_act_agent.ReActAgent", return_value=mock_agent) as mock_ra, \
             patch("agentnexus.tui.app.AgentNexusTUI", return_value=mock_tui_app), \
             patch("agentnexus.core.config.get_settings", return_value=mock_settings), \
             patch("agentnexus.observability.tracer.trace_manager"):

            runner.invoke(app, ["tui"])
            mock_ra.assert_called_once_with(mock_llm, mock_executor, conversation_mode=True)

    def test_tui_closes_mcp_manager(self):
        mock_llm = MagicMock()
        mock_executor = MagicMock()
        mock_confirm = MagicMock()
        mock_settings = MagicMock()
        mock_settings.memory_db_path = "/tmp/test.db"
        mock_settings.traces_dir = "/tmp/traces"
        mock_memory = MagicMock()
        mock_version = MagicMock()
        mock_agent = MagicMock()
        mock_tui_app = MagicMock()
        mock_manager = MagicMock()

        with patch("agentnexus.core.llm.AgentLLM", return_value=mock_llm), \
             patch("agentnexus.tools.tool_executor.ToolExecutor", return_value=mock_executor), \
             patch("agentnexus.tools.confirm_bridge.ConfirmBridge", return_value=mock_confirm), \
             patch("agentnexus.tools.mcp_adapter.create_mcp_manager_from_settings", return_value=mock_manager), \
             patch("agentnexus.tools.register_all_tools"), \
             patch("agentnexus.memory.manager.MemoryManager", return_value=mock_memory), \
             patch("agentnexus.memory.versioned.ConversationVersionManager", return_value=mock_version), \
             patch("agentnexus.agents.re_act_agent.ReActAgent", return_value=mock_agent), \
             patch("agentnexus.tui.app.AgentNexusTUI", return_value=mock_tui_app) as mock_tui_cls, \
             patch("agentnexus.core.config.get_settings", return_value=mock_settings), \
             patch("agentnexus.observability.tracer.trace_manager"):

            runner.invoke(app, ["tui"])
            mock_tui_cls.assert_called_once_with(
                agent=mock_agent,
                memory=mock_memory,
                version=mock_version,
                mcp_manager=mock_manager,
            )
            mock_manager.close.assert_called_once()
