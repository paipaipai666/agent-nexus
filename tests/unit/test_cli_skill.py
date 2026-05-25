import yaml
from typer.testing import CliRunner

import agentnexus.core.config as cfg
from agentnexus.cli import app

runner = CliRunner()


class TestSkillCli:
    def setup_method(self):
        cfg._settings_cache = None

    def teardown_method(self):
        cfg._settings_cache = None

    def test_skill_list_shows_builtin(self, temp_agentnexus_home):
        result = runner.invoke(app, ["skill", "list"])
        assert result.exit_code == 0
        assert "review/code_review" in result.stdout

    def test_skill_validate_builtin(self, temp_agentnexus_home):
        result = runner.invoke(app, ["skill", "validate", "review/code_review"])
        assert result.exit_code == 0
        assert "validation passed" in result.stdout

    def test_skill_validate_missing_returns_nonzero(self, temp_agentnexus_home):
        result = runner.invoke(app, ["skill", "validate", "missing"])
        assert result.exit_code == 1
        assert "Skill not found" in result.stdout

    def test_skill_init_creates_user_skill_md(self, temp_agentnexus_home):
        result = runner.invoke(app, ["skill", "init", "writer/draft", "--name", "Draft Writer"])
        assert result.exit_code == 0
        path = temp_agentnexus_home / "skills" / "writer" / "SKILL.md"
        assert path.exists()
        assert (temp_agentnexus_home / "skills" / "writer" / "scripts").is_dir()
        assert (temp_agentnexus_home / "skills" / "writer" / "references").is_dir()
        assert (temp_agentnexus_home / "skills" / "writer" / "assets").is_dir()
        text = path.read_text(encoding="utf-8")
        assert "id: draft" in text
        assert "name: Draft Writer" in text

        list_result = runner.invoke(app, ["skill", "list"])
        assert list_result.exit_code == 0
        assert "writer/draft" in list_result.stdout
        assert "Source" in list_result.stdout
        assert "Resources" in list_result.stdout

    def test_skill_init_workflow_flag_creates_workflow_yaml(self, temp_agentnexus_home):
        result = runner.invoke(app, ["skill", "init", "writer/draft", "--workflow"])
        assert result.exit_code == 0
        path = temp_agentnexus_home / "skills" / "writer" / "workflow.yaml"
        assert path.exists()
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["id"] == "draft"

    def test_skill_init_refuses_existing_without_force(self, temp_agentnexus_home):
        first = runner.invoke(app, ["skill", "init", "draft"])
        assert first.exit_code == 0
        second = runner.invoke(app, ["skill", "init", "draft"])
        assert second.exit_code == 1
        assert "skill 已存在" in second.stdout

    def test_skill_use_persists_default(self, temp_agentnexus_home):
        result = runner.invoke(app, ["skill", "use", "review/code_review"])
        assert result.exit_code == 0
        assert "默认 skill 已设置" in result.stdout
        data = yaml.safe_load((temp_agentnexus_home / "config.yaml").read_text(encoding="utf-8"))
        assert data["default_skill"] == "review/code_review"

    def test_skill_use_ambiguous_returns_nonzero(self, temp_agentnexus_home):
        workflow = """
id: duplicate
version: "1"
display_name: Duplicate
prompt_profile:
  system: react
tool_policy:
  max_risk: low
steps:
  - type: prompt
    prompt: Inspect.
success_criteria:
  - Done.
""".strip()
        for namespace in ("a", "b"):
            path = temp_agentnexus_home / "skills" / namespace
            path.mkdir(parents=True)
            (path / "workflow.yaml").write_text(workflow, encoding="utf-8")

        result = runner.invoke(app, ["skill", "use", "duplicate"])

        assert result.exit_code == 1
        assert "Ambiguous skill id" in result.stdout

    def test_skill_use_duplicate_qualified_id_returns_nonzero(self, temp_agentnexus_home):
        workflow = """
id: duplicate
version: "1"
display_name: Duplicate
prompt_profile:
  system: react
tool_policy:
  max_risk: low
steps:
  - type: prompt
    prompt: Inspect.
success_criteria:
  - Done.
""".strip()
        path = temp_agentnexus_home / "skills" / "a"
        path.mkdir(parents=True)
        (path / "workflow.yaml").write_text(workflow, encoding="utf-8")
        (path / "other.workflow.yaml").write_text(workflow, encoding="utf-8")

        result = runner.invoke(app, ["skill", "use", "a/duplicate"])

        assert result.exit_code == 1
        assert "Duplicate skill id" in result.stdout

    def test_skill_use_ignores_unrelated_invalid_workflow(self, temp_agentnexus_home):
        broken = temp_agentnexus_home / "skills" / "broken"
        broken.mkdir(parents=True)
        (broken / "workflow.yaml").write_text("id: [", encoding="utf-8")

        result = runner.invoke(app, ["skill", "use", "review/code_review"])

        assert result.exit_code == 0
        assert "默认 skill 已设置" in result.stdout

    def test_skill_reset_clears_default(self, temp_agentnexus_home):
        (temp_agentnexus_home / "config.yaml").write_text("default_skill: review/code_review\n", encoding="utf-8")
        result = runner.invoke(app, ["skill", "reset"])
        assert result.exit_code == 0
        data = yaml.safe_load((temp_agentnexus_home / "config.yaml").read_text(encoding="utf-8")) or {}
        assert "default_skill" not in data

    def test_skill_status_shows_default(self, temp_agentnexus_home):
        (temp_agentnexus_home / "config.yaml").write_text("default_skill: review/code_review\n", encoding="utf-8")
        cfg._settings_cache = None
        result = runner.invoke(app, ["skill", "status"])
        assert result.exit_code == 0
        assert "review/code_review" in result.stdout
