from unittest.mock import MagicMock

from agentnexus.services.skill import SkillService
from agentnexus.skills.registry import SkillEntry, SkillRegistry
from agentnexus.skills.router import SkillRouter, SkillRouterIndex, _parse_llm_skill_id, _tokenize
from agentnexus.skills.workflow import Workflow


def _entry():
    workflow = Workflow.model_validate({
        "id": "code_review",
        "version": "1",
        "display_name": "Code Review",
        "description": "Review code",
        "prompt_profile": {"system": "react"},
        "tool_policy": {"max_risk": "low"},
        "steps": [{"type": "prompt", "id": "inspect", "prompt": "Inspect."}],
        "success_criteria": ["Done."],
        "resources": [
            {"type": "script", "path": "scripts/check.py", "name": "check.py", "size_bytes": 1},
            {"type": "reference", "path": "references/policy.md", "name": "policy.md", "size_bytes": 1},
            {"type": "asset", "path": "assets/template.txt", "name": "template.txt", "size_bytes": 1},
        ],
    })
    return SkillEntry("review", "code_review", "Code Review", "Review code", MagicMock(), workflow)


def _skill_entry(skill_id: str, name: str, description: str):
    workflow = Workflow.model_validate({
        "id": skill_id,
        "version": "1",
        "display_name": name,
        "description": description,
        "prompt_profile": {"system": "react"},
        "tool_policy": {"max_risk": "low"},
        "steps": [{"type": "prompt", "id": "guide", "prompt": f"Use {name}."}],
        "success_criteria": ["Done."],
    })
    return SkillEntry("default", skill_id, name, description, MagicMock(), workflow, source_kind="skill")


def test_skill_service_use_applies_agent_profile():
    entry = _entry()
    registry = SkillRegistry([])
    registry._entries = [entry]
    agent = MagicMock()
    service = SkillService(registry, agent=agent)

    result = service.use("code_review")

    assert result == entry
    assert service.current == entry
    assert service.snapshot().status == "selected"
    assert service.snapshot().scripts == 1
    assert service.snapshot().references == 1
    assert service.snapshot().assets == 1
    agent.set_session_profile.assert_called_once()


def test_skill_service_reset_clears_agent_profile():
    service = SkillService(SkillRegistry([]), agent=MagicMock())
    service.reset()
    assert service.current is None
    assert service.snapshot().current == "default/default"
    service.agent.set_session_profile.assert_called_once_with(None)


def test_skill_service_validate_updates_status():
    registry = MagicMock()
    registry.validate.return_value = ["bad"]
    registry.list.return_value = []
    registry.errors = []
    service = SkillService(registry)

    errors = service.validate()

    assert errors == ["bad"]
    assert service.snapshot().status == "error"


def test_skill_service_use_default_records_error_without_raising():
    registry = SkillRegistry([])
    service = SkillService(registry, agent=MagicMock())
    result = service.use_default("missing")
    assert result is None
    assert service.snapshot().status == "error"
    assert registry.errors


def test_skill_service_use_default_success_applies_profile():
    entry = _entry()
    registry = SkillRegistry([])
    registry._entries = [entry]
    agent = MagicMock()
    service = SkillService(registry, agent=agent)

    result = service.use_default("review/code_review")

    assert result == entry
    assert service.current == entry
    assert service.snapshot().status == "selected"
    agent.set_session_profile.assert_called_once()


def test_skill_service_prepare_message_records_last_run():
    entry = _entry()
    registry = SkillRegistry([])
    registry._entries = [entry]
    service = SkillService(registry, agent=MagicMock())
    service.use("review/code_review")

    result = service.prepare_message("hello")
    snapshot = service.snapshot()

    assert "Workflow Runtime Context" in result.enhanced_question
    assert snapshot.last_run_status == "completed"
    assert snapshot.step_count == 1
    assert snapshot.ok_steps == 1
    assert snapshot.last_run_id.startswith("workflow_")


def test_skill_service_use_validation_error_does_not_select():
    entry = _entry()
    entry.workflow.prompt_profile.fragments = ["missing_fragment"]
    registry = SkillRegistry([])
    registry._entries = [entry]
    agent = MagicMock()
    service = SkillService(registry, agent=agent)

    try:
        service.use("review/code_review")
    except ValueError as exc:
        assert "Prompt fragment not found" in str(exc)
    else:
        raise AssertionError("expected validation error")

    assert service.current is None
    assert service.snapshot().status == "error"
    agent.set_session_profile.assert_not_called()


def test_skill_service_auto_selects_matching_skill():
    entry = _skill_entry("draft-writer", "Draft Writer", "Write concise product release notes and drafts.")
    registry = SkillRegistry([])
    registry._entries = [entry]
    service = SkillService(registry, agent=MagicMock())

    route = service.maybe_auto_select("Please write concise release notes for this product.")

    assert route is not None
    assert service.current == entry
    snapshot = service.snapshot()
    assert snapshot.current == "default/draft-writer"
    assert snapshot.auto_route_reason
    assert snapshot.auto_route_score >= 2.0
    assert snapshot.auto_route_source == "deterministic"


def test_skill_router_builds_cached_index_and_routes_from_it():
    entry = _skill_entry("draft-writer", "Draft Writer", "Write concise product release notes and drafts.")
    router = SkillRouter()
    router.rebuild([entry])

    route = router.decide_indexed("Please write concise release notes.").route

    assert route is not None
    assert route.entry == entry
    assert router.index.signature == (
        "default/draft-writer\0Draft Writer\0Write concise product release notes and drafts.",
    )


