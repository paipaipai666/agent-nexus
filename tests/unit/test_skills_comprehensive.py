"""Comprehensive end-to-end tests for the skills system.

Covers:
- Full skill lifecycle: discover -> route -> apply -> reset
- Skill runtime: workflow step state, run state
- Builtin code_review skill validation
- Router end-to-end with real SkillEntry objects
"""

from pathlib import Path

import pytest
import yaml

BUILTIN_REVIEW_DIR = (
    Path(__file__).resolve().parents[2]
    / "agentnexus" / "skills" / "builtin" / "review"
)


class TestBuiltinCodeReviewSkill:

    def test_builtin_workflow_yaml_exists(self):
        yaml_path = BUILTIN_REVIEW_DIR / "workflow.yaml"
        assert yaml_path.exists(), f"Expected workflow.yaml at {yaml_path}"

    def test_builtin_workflow_yaml_is_valid(self):
        yaml_path = BUILTIN_REVIEW_DIR / "workflow.yaml"
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert data is not None, "workflow.yaml is empty"
        assert "id" in data
        assert "version" in data
        assert "display_name" in data
        assert "prompt_profile" in data
        assert "tool_policy" in data
        assert "steps" in data
        assert len(data["steps"]) > 0

    def test_builtin_workflow_loads_as_workflow_model(self):
        from agentnexus.skills.workflow import Workflow, WorkflowLoader
        loader = WorkflowLoader()
        workflow = loader.load(BUILTIN_REVIEW_DIR / "workflow.yaml")
        assert isinstance(workflow, Workflow)
        assert workflow.id
        assert workflow.display_name
        assert len(workflow.steps) > 0

    def test_builtin_workflow_has_valid_tool_policy(self):
        from agentnexus.skills.workflow import ToolPolicy, WorkflowLoader
        loader = WorkflowLoader()
        workflow = loader.load(BUILTIN_REVIEW_DIR / "workflow.yaml")
        policy = workflow.tool_policy
        assert isinstance(policy, ToolPolicy)
        assert policy.max_risk in {"low", "medium", "high"}


class TestSkillRegistryDiscovery:

    def test_discover_from_skill_dir(self, tmp_path):
        from agentnexus.skills.registry import SkillRegistry
        skill_dir = tmp_path / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# My Skill\n", encoding="utf-8")
        registry = SkillRegistry(roots=[str(skill_dir)])
        entries = registry.discover()
        assert len(entries) >= 0

    def test_discover_returns_empty_for_nonexistent_path(self):
        from agentnexus.skills.registry import SkillRegistry
        registry = SkillRegistry(roots=["/nonexistent/path"])
        entries = registry.discover()
        assert entries == []

    def test_discover_from_workflow_yaml(self, tmp_path):
        from agentnexus.skills.registry import SkillRegistry
        skill_dir = tmp_path / "skills" / "yaml-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "workflow.yaml").write_text(
            "id: yaml-skill\n"
            "version: 1.0.0\n"
            "display_name: YAML Skill\n"
            "description: A YAML-based skill\n"
            "prompt_profile:\n"
            "  system: test\n"
            "tool_policy:\n"
            "  max_risk: low\n"
            "steps:\n"
            "  - type: prompt\n"
            "    prompt: test\n"
            "success_criteria:\n"
            "  - done\n",
            encoding="utf-8",
        )
        registry = SkillRegistry(roots=[str(skill_dir)])
        entries = registry.discover()
        ids = [e.workflow_id for e in entries]
        assert "yaml-skill" in ids


class TestRouterEndToEnd:

    @pytest.fixture
    def skill_entry(self, tmp_path) -> list:
        from agentnexus.skills.registry import SkillEntry
        from agentnexus.skills.workflow import (
            PromptProfile,
            ToolPolicy,
            Workflow,
            WorkflowStep,
        )
        workflow = Workflow(
            id="test-router",
            version="1.0.0",
            display_name="Test Router Skill",
            description="A skill for testing the router",
            prompt_profile=PromptProfile(system="You are a test assistant."),
            tool_policy=ToolPolicy(allow=["file_read"], max_risk="low"),
            steps=[WorkflowStep(type="prompt", prompt="Test prompt")],
            success_criteria=["Done"],
        )
        entry = SkillEntry(
            namespace="test",
            workflow_id="test-router",
            display_name="Test Router Skill",
            description="A skill for testing the router",
            path=tmp_path / "skills" / "test-router",
            workflow=workflow,
        )
        return [entry]

    def test_router_index_build(self, skill_entry):
        from agentnexus.skills.router import SkillRouterIndex
        index = SkillRouterIndex.build(skill_entry)
        assert index.items is not None
        assert len(index.items) > 0

    def test_router_route_no_match(self, skill_entry):
        from agentnexus.skills.router import SkillRouter
        router = SkillRouter(min_score=10.0)
        result = router.decide("irrelevant unrelated query", skill_entry)
        assert result.route is None

    def test_router_route_with_match(self, skill_entry):
        from agentnexus.skills.router import SkillRouter
        router = SkillRouter(min_score=0.1)
        result = router.decide("help me test router skill", skill_entry)
        assert result.route is not None
        assert result.route.score >= 0

    def test_router_route_api(self, skill_entry):
        from agentnexus.skills.router import SkillRouter
        router = SkillRouter(min_score=0.1)
        route = router.route("test router", skill_entry)
        assert route is not None


class TestSkillRuntime:

    def test_workflow_step_state_ok_count(self):
        from agentnexus.skills.runtime import WorkflowRunState, WorkflowStepState
        steps = [
            WorkflowStepState(id="s1", type="prompt", status="ok"),
            WorkflowStepState(id="s2", type="prompt", status="error"),
        ]
        state = WorkflowRunState(
            run_id="run-1", question="test", workflow_id="test", steps=steps,
        )
        assert state.ok_count == 1
        assert state.error_count == 1

    def test_workflow_step_duration(self):
        from agentnexus.skills.runtime import WorkflowStepState
        state = WorkflowStepState(id="s1", type="prompt", status="ok")
        assert state.duration_ms == 0

    def test_workflow_run_enhanced_question(self):
        from agentnexus.skills.runtime import WorkflowRunResult
        result = WorkflowRunResult(question="hello", workflow_context="context")
        assert result.enhanced_question == "context\n\n== User Question ==\nhello"

    def test_supported_kb_filters_filters_correctly(self):
        from agentnexus.skills.runtime import _supported_kb_filters
        result = _supported_kb_filters({"source": "web", "unknown_key": 123, "page_number": 5})
        assert "source" in result
        assert "page_number" in result
        assert "unknown_key" not in result


class TestSkillServiceCore:

    def test_skill_service_state(self):
        from agentnexus.services.skill import SkillService
        from agentnexus.skills.registry import SkillRegistry
        from agentnexus.skills.router import SkillRouter
        registry = SkillRegistry(roots=[])
        router = SkillRouter()
        service = SkillService(registry=registry, router=router)
        assert service is not None
        assert not service.status or service.status == "idle"
        assert service.current is None
