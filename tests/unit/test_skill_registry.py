import shutil
import uuid
from pathlib import Path

from agentnexus.skills import SkillRegistry

WORKFLOW_YAML = """
id: code_review
version: "1"
display_name: Code Review
description: Review code changes
prompt_profile:
  system: react
tool_policy:
  max_risk: low
steps:
  - type: prompt
    id: gather
    prompt: Inspect the change.
success_criteria:
  - Findings are actionable.
""".strip()


def _workspace() -> Path:
    path = Path.cwd() / "build" / "test-workspace" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_registry_discovers_workflow_yaml():
    workspace = _workspace()
    try:
        skill_dir = workspace / "skills" / "review"
        skill_dir.mkdir(parents=True)
        path = skill_dir / "workflow.yaml"
        path.write_text(WORKFLOW_YAML, encoding="utf-8")

        registry = SkillRegistry([workspace / "skills"])
        entries = registry.discover()

        assert len(entries) == 1
        assert entries[0].namespace == "review"
        assert entries[0].workflow_id == "code_review"
        assert entries[0].display_name == "Code Review"
        assert entries[0].path == path
        assert registry.get("code_review") == entries[0]
        assert registry.get("review/code_review") == entries[0]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_registry_discovers_skill_md():
    workspace = _workspace()
    try:
        skill_dir = workspace / "skills" / "writer"
        skill_dir.mkdir(parents=True)
        path = skill_dir / "SKILL.md"
        path.write_text(
            """---
id: draft_writer
name: Draft Writer
description: Write concise drafts.
max_risk: low
allow_tools:
  - file_read
---

# Draft Writer

Follow these writing instructions.
""",
            encoding="utf-8",
        )

        registry = SkillRegistry([workspace / "skills"])
        entries = registry.discover()

        assert len(entries) == 1
        assert entries[0].namespace == "writer"
        assert entries[0].workflow_id == "draft_writer"
        assert entries[0].display_name == "Draft Writer"
        assert entries[0].description == "Write concise drafts."
        assert entries[0].workflow.tool_policy.max_risk == "low"
        assert entries[0].workflow.tool_policy.allow == ["file_read"]
        assert "Follow these writing instructions" in entries[0].workflow.steps[0].prompt
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_registry_standard_skill_folder_uses_plain_skill_id_and_indexes_resources():
    workspace = _workspace()
    try:
        skill_dir = workspace / "skills" / "draft-writer"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "references").mkdir()
        (skill_dir / "assets").mkdir()
        (skill_dir / "SKILL.md").write_text(
            """---
name: Draft Writer
description: Write concise drafts.
---

# Draft Writer

Use references/style.md when style guidance is needed.
""",
            encoding="utf-8",
        )
        (skill_dir / "scripts" / "format.py").write_text("print('ok')\n", encoding="utf-8")
        (skill_dir / "references" / "style.md").write_text("# Style\n\nBe concise.\n", encoding="utf-8")
        (skill_dir / "assets" / "template.txt").write_text("template\n", encoding="utf-8")

        registry = SkillRegistry([workspace / "skills"])
        entries = registry.discover()

        assert len(entries) == 1
        entry = entries[0]
        assert entry.qualified_id == "default/draft-writer"
        assert registry.get("draft-writer") == entry
        assert entry.source_kind == "skill"
        assert {resource.path for resource in entry.workflow.resources} == {
            "scripts/format.py",
            "references/style.md",
            "assets/template.txt",
        }
        assert all(resource.absolute_path for resource in entry.workflow.resources)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_registry_prefers_skill_md_over_workflow_yaml_in_same_dir():
    workspace = _workspace()
    try:
        skill_dir = workspace / "skills" / "review"
        skill_dir.mkdir(parents=True)
        (skill_dir / "workflow.yaml").write_text(WORKFLOW_YAML, encoding="utf-8")
        (skill_dir / "SKILL.md").write_text(
            """---
id: markdown_review
name: Markdown Review
---

# Markdown Review

Use the SKILL.md instructions.
""",
            encoding="utf-8",
        )

        registry = SkillRegistry([workspace / "skills"])
        entries = registry.discover()

        assert len(entries) == 1
        assert entries[0].workflow_id == "markdown_review"
        assert entries[0].display_name == "Markdown Review"
        assert entries[0].path.name == "SKILL.md"
        assert entries[0].source_kind == "skill"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_registry_validate_requires_standard_skill_md_metadata():
    workspace = _workspace()
    try:
        skill_dir = workspace / "skills" / "broken-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Broken\n\nDo work.\n", encoding="utf-8")

        registry = SkillRegistry([workspace / "skills"])
        registry.discover()
        errors = registry.validate("broken-skill")

        assert any("missing required 'name'" in error for error in errors)
        assert any("missing required 'description'" in error for error in errors)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_registry_collects_invalid_yaml_errors():
    workspace = _workspace()
    try:
        skill_dir = workspace / "skills"
        skill_dir.mkdir()
        (skill_dir / "bad.workflow.yaml").write_text("id: [", encoding="utf-8")

        registry = SkillRegistry([skill_dir])
        entries = registry.discover()

        assert entries == []
        assert registry.errors
        assert "Invalid workflow manifest" in registry.errors[0]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_registry_ambiguous_id_requires_namespace():
    workspace = _workspace()
    try:
        root = workspace / "skills"
        for namespace in ("a", "b"):
            skill_dir = root / namespace
            skill_dir.mkdir(parents=True)
            (skill_dir / "workflow.yaml").write_text(WORKFLOW_YAML, encoding="utf-8")

        registry = SkillRegistry([root])
        entries = registry.discover()

        assert len(entries) == 2
        assert registry.get("a/code_review").namespace == "a"
        assert registry.get("b/code_review").namespace == "b"
        try:
            registry.get("code_review")
        except ValueError as exc:
            assert "Ambiguous skill id" in str(exc)
        else:
            raise AssertionError("expected ambiguous skill id")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_registry_validate_reports_ambiguous_id():
    workspace = _workspace()
    try:
        root = workspace / "skills"
        for namespace in ("a", "b"):
            skill_dir = root / namespace
            skill_dir.mkdir(parents=True)
            (skill_dir / "workflow.yaml").write_text(WORKFLOW_YAML, encoding="utf-8")

        registry = SkillRegistry([root])
        registry.discover()

        errors = registry.validate("code_review")

        assert errors
        assert "Ambiguous skill id" in errors[0]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_registry_reports_duplicate_qualified_id():
    workspace = _workspace()
    try:
        root = workspace / "skills" / "review"
        root.mkdir(parents=True)
        (root / "workflow.yaml").write_text(WORKFLOW_YAML, encoding="utf-8")
        (root / "extra.workflow.yaml").write_text(WORKFLOW_YAML, encoding="utf-8")

        registry = SkillRegistry([workspace / "skills"])
        entries = registry.discover()

        assert len(entries) == 1
        assert any("Duplicate skill id review/code_review" in error for error in registry.errors)
        try:
            registry.get("review/code_review")
        except ValueError as exc:
            assert "Duplicate skill id" in str(exc)
        else:
            raise AssertionError("expected duplicate skill id")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_registry_validate_target_ignores_unrelated_invalid_yaml():
    workspace = _workspace()
    try:
        root = workspace / "skills"
        good = root / "review"
        bad = root / "broken"
        good.mkdir(parents=True)
        bad.mkdir(parents=True)
        (good / "workflow.yaml").write_text(WORKFLOW_YAML, encoding="utf-8")
        (bad / "workflow.yaml").write_text("id: [", encoding="utf-8")

        registry = SkillRegistry([root])
        registry.discover()

        assert registry.errors
        assert registry.validate("review/code_review") == []
        assert registry.validate()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_registry_root_level_workflow_uses_default_namespace():
    workspace = _workspace()
    try:
        root = workspace / "skills"
        root.mkdir()
        (root / "workflow.yaml").write_text(WORKFLOW_YAML, encoding="utf-8")

        registry = SkillRegistry([root], default_namespace="default")
        entries = registry.discover()

        assert entries[0].namespace == "default"
        assert entries[0].qualified_id == "default/code_review"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_registry_validate_reports_prompt_errors():
    workspace = _workspace()
    try:
        root = workspace / "skills"
        root.mkdir()
        (root / "workflow.yaml").write_text(
            WORKFLOW_YAML.replace("system: react", "system: missing_prompt"),
            encoding="utf-8",
        )

        registry = SkillRegistry([root])
        registry.discover()
        errors = registry.validate()

        assert errors
        assert "Prompt template not found" in errors[0]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_registry_from_settings_includes_builtin_skills():
    class Settings:
        extensions_dirs = []
        skills_default_namespace = "default"

    registry = SkillRegistry.from_settings(Settings())
    registry.discover()
    ids = [entry.qualified_id for entry in registry.list()]
    assert "review/code_review" in ids
    assert registry.validate("review/code_review") == []
