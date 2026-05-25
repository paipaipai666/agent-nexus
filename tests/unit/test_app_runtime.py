from unittest.mock import MagicMock, patch


def test_runtime_build_assembles_core_services():
    mock_settings = MagicMock()
    mock_settings.memory_db_path = "/tmp/memory.db"
    mock_settings.traces_dir = "/tmp/traces"
    mock_settings.extensions_dirs = []
    mock_settings.extensions_enabled = True
    mock_settings.default_skill = "review/code_review"

    with patch("agentnexus.core.config.get_settings", return_value=mock_settings), \
         patch("agentnexus.core.llm.AgentLLM", return_value=MagicMock()), \
         patch("agentnexus.tools.tool_executor.ToolExecutor", return_value=MagicMock()), \
         patch("agentnexus.tools.confirm_bridge.ConfirmBridge", return_value=MagicMock()), \
         patch("agentnexus.tools.mcp_adapter.create_mcp_manager_from_settings", return_value=None), \
         patch("agentnexus.tools.register_all_tools"), \
         patch("agentnexus.memory.manager.MemoryManager", return_value=MagicMock()), \
         patch("agentnexus.memory.versioned.ConversationVersionManager", return_value=MagicMock()), \
         patch("agentnexus.agents.re_act_agent.ReActAgent", return_value=MagicMock()), \
         patch("agentnexus.skills.SkillRegistry.from_settings") as mock_skill_registry, \
         patch("agentnexus.observability.tracer.trace_manager") as mock_trace:
        registry = MagicMock()
        registry.discover.return_value = []
        mock_skill_registry.return_value = registry
        from agentnexus.app import AppRuntime

        runtime = AppRuntime.build(profile="tui")

    assert runtime.session_id.startswith("tui_")
    assert runtime.services.chat is not None
    assert runtime.services.skill is not None
    assert runtime.services.knowledge_base is not None
    assert runtime.services.eval is not None
    assert runtime.services.config is not None
    mock_trace.configure.assert_called_once_with("/tmp/traces")