def test_skill_router_idf_downweights_common_terms():
    common = _skill_entry("common", "Common", "Handle shared release notes tasks.")
    specific = _skill_entry("security-review", "Security Review", "Handle shared security audit tasks.")
    index = SkillRouterIndex.build([common, specific])

    assert index.idf["security"] > index.idf["shared"]


def test_skill_router_tokenize_splits_mixed_chinese_english():
    tokens = _tokenize("生成一份word文档 docx格式")

    assert "word" in tokens
    assert "docx" in tokens


def test_skill_service_auto_route_does_not_override_manual_skill():
    manual = _skill_entry("manual", "Manual Skill", "Handle manual tasks.")
    auto = _skill_entry("draft-writer", "Draft Writer", "Write concise product release notes and drafts.")
    registry = SkillRegistry([])
    registry._entries = [manual, auto]
    service = SkillService(registry, agent=MagicMock())
    service.use("manual")

    route = service.maybe_auto_select("Please write concise release notes.")

    assert route is None
    assert service.current == manual


def test_skill_service_refresh_rebuilds_router_index():
    entry = _skill_entry("draft-writer", "Draft Writer", "Write concise release notes.")
    registry = SkillRegistry([])
    registry._entries = []
    service = SkillService(registry, agent=MagicMock())
    assert service.router.index.items == ()

    registry.discover = MagicMock(return_value=[entry])
    registry._entries = [entry]
    service.refresh()

    assert len(service.router.index.items) == 1
    assert service.router.index.items[0].entry == entry


def test_skill_service_available_skill_context_lists_metadata():
    entry = _skill_entry("docx", "DOCX", "Create and edit Word documents.")
    registry = SkillRegistry([])
    registry._entries = [entry]
    service = SkillService(registry, agent=MagicMock())

    context = service.available_skill_context()

    assert "Available Skills" in context
    assert "default/docx" in context
    assert "Create and edit Word documents" in context


def test_skill_service_auto_route_ignores_ambiguous_matches():
    first = _skill_entry("draft-one", "Draft One", "Write concise release notes.")
    second = _skill_entry("draft-two", "Draft Two", "Write concise release notes.")
    registry = SkillRegistry([])
    registry._entries = [first, second]
    service = SkillService(registry, agent=MagicMock())

    route = service.maybe_auto_select("Write concise release notes.")

    assert route is None
    assert service.current is None


def test_skill_service_llm_fallback_resolves_ambiguous_route():
    first = _skill_entry("draft-one", "Draft One", "Write concise release notes.")
    second = _skill_entry("draft-two", "Draft Two", "Write concise release notes.")
    registry = SkillRegistry([])
    registry._entries = [first, second]
    llm = MagicMock()
    llm.think.return_value = '{"skill_id": "default/draft-two"}'
    service = SkillService(registry, agent=MagicMock(), llm_client=llm)

    route = service.maybe_auto_select("Write concise release notes.")

    assert route is not None
    assert service.current == second
    assert service.snapshot().auto_route_source == "llm"
    llm.think.assert_called_once()
    kwargs = llm.think.call_args.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["max_attempts"] == 1
    assert kwargs["thinking"] is False


def test_skill_service_llm_fallback_can_decline_skill():
    first = _skill_entry("draft-one", "Draft One", "Write concise release notes.")
    second = _skill_entry("draft-two", "Draft Two", "Write concise release notes.")
    registry = SkillRegistry([])
    registry._entries = [first, second]
    llm = MagicMock()
    llm.think.return_value = '{"skill_id": null}'
    service = SkillService(registry, agent=MagicMock(), llm_client=llm)

    route = service.maybe_auto_select("Write concise release notes.")

    assert route is None
    assert service.current is None
    llm.think.assert_called_once()


def test_skill_service_confident_route_does_not_call_llm_fallback():
    entry = _skill_entry("draft-writer", "Draft Writer", "Write concise product release notes and drafts.")
    registry = SkillRegistry([])
    registry._entries = [entry]
    llm = MagicMock()
    service = SkillService(registry, agent=MagicMock(), llm_client=llm)

    route = service.maybe_auto_select("Please write concise release notes for this product.")

    assert route is not None
    llm.think.assert_not_called()


def test_skill_router_parse_llm_skill_id_strict_validation():
    assert _parse_llm_skill_id('{"skill_id": "default/draft"}') == "default/draft"
    assert _parse_llm_skill_id('```json\n{"skill_id": null}\n```') is None
    assert _parse_llm_skill_id('Selected:\n{"skill_id": "default/draft"}') == "default/draft"
    assert _parse_llm_skill_id('{"skill_id": "default/draft", "reason": "x"}') is None
    assert _parse_llm_skill_id('{"skill_id": 123}') is None
    assert _parse_llm_skill_id('["default/draft"]') is None


def test_skill_service_llm_fallback_invalid_json_declines_without_selecting():
    first = _skill_entry("draft-one", "Draft One", "Write concise release notes.")
    second = _skill_entry("draft-two", "Draft Two", "Write concise release notes.")
    registry = SkillRegistry([])
    registry._entries = [first, second]
    llm = MagicMock()
    llm.think.return_value = '{"skill_id": "default/draft-two", "reason": "extra"}'
    service = SkillService(registry, agent=MagicMock(), llm_client=llm)

    route = service.maybe_auto_select("Write concise release notes.")

    assert route is None
    assert service.current is None
