import shutil
import uuid
from pathlib import Path


def test_workflow_loads_and_converts_to_session_profile():
    tmp_path = Path.cwd() / "build" / "test-workspace" / uuid.uuid4().hex
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "workflow.yaml"
    try:
        path.write_text(
            """
id: code_review
version: "1"
display_name: Code Review
description: Review code changes
prompt_profile:
  system: react
  fragments:
    - security
  variables:
    tone: concise
tool_policy:
  allow:
    - grep_search
    - file_read
  deny:
    - shell_exec
  max_risk: low
  allow_subagents: false
memory_policy:
  inject_long_term: false
  allow_save: false
retrieval_policy:
  namespace: docs
  view: section
  top_k: 3
steps:
  - type: prompt
    id: gather
    prompt: Inspect the change.
  - type: retrieve
    id: docs
success_criteria:
  - Findings are actionable.
""".strip(),
            encoding="utf-8",
        )

        from agentnexus.skills import load_workflow

        workflow = load_workflow(path)
        profile = workflow.to_session_profile()

        assert profile.workflow_id == "code_review"
        assert profile.display_name == "Code Review"
        assert profile.description == "Review code changes"
        assert profile.success_criteria == ["Findings are actionable."]
        assert profile.tool_policy.allow == ["grep_search", "file_read"]
        assert profile.retrieval_policy.top_k == 3
        assert [step.type for step in profile.steps] == ["prompt", "retrieve"]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
