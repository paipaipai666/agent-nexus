"""Terminal commands exercise the same domain operations as desktop."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def terminal(tmp_path):
    from agentnexus.core.config import get_settings
    from agentnexus.memory.short_term import ShortTermMemory
    from agentnexus.services.chat import ChatService
    from agentnexus.tui.commands import TerminalCommands

    def memory_factory(_sid):
        return lambda: SimpleNamespace(short_term=ShortTermMemory(), estimate_stm_tokens=lambda: 0)

    agent = MagicMock()
    agent.agent_id = "test-agent"
    agent.max_steps = 5
    agent._todo_list = None
    agent._on_event = None
    agent._step_count = 0
    agent._total_usage = {}
    agent.llm_client.model = "test-model"
    agent.llm_client.base_url = ""
    agent.run.return_value = SimpleNamespace(answer="command answer")
    chat = ChatService(lambda _sid: agent, memory_factory)
    session = chat.start_session(workspace=str(tmp_path))
    runtime = SimpleNamespace(chat=chat, settings=get_settings(), llm=MagicMock(), wiki_service=None, capability_runtime=None)
    return TerminalCommands(runtime), session.id


def test_plan_and_thinking_controls_are_session_scoped(terminal):
    commands, sid = terminal
    other = commands.runtime.chat.start_session()
    assert commands.run("/plan", "on", sid).value == {"plan_mode": True}
    assert commands.run("/think", "high", sid).value == {"thinking_effort": "high"}
    assert commands.runtime.chat.is_plan_mode(sid) is True
    assert commands.runtime.chat.is_plan_mode(other.id) is False
    assert commands.runtime.chat.get_thinking_effort(other.id) is None


def test_attachments_use_windows_paths_and_remain_in_their_session(terminal, tmp_path):
    commands, sid = terminal
    file = tmp_path / "中文 file.txt"
    file.write_text("content", encoding="utf-8")
    commands.run("/attach", f'add "{file}"', sid)
    assert commands.run("/attach", "list", sid).value[0]["path"] == str(file.resolve())
    other = commands.runtime.chat.start_session()
    assert commands.run("/attach", "list", other.id).value == []
    commands.run("/attach", "remove 1", sid)
    assert commands.run("/attach", "list", sid).value == []


def test_memory_list_delete_and_clear_use_the_real_store(terminal):
    from agentnexus.memory.long_term import get_long_term_memory

    commands, sid = terminal
    store = get_long_term_memory()
    store.save(sid, "terminal regression memory", category="fact")
    items = commands.run("/memory", "list", sid).value
    assert items[0]["content"] == "terminal regression memory"
    commands.run("/memory", f"delete {items[0]['id']}", sid)
    assert commands.run("/memory", "list", sid).value == []
    store.save(sid, "second memory", category="fact")
    commands.run("/memory", "clear", sid)
    assert commands.run("/memory", "list", sid).value == []


def test_knowledge_list_works_without_downloading_models(terminal):
    commands, sid = terminal
    assert commands.run("/kb", "list", sid).value == {"documents": [], "total_chunks": 0}


def test_wiki_page_and_review_commands_use_persisted_records(terminal):
    from agentnexus.wiki.models import ReviewItem, WikiPage
    from agentnexus.wiki.store import get_wiki_store

    commands, sid = terminal
    store = get_wiki_store()
    store.upsert_page(WikiPage(page_id="tui-page", title="Terminal page", content="Known content", source_namespace="default"))
    store.add_review_item(ReviewItem(item_id="tui-review", page_id="tui-page", description="Check source"))
    assert commands.run("/wiki", "show tui-page", sid).value["content"] == "Known content"
    assert commands.run("/wiki", "reviews", sid).value[0]["description"] == "Check source"
    commands.run("/wiki", "resolve tui-review", sid)
    assert commands.run("/wiki", "reviews", sid).value == []
    commands.run("/wiki", "delete tui-page", sid)
    assert commands.run("/wiki", "list", sid).value == []


def test_audit_commands_show_real_tool_entries(terminal):
    from agentnexus.observability.audit_log import append_audit

    commands, sid = terminal
    append_audit({"tool_name": "tui_fixture", "success": True})
    result = commands.run("/audit", "tui_fixture 5", sid).value
    assert result == {"entries": [{"tool_name": "tui_fixture", "success": True}], "count": 1}


def test_trace_lookup_reads_the_actual_log(terminal):
    import json
    from datetime import date
    from pathlib import Path

    from agentnexus.core.config import get_settings

    commands, sid = terminal
    folder = Path(get_settings().traces_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{date.today()}.jsonl").write_text(json.dumps({"trace_id": "tui-trace", "span_id": "known-span"}) + "\n", encoding="utf-8")
    assert commands.run("/trace", "tui-trace", sid).value == {"trace_id": "tui-trace", "spans": [{"trace_id": "tui-trace", "span_id": "known-span"}]}
    assert commands.run("/logs", "7", sid).value["traces"][0]["trace_id"] == "tui-trace"


def test_evaluation_lists_shipped_suites_and_rejects_missing_tasks(terminal):
    commands, sid = terminal
    suites = commands.run("/eval", "suites", sid).value["suites"]
    assert "coding" in {suite["name"] for suite in suites}
    with pytest.raises(ValueError, match="Task not found"):
        commands.run("/eval", "task tui-nonexistent-task", sid)


def test_settings_commands_preserve_keys_and_switch_models(terminal, tmp_path):
    import json

    commands, sid = terminal
    path = tmp_path / "provider.json"
    path.write_text(json.dumps({"providers": [{"name": "test-provider", "base_url": "http://localhost:12345/v1", "api_key": "fixture-secret", "models": [{"model_id": "test-model"}]}]}), encoding="utf-8")
    assert commands.run("/providers", f'save "{path}"', sid).value == {"status": "updated", "count": 1}
    result = commands.run("/model", "select test-provider/test-model", sid).value
    assert result["active"] == "test-provider/test-model"
    public = commands.run("/settings", "show", sid).value
    assert public["llm_providers"][0]["api_key"] == "****"
    assert "fixture-secret" not in json.dumps(public, default=str)


def test_recovery_exports_saved_result_without_running_again(terminal, tmp_path):
    import json

    commands, sid = terminal
    commands.runtime.chat.send_message(sid, "original request")
    path = tmp_path / "recovery.json"
    commands.run("/recover", f'export "{path}"', sid)
    recovery = json.loads(path.read_text(encoding="utf-8"))
    assert recovery["answer"] == "command answer"
    assert recovery["persisted"] is True
    assert "original request" in commands.run("/recover", "continue", sid).draft
    assert commands.runtime.chat.session_agent(sid).run.call_count == 1


def test_multiline_draft_loads_exact_text_for_submission(terminal, tmp_path):
    commands, sid = terminal
    path = tmp_path / "request.txt"
    path.write_text("first line\n第二行\n", encoding="utf-8")
    commands.run("/draft", f'load "{path}"', sid)
    assert commands.run("/draft", "send", sid).submission["text"] == "first line\n第二行\n"


def test_subagent_reply_preserves_attempt_question_and_request_identity(terminal):
    commands, sid = terminal
    receiver = MagicMock(return_value={"request_id": "request-one", "delivery_state": "queued"})
    commands.runtime.chat.send_subagent_message = receiver
    receipt = commands.run("/subagents", "reply child-one child-attempt 7 request-one clarify this question", sid).value
    assert receipt == {"request_id": "request-one", "delivery_state": "queued"}
    receiver.assert_called_once_with(sid, "child-one", "clarify this question", "request-one", "child-attempt", 7)


def test_timeline_and_context_are_scoped_to_the_target_session(terminal):
    import json

    from agentnexus.observability.timeline import get_timeline_store

    commands, sid = terminal
    store = get_timeline_store()
    store.record_event(sid, "our-run", 1, "tool_start", {"name": "our-tool"}, step_id=1)
    store.record_event("another-session", "their-run", 1, "tool_start", {"name": "their-tool"}, step_id=1)
    store.record_snapshot(sid, "our-run", 1, [{"role": "user", "content": "our context"}])
    timeline = json.dumps(commands.run("/timeline", "", sid).value)
    assert "our-tool" in timeline
    assert "their-tool" not in timeline
    assert commands.run("/context", "our-run 1", sid).value["messages"] == [{"role": "user", "content": "our context"}]


def test_shared_events_preserve_full_tool_result_for_terminal_rendering(terminal):
    from agentnexus.agents.react_types import ReActEvent, ReActEventType

    commands, sid = terminal
    agent = commands.runtime.chat.session_agent(sid)
    original = "header\n" + "result line\n" * 100 + "final result"

    def run(*args, **kwargs):
        agent._on_event(ReActEvent(ReActEventType.TOOL_DONE, {"name": "file_read", "result": original}), None, None)
        return SimpleNamespace(answer="done")

    agent.run.side_effect = run
    run_handle = commands.runtime.chat.send_message(sid, "read")
    events = list(commands.runtime.chat.stream_events(run_handle.id))
    finished = next(event for event in events if event.type == "tool_done")
    assert finished.payload["raw_result"] == original
    assert len(finished.payload["result"]) <= 300


@pytest.mark.parametrize("command", ["/undo", "/redo", "/compact", "/reset"])
def test_history_mutations_reject_an_executing_session(terminal, command):
    commands, sid = terminal
    commands.runtime.chat.mark_processing(True, session_id=sid)
    with pytest.raises(ValueError, match="执行"):
        commands.run(command, "", sid)


def test_undo_restores_the_selected_context_and_its_token_counter(terminal):
    commands, sid = terminal
    memory = commands.runtime.chat.session_memory(sid)
    version = commands.runtime.chat.session_version(sid)
    memory.short_term.append("user", "first message")
    expected_tokens = memory.short_term.estimate_tokens()
    version.commit(stm_snapshot=memory.short_term.to_json(), question="first")
    memory.short_term.append("user", "second message " * 50)
    version.commit(stm_snapshot=memory.short_term.to_json(), question="second")
    commands.run("/undo", "", sid)
    assert [message["content"] for message in memory.short_term.get_all()] == ["first message"]
    assert memory.short_term.estimate_tokens() == expected_tokens


def test_sending_a_draft_consumes_an_independent_attachment_snapshot(terminal, tmp_path):
    commands, sid = terminal
    attachment = tmp_path / "context.txt"
    attachment.write_text("context", encoding="utf-8")
    commands.drafts[sid] = "use this context"
    commands.run("/attach", f'add "{attachment}"', sid)
    first = commands.run("/draft", "send", sid)
    commands.run("/attach", "clear", sid)
    assert first.submission["attachments"] == [{"path": str(attachment), "name": "context.txt", "mime": "text/plain", "size": 7}]
    assert commands.run("/draft", "show", sid).value == {"draft": ""}
    commands.drafts[sid] = "second request"
    assert commands.run("/draft", "send", sid).submission["attachments"] is None


def test_provider_validation_does_not_echo_secret_inputs(terminal, tmp_path):
    commands, sid = terminal
    path = tmp_path / "invalid-providers.json"
    path.write_text('{"providers":[{"api_key":"demo_secret_value"}]}', encoding="utf-8")
    with pytest.raises(ValueError) as error:
        commands.run("/providers", f'save "{path}"', sid)
    assert "demo_secret_value" not in str(error.value)
    assert "base_url" in str(error.value)


def test_drafts_and_attachments_survive_a_new_terminal_controller(terminal, tmp_path):
    from agentnexus.tui.commands import TerminalCommands

    commands, sid = terminal
    request = tmp_path / "draft.txt"
    request.write_text("first line\nsecond line", encoding="utf-8")
    commands.run("/draft", f'load "{request}"', sid)
    commands.run("/attach", f'add "{request}"', sid)
    restored = TerminalCommands(commands.runtime)
    assert restored.run("/draft", "show", sid).value == {"draft": "first line\nsecond line"}
    assert restored.run("/attach", "list", sid).value[0]["name"] == "draft.txt"
    restored.run("/draft", "send", sid)
    assert TerminalCommands(commands.runtime).run("/attach", "list", sid).value == []


def test_session_draft_storage_does_not_retain_a_setting_command(terminal):
    from agentnexus.tui.commands import TerminalCommands

    commands, sid = terminal
    commands.inputs[sid] = "/settings set llm_api_key demo_secret_value"
    commands.save_draft(sid)
    restored = TerminalCommands(commands.runtime)
    restored.load_draft(sid)
    assert restored.inputs[sid] == ""


def test_knowledge_ingestion_progress_and_search_preserve_domain_results(terminal, tmp_path):
    from dataclasses import asdict

    from agentnexus.core.config import get_settings
    from agentnexus.rag.models import IngestionRunRecord, KnowledgeBaseRecord
    from agentnexus.rag.store import get_knowledge_base_catalog

    commands, sid = terminal
    path = tmp_path / "document.txt"
    path.write_text("local source", encoding="utf-8")
    namespace = get_settings().rag_default_namespace
    get_knowledge_base_catalog().upsert_knowledge_base(KnowledgeBaseRecord("test-kb", namespace, "Test", "test-collection"))

    def ingest(source, *, run_id, **kwargs):
        run = IngestionRunRecord(run_id=run_id, kb_id="test-kb", status="completed", source_uri=source, documents_seen=1, chunks_written=3)
        get_knowledge_base_catalog().upsert_ingestion_run(run)
        return None, run

    with patch("agentnexus.rag.kb_service.ingest_one_document", autospec=True, side_effect=ingest) as ingest_document:
        result = commands.run("/kb", f'ingest "{path}"', sid).value
        assert result["status"] == "completed"
        assert result["chunks_written"] == 3
        assert commands.run("/kb", "progress", sid).value == asdict(get_knowledge_base_catalog().get_ingestion_run(result["run_id"]))
        assert ingest_document.call_args.kwargs["namespace"] == namespace
    with patch("agentnexus.rag.kb_service.search_kb", autospec=True, return_value=[{"text": "local source", "score": 0.9}]) as search:
        assert commands.run("/kb", 'search {"query":"local","top_k":3}', sid).value == [{"text": "local source", "score": 0.9}]
        search.assert_called_once_with(query="local", top_k=3, namespace=namespace, view="chunk")
    with patch("agentnexus.rag.kb_service.delete_document_and_vectors", autospec=True, return_value=3):
        assert commands.run("/kb", "delete doc-1", sid).value == {"deleted": "doc-1", "deleted_vectors": 3}


def test_wiki_query_and_file_import_use_the_shared_service(terminal, tmp_path):
    from agentnexus.wiki.wiki_service import WikiQueryResult, WikiService

    commands, sid = terminal
    service = MagicMock(spec=WikiService)
    service.query.return_value = WikiQueryResult(used_wiki=True, answer="verified answer", source_chunks=["chunk-1"])
    from agentnexus.wiki.models import WikiPage

    service.ingest_source.return_value = WikiPage(page_id="imported", title="Imported")
    commands.runtime.wiki_service = service
    result = commands.run("/wiki", "query question", sid).value
    assert result["answer"] == "verified answer"
    assert result["source_chunks"] == ["chunk-1"]
    path = tmp_path / "wiki.txt"
    path.write_text("source text\nsecond line", encoding="utf-8")
    assert commands.run("/wiki", f'ingest "{path}"', sid).value["page_id"] == "imported"
    assert service.ingest_source.call_args.kwargs["source_text"] == "source text\nsecond line"


def test_statistics_health_and_alerts_show_actual_records(terminal):
    from agentnexus.observability.alerting import Alert, AlertSeverity, AlertType, get_alert_manager

    commands, sid = terminal
    manager = get_alert_manager()
    manager.emit(Alert(AlertType.HUMAN_TAKEOVER, AlertSeverity.WARNING, "needs attention"))
    alerts = commands.run("/alerts", "7 warning", sid).value
    assert any(alert["message"] == "needs attention" for alert in alerts)
    assert isinstance(commands.run("/alerts", "rules", sid).value, list)
    stats = commands.run("/stats", "7", sid).value
    assert stats["total_tasks"] == 0
    with patch("agentnexus.server.health_checks.run_health_checks", autospec=True, return_value={"status": "degraded", "checks": [{"component": "llm", "status": "missing"}]}):
        assert commands.run("/health", "", sid).value == {"status": "degraded", "checks": [{"component": "llm", "status": "missing"}]}


def test_evaluation_saves_and_compares_real_baseline_files(terminal):
    from pathlib import Path

    commands, sid = terminal
    report = {"suite_name": "coding", "aggregate": {"pass_rate": 1.0, "avg_score": 0.8}, "task_reports": []}
    with patch("agentnexus.services.eval.EvalService.run_suite", autospec=True, return_value=report) as run_suite:
        assert commands.run("/eval", 'run coding {"n_trials":2,"concurrency":1}', sid).value == report
        assert run_suite.call_args.kwargs == {"n_trials": 2, "concurrency": 1}
        saved = commands.run("/eval", "save coding", sid).value
        assert saved["status"] == "saved"
        assert Path(saved["path"]).is_file()
        assert commands.run("/eval", "baseline coding", sid).value["aggregate"]["avg_score"] == 0.8
        compared = commands.run("/eval", "compare coding", sid).value
        assert compared["pass_rate_diff"] == 0.0
        assert compared["has_regression"] is False
