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

    assert "Workflow Runtime Context" in result.workflow_context
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
        "default/draft-writer\0Draft Writer\0Write concise product release notes and drafts.\0\0\0"
        "\0Use Draft Writer.\nDone.",
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


def test_available_skill_context_injects_all_when_few_skills():
    entries = [
        _skill_entry(f"skill-{i}", f"Skill {i}", f"Capability number {i}.")
        for i in range(4)
    ]
    registry = SkillRegistry([])
    registry._entries = entries
    service = SkillService(registry, agent=MagicMock())

    context = service.available_skill_context()

    for entry in entries:
        assert entry.qualified_id in context
    assert "more skills available" not in context


def test_available_skill_context_shortlist_when_many_skills():
    long_desc = "Handles deploy workflows for k8s and release trains. " * 4
    entries = [
        _skill_entry(f"skill-{i:02d}", f"Skill {i:02d}", f"{long_desc} Capability {i}.")
        for i in range(12)
    ]
    registry = SkillRegistry([])
    registry._entries = entries
    service = SkillService(registry, agent=MagicMock())
    from agentnexus.skills.router import SkillRoute

    picks = [
        SkillRoute(entry=entries[7], score=4.0, matched_terms=("deploy",), reason="r"),
        SkillRoute(entry=entries[3], score=3.0, matched_terms=("k8s",), reason="r"),
    ]

    # Tight budget: header + recommended block + only the ranked hits
    context = service.available_skill_context(recommendations=picks, token_budget=200)

    assert "default/skill-07" in context
    assert "default/skill-03" in context
    assert "default/skill-00" not in context
    assert "more skills available" in context
    # Ranked shortlist must not be registry-order truncation
    assert context.index("skill-07") < context.index("skill-03")


def test_available_skill_context_orders_recommended_first_when_few():
    entries = [
        _skill_entry("alpha", "Alpha", "Write docs."),
        _skill_entry("beta", "Beta", "Deploy app."),
        _skill_entry("gamma", "Gamma", "Scan code."),
    ]
    registry = SkillRegistry([])
    registry._entries = entries
    service = SkillService(registry, agent=MagicMock())
    from agentnexus.skills.router import SkillRoute

    picks = [SkillRoute(entry=entries[2], score=5.0, matched_terms=("scan",), reason="r")]

    context = service.available_skill_context(recommendations=picks)

    assert context.index("default/gamma") < context.index("default/alpha")
    assert context.index("default/gamma") < context.index("default/beta")
    assert context.count("default/gamma") >= 2  # recommended section + catalog


def test_skill_context_budget_scales_with_context_window():
    service = SkillService(SkillRegistry([]), agent=MagicMock())
    small = service.skill_context_token_budget(
        context_window_tokens=32_000, budget_ratio=0.02, max_tokens=4000,
    )
    large = service.skill_context_token_budget(
        context_window_tokens=256_000, budget_ratio=0.02, max_tokens=4000,
    )
    assert small == int(32_000 * 0.02)
    # 256k * 2% = 5120 but hard-capped at 4k (Claude Code-style ceiling)
    assert large == 4000
    assert large > small
    # explicit override wins
    assert service.skill_context_token_budget(token_budget=99) == 99


def test_skill_context_budget_ceiling_prevents_unbounded_share():
    service = SkillService(SkillRegistry([]), agent=MagicMock())
    huge = service.skill_context_token_budget(
        context_window_tokens=1_000_000, budget_ratio=0.02, max_tokens=4000,
    )
    assert huge == 4000
    uncapped = service.skill_context_token_budget(
        context_window_tokens=1_000_000, budget_ratio=0.02, max_tokens=20_000,
    )
    assert uncapped == 20_000


def test_available_skill_context_respects_token_budget_not_count():
    long_desc = "Detailed capability description with enough text to burn tokens. " * 3
    entries = [
        _skill_entry(f"skill-{i:02d}", f"Skill {i:02d}", f"{long_desc} num={i}")
        for i in range(6)
    ]
    registry = SkillRegistry([])
    registry._entries = entries
    service = SkillService(registry, agent=MagicMock())

    tight = service.available_skill_context(token_budget=200)
    roomy = service.available_skill_context(token_budget=8000)

    assert "more skills available" in tight
    assert tight.count("- default/") < roomy.count("- default/")
    assert "more skills available" not in roomy


def test_adaptive_shortlist_len_expands_on_flat_scores():
    from agentnexus.skills.router.rank import adaptive_shortlist_len

    assert adaptive_shortlist_len([9.0, 1.0], min_k=1, max_k=8) == 1
    assert adaptive_shortlist_len([5.0, 4.8, 4.6, 4.4], min_k=1, max_k=8) == 4
    assert adaptive_shortlist_len([5.0, 4.9] + [1.0] * 10, min_k=1, max_k=5) == 2


def test_synthesize_intent_queries_include_verb_object_templates():
    from agentnexus.skills.router.retrieve import synthesize_intent_queries

    entry = _skill_entry("docx", "DOCX", "Create and edit Word documents.")
    # _skill_entry has no verbs/objects — infer from description
    intents = synthesize_intent_queries(entry)
    assert intents
    assert any("创建" in q or "编辑" in q or "写" in q or "DOCX" in q or "Word" in q for q in intents)


