from agentnexus.skills.profile import (
    build_workflow_guidance,
    filter_tool_meta,
    format_tool_policy_summary,
    validate_session_profile,
)
from agentnexus.skills.workflow import Workflow
from agentnexus.tools.registry import RiskLevel, ToolMeta


def _profile(**overrides):
    data = {
        "id": "code_review",
        "version": "1",
        "display_name": "Code Review",
        "description": "Review {target}",
        "prompt_profile": {
            "system": "react",
            "fragments": ["security"],
            "variables": {"target": "diff"},
        },
        "tool_policy": {
            "allow": ["file_read", "web_search", "subagent_run"],
            "deny": ["web_search"],
            "max_risk": "low",
            "allow_subagents": False,
        },
        "steps": [{"type": "prompt", "id": "inspect", "prompt": "Inspect {target}."}],
        "success_criteria": ["Findings mention {target}."],
    }
    data.update(overrides)
    return Workflow.model_validate(data).to_session_profile()


def _meta(name: str, risk: RiskLevel = RiskLevel.LOW) -> ToolMeta:
    return ToolMeta(name=name, description="desc", param_schema={}, risk_level=risk)


def test_validate_session_profile_loads_prompt_and_fragments():
    compiled = validate_session_profile(_profile())
    assert "ReAct" in compiled.prompt_template
    assert "Security Fragment" in compiled.fragments_text
    assert "Skill Workflow" in compiled.workflow_guidance
    assert "Review diff" in compiled.workflow_guidance
    assert "Inspect diff." in compiled.workflow_guidance


def test_workflow_guidance_lists_skill_resources():
    workflow = Workflow.model_validate({
        "id": "draft-writer",
        "version": "1",
        "display_name": "Draft Writer",
        "description": "Draft text",
        "prompt_profile": {"system": "react"},
        "tool_policy": {"max_risk": "low"},
        "steps": [{"type": "prompt", "prompt": "Write."}],
        "success_criteria": ["Done."],
        "resources": [
            {
                "type": "script",
                "path": "scripts/format.py",
                "absolute_path": "C:/skills/draft-writer/scripts/format.py",
                "name": "format.py",
                "size_bytes": 12,
            },
            {"type": "reference", "path": "references/style.md", "name": "style.md", "size_bytes": 20},
            {"type": "asset", "path": "assets/template.txt", "name": "template.txt", "size_bytes": 8},
        ],
    })

    guidance = build_workflow_guidance(workflow.to_session_profile())

    assert "Bundled resources" in guidance
    assert "scripts/format.py" in guidance
    assert "C:/skills/draft-writer/scripts/format.py" in guidance
    assert "references/style.md" in guidance
    assert "assets/template.txt" in guidance


def test_validate_session_profile_missing_prompt_errors():
    profile = _profile(prompt_profile={"system": "missing_template", "fragments": []})
    try:
        validate_session_profile(profile)
    except ValueError as exc:
        assert "Prompt template not found" in str(exc)
    else:
        raise AssertionError("expected missing prompt error")


def test_validate_session_profile_missing_fragment_errors():
    profile = _profile(prompt_profile={"system": "react", "fragments": ["missing_fragment"]})
    try:
        validate_session_profile(profile)
    except ValueError as exc:
        assert "Prompt fragment not found" in str(exc)
    else:
        raise AssertionError("expected missing fragment error")


def test_filter_tool_meta_applies_allow_deny_risk_and_subagents():
    policy = _profile().tool_policy
    assert filter_tool_meta("file_read", _meta("file_read", RiskLevel.LOW), policy)
    assert not filter_tool_meta("web_search", _meta("web_search", RiskLevel.LOW), policy)
    assert not filter_tool_meta("shell_exec", _meta("shell_exec", RiskLevel.HIGH), policy)
    assert not filter_tool_meta("subagent_run", _meta("subagent_run", RiskLevel.LOW), policy)


def test_build_workflow_guidance_and_policy_summary():
    profile = _profile()
    guidance = build_workflow_guidance(profile)
    assert "Success criteria" in guidance
    assert "Findings mention diff." in guidance
    summary = format_tool_policy_summary(profile.tool_policy)
    assert "risk<=low" in summary
    assert "allow=3" in summary
    assert "deny=1" in summary
    assert "no-subagents" in summary
