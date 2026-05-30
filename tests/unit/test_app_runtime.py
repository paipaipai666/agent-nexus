from unittest.mock import ANY, MagicMock, patch


def test_runtime_build_assembles_core_services():
    mock_settings = MagicMock()
    mock_settings.memory_db_path = "/tmp/memory.db"
    mock_settings.traces_dir = "/tmp/traces"
    mock_settings.extensions_dirs = []
    mock_settings.extensions_enabled = True
    mock_settings.default_skill = "review/code_review"

    with patch("agentnexus.core.config.get_settings", return_value=mock_settings), \
         patch("agentnexus.core.llm.AgentLLM", return_value=MagicMock()), \
         patch("agentnexus.tools.tool_executor.ToolRegistry", return_value=MagicMock()), \
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


def test_runtime_build_uses_supplied_session_id_and_restores_stm():
    mock_settings = MagicMock()
    mock_settings.memory_db_path = "/tmp/memory.db"
    mock_settings.traces_dir = "/tmp/traces"
    mock_settings.extensions_dirs = []
    mock_settings.extensions_enabled = True
    mock_settings.default_skill = ""

    from agentnexus.memory.short_term import ShortTermMemory

    stm = ShortTermMemory()
    stm.append("user", "previous question")
    snapshot = stm.to_json()

    memory = MagicMock()
    memory.short_term = ShortTermMemory()
    version = MagicMock()
    version.get_head_stm.return_value = snapshot

    with patch("agentnexus.core.config.get_settings", return_value=mock_settings), \
         patch("agentnexus.core.llm.AgentLLM", return_value=MagicMock()), \
         patch("agentnexus.tools.tool_executor.ToolRegistry", return_value=MagicMock()), \
         patch("agentnexus.tools.confirm_bridge.ConfirmBridge", return_value=MagicMock()), \
         patch("agentnexus.tools.mcp_adapter.create_mcp_manager_from_settings", return_value=None), \
         patch("agentnexus.tools.register_all_tools"), \
         patch("agentnexus.memory.manager.MemoryManager", return_value=memory) as mock_memory_cls, \
         patch("agentnexus.memory.versioned.ConversationVersionManager", return_value=version) as mock_version_cls, \
         patch("agentnexus.agents.re_act_agent.ReActAgent", return_value=MagicMock()), \
         patch("agentnexus.skills.SkillRegistry.from_settings") as mock_skill_registry, \
         patch("agentnexus.observability.tracer.trace_manager"):
        registry = MagicMock()
        registry.discover.return_value = []
        mock_skill_registry.return_value = registry
        from agentnexus.app import AppRuntime

        runtime = AppRuntime.build(profile="tui", session_id="tui_existing", restore_session=True)

    assert runtime.session_id == "tui_existing"
    mock_memory_cls.assert_called_once_with("tui_existing", llm=ANY)
    mock_version_cls.assert_called_once_with(
        "tui_existing",
        "/tmp/memory.db",
        workspace_path=ANY,
        profile="tui",
    )
    assert memory.short_term.get_all()[0]["content"] == "previous question"


def test_close_method(mocker):
    """AppRuntime.close() calls mcp_manager.close() if manager exists."""
    from agentnexus.app import AppRuntime

    mock_mcp = mocker.MagicMock()
    runtime = AppRuntime(
        settings=mocker.MagicMock(),
        llm=mocker.MagicMock(),
        executor=mocker.MagicMock(),
        agent=mocker.MagicMock(),
        memory_manager=mocker.MagicMock(),
        version_manager=mocker.MagicMock(),
        mcp_manager=mock_mcp,
        extension_manager=mocker.MagicMock(),
        capability_runtime=mocker.MagicMock(),
        services=mocker.MagicMock(),
        subagent_confirm=mocker.MagicMock(),
        session_id="test-close",
    )
    runtime.close()
    mock_mcp.close.assert_called_once()


def test_close_method_no_mcp(mocker):
    """AppRuntime.close() is noop when mcp_manager is None."""
    from agentnexus.app import AppRuntime

    runtime = AppRuntime(
        settings=mocker.MagicMock(),
        llm=mocker.MagicMock(),
        executor=mocker.MagicMock(),
        agent=mocker.MagicMock(),
        memory_manager=mocker.MagicMock(),
        version_manager=mocker.MagicMock(),
        mcp_manager=None,
        extension_manager=mocker.MagicMock(),
        capability_runtime=mocker.MagicMock(),
        services=mocker.MagicMock(),
        subagent_confirm=mocker.MagicMock(),
        session_id="test-close-noop",
    )
    runtime.close()