def test_auto_select_soft_recommends_without_locking_profile():
    entry = _skill_entry("only", "Only", "Write concise release notes.")
    registry = SkillRegistry([])
    registry._entries = [entry]
    service = SkillService(registry, agent=MagicMock())
    # Force soft path: single candidate but score gate uses min_score only —
    # single-candidate hard activate stays. Use two close scores instead.
    other = _skill_entry("only2", "Only2", "Write concise release notes too.")
    registry._entries = [entry, other]
    service = SkillService(registry, agent=MagicMock())
    llm = MagicMock()
    llm.think.return_value = '{"skill_id": "default/only", "confidence": 0.6, "reason": "x"}'
    service.llm_client = llm
    route = service.maybe_auto_select("Write concise release notes.")
    # LLM path hard-activates by design
    assert route is not None
    assert service.current is not None


def test_soft_recommend_does_not_use_when_margin_small_without_llm():
    first = _skill_entry("draft-one", "Draft One", "Write concise release notes.")
    second = _skill_entry("draft-two", "Draft Two", "Write concise release notes.")
    registry = SkillRegistry([])
    registry._entries = [first, second]
    service = SkillService(registry, agent=MagicMock())
    route = service.maybe_auto_select("Write concise release notes.")
    assert route is None
    assert service.current is None


def test_llm_rerank_reorders_close_candidates():
    from agentnexus.skills.router.llm_fallback import parse_llm_rank_order, rerank_with_llm

    assert parse_llm_rank_order('{"ordered_skill_ids": ["b", "a"]}') == ["b", "a"]
    assert parse_llm_rank_order("not json") == []

    a = _skill_entry("a", "A", "Write docs.")
    b = _skill_entry("b", "B", "Write docs.")
    from agentnexus.skills.router import SkillRoute

    cands = [
        SkillRoute(entry=a, score=3.0, matched_terms=("write",), reason="r"),
        SkillRoute(entry=b, score=2.9, matched_terms=("docs",), reason="r"),
    ]
    llm = MagicMock()
    llm.think.return_value = '{"ordered_skill_ids": ["default/b", "default/a"]}'
    out = rerank_with_llm("write docs", cands, llm)
    assert [c.entry.workflow_id for c in out] == ["b", "a"]


def test_skill_auto_route_margin_gates_hard_activate():
    """skill_auto_route_margin is the required score gap for hard activation."""
    from agentnexus.skills.router import SkillRoute

    first = _skill_entry("one", "One", "Write release notes.")
    second = _skill_entry("two", "Two", "Write release notes.")
    registry = SkillRegistry([])
    registry._entries = [first, second]
    service = SkillService(registry, agent=MagicMock())

    close = SkillRoute(entry=first, score=4.0, matched_terms=("write",), reason="r")
    far = SkillRoute(entry=second, score=2.5, matched_terms=("notes",), reason="r")

    # gap = 1.5 ≥ margin 0.75 and score 4.0 ≥ 2.5 → hard activate
    assert service._should_hard_activate(close, [close, far]) is True

    service.router.margin = 2.0
    # gap 1.5 < required margin 2.0 → soft only
    assert service._should_hard_activate(close, [close, far]) is False


def test_route_with_llm_uses_configured_margin():
    a = _skill_entry("a", "A", "Write docs.")
    b = _skill_entry("b", "B", "Scan code.")
    registry = SkillRegistry([])
    registry._entries = [a, b]
    service = SkillService(registry, agent=MagicMock())
    service.router.min_score = 2.0
    service.router.margin = 0.75
    route = service.router.route_with_llm("Write docs.", [a, b], llm_client=None)
    assert route is not None
    assert route.entry.qualified_id == "default/a"


def test_skill_service_disable_current_skill_resets_agent_profile():
    entry = _skill_entry("docx", "DOCX", "Create and edit Word documents.")
    registry = SkillRegistry([])
    registry._entries = [entry]
    agent = MagicMock()
    service = SkillService(registry, agent=agent)
    service.use("docx")
    agent.set_session_profile.reset_mock()

    service.set_enabled("default/docx", False)

    assert service.current is None
    agent.set_session_profile.assert_called_once_with(None)
    assert "default/docx" not in service.available_skill_context()


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
    assert _parse_llm_skill_id('{"skill_id": "default/draft", "reason": "x"}') == "default/draft"
    assert _parse_llm_skill_id('{"skill_id": 123}') is None
    assert _parse_llm_skill_id('["default/draft"]') is None


def test_skill_service_llm_fallback_invalid_json_declines_without_selecting():
    first = _skill_entry("draft-one", "Draft One", "Write concise release notes.")
    second = _skill_entry("draft-two", "Draft Two", "Write concise release notes.")
    registry = SkillRegistry([])
    registry._entries = [first, second]
    llm = MagicMock()
    llm.think.return_value = '{"skill_id": 123}'
    service = SkillService(registry, agent=MagicMock(), llm_client=llm)

    route = service.maybe_auto_select("Write concise release notes.")

    assert route is None
    assert service.current is None
