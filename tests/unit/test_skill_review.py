"""Tests for the builtin code_review skill workflow YAML.

Validates that the YAML is syntactically valid, loads correctly,
and has the expected structure for the code review workflow.
"""

from pathlib import Path

import yaml

BUILTIN_REVIEW_YAML = Path(__file__).parents[2] / "agentnexus" / "skills" / "builtin" / "review" / "workflow.yaml"


def test_yaml_exists():
    assert BUILTIN_REVIEW_YAML.exists()
    assert BUILTIN_REVIEW_YAML.suffix == ".yaml"


def test_yaml_is_valid():
    raw = BUILTIN_REVIEW_YAML.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    assert isinstance(data, dict)


def test_loads_as_workflow():
    from agentnexus.skills.workflow import load_workflow

    wf = load_workflow(str(BUILTIN_REVIEW_YAML))
    assert wf.id == "code_review"
    assert wf.version == "1"
    assert wf.entry_mode == "chat"


def test_display_name():
    from agentnexus.skills.workflow import load_workflow

    wf = load_workflow(str(BUILTIN_REVIEW_YAML))
    assert wf.display_name == "Code Review"


def test_description():
    from agentnexus.skills.workflow import load_workflow

    wf = load_workflow(str(BUILTIN_REVIEW_YAML))
    assert "Review code changes" in wf.description


def test_prompt_profile():
    from agentnexus.skills.workflow import load_workflow

    wf = load_workflow(str(BUILTIN_REVIEW_YAML))
    assert wf.prompt_profile.system == "react"
    assert "security" in wf.prompt_profile.fragments


def test_tool_policy_allow():
    from agentnexus.skills.workflow import load_workflow

    wf = load_workflow(str(BUILTIN_REVIEW_YAML))
    assert "file_read" in wf.tool_policy.allow
    assert "grep_search" in wf.tool_policy.allow
    assert "kb_search" in wf.tool_policy.allow


def test_tool_policy_deny():
    from agentnexus.skills.workflow import load_workflow

    wf = load_workflow(str(BUILTIN_REVIEW_YAML))
    assert "shell_exec" in wf.tool_policy.deny
    assert "python_execute" in wf.tool_policy.deny
    assert "file_write" in wf.tool_policy.deny


def test_tool_policy_no_subagents():
    from agentnexus.skills.workflow import load_workflow

    wf = load_workflow(str(BUILTIN_REVIEW_YAML))
    assert wf.tool_policy.allow_subagents is False


def test_tool_policy_max_risk():
    from agentnexus.skills.workflow import load_workflow

    wf = load_workflow(str(BUILTIN_REVIEW_YAML))
    assert wf.tool_policy.max_risk == "low"


def test_memory_policy():
    from agentnexus.skills.workflow import load_workflow

    wf = load_workflow(str(BUILTIN_REVIEW_YAML))
    assert wf.memory_policy.inject_long_term is True
    assert wf.memory_policy.allow_save is False


def test_retrieval_policy():
    from agentnexus.skills.workflow import load_workflow

    wf = load_workflow(str(BUILTIN_REVIEW_YAML))
    assert wf.retrieval_policy.namespace == "default"
    assert wf.retrieval_policy.top_k == 3


def test_steps_have_scope_context_findings():
    from agentnexus.skills.workflow import load_workflow

    wf = load_workflow(str(BUILTIN_REVIEW_YAML))
    step_ids = [s.id for s in wf.steps]
    assert "scope" in step_ids
    assert "context" in step_ids
    assert "findings" in step_ids


def test_finalize_step_is_last():
    from agentnexus.skills.workflow import load_workflow

    wf = load_workflow(str(BUILTIN_REVIEW_YAML))
    assert wf.steps[-1].id == "findings"
    assert wf.steps[-1].type == "finalize"


def test_success_criteria():
    from agentnexus.skills.workflow import load_workflow

    wf = load_workflow(str(BUILTIN_REVIEW_YAML))
    assert len(wf.success_criteria) >= 2
    assert any("actionable" in c for c in wf.success_criteria)
    assert any("Cosmetic" in c for c in wf.success_criteria)


def test_discovered_by_registry():
    import os
    from pathlib import Path

    from agentnexus.core.config import Settings
    from agentnexus.skills.registry import SkillRegistry

    settings = Settings(_env_file=None)
    if "AGENTNEXUS_HOME" not in os.environ:
        os.environ["AGENTNEXUS_HOME"] = str(Path.cwd() / "build" / "test-home")

    registry = SkillRegistry.from_settings(settings)
    entries = registry.discover()
    ids = [e.workflow_id for e in entries]
    assert "code_review" in ids
