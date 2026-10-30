from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentnexus.services.chat import ChatService
from agentnexus.services.skill import SkillService
from agentnexus.skills import SkillRegistry
from agentnexus.skills.router import SkillRouter


@pytest.fixture(autouse=True)
def persist_test_traces(tmp_path, monkeypatch):
    from agentnexus.core.telemetry import trace_manager

    trace_manager.configure(str(tmp_path / "traces"))


def recording_agent():
    agent = MagicMock()
    agent.run.return_value = "done"
    agent.llm_client.model = "test-model"
    agent.agent_id = "test-agent"
    agent.max_steps = 5
    agent._total_usage = {}
    agent._step_count = 0
    return agent


def write_skill(root: Path, skill_id: str, instruction: str = "Follow this skill.") -> Path:
    path = root / skill_id / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {skill_id}\ndescription: Instructions for {skill_id}.\n---\n\n{instruction}\n",
        encoding="utf-8",
    )
    return path


def test_default_registry_discovers_user_and_project_agents_skills(tmp_path, monkeypatch):
    user_home = tmp_path / "user"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    monkeypatch.setenv("AGENTNEXUS_HOME", str(tmp_path / "agentnexus"))
    monkeypatch.chdir(project)
    user_skill = write_skill(user_home / ".agents" / "skills", "global-helper")
    project_skill = write_skill(project / ".agents" / "skills", "project-helper")
    legacy_skill = write_skill(tmp_path / "agentnexus" / "skills", "legacy-helper")
    extension_skill = write_skill(tmp_path / "extensions", "extension-helper")

    registry = SkillRegistry.from_settings(SimpleNamespace(extensions_dirs=[
        str(tmp_path / "extensions"), str(project / ".agents" / "skills"),
    ]))
    registry.discover()

    assert registry.get("global-helper").path == user_skill
    assert registry.get("project-helper").path == project_skill
    assert registry.get("legacy-helper").path == legacy_skill
    assert registry.get("extension-helper").path == extension_skill
    assert sum(entry.path == project_skill for entry in registry.list()) == 1
    assert registry.get("review/code_review") is not None
    assert registry.errors == []


def test_chat_discovers_only_its_workspace_skills(tmp_path, monkeypatch):
    user_home = tmp_path / "user"
    launch = tmp_path / "launch"
    launch.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    monkeypatch.chdir(launch)
    write_skill(user_home / ".agents" / "skills", "shared-guidance")
    write_skill(launch / ".agents" / "skills", "launch-only")
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    write_skill(alpha / ".agents" / "skills", "project-guide", "Apply alpha project instructions.")
    write_skill(beta / ".agents" / "skills", "project-guide", "Apply beta project instructions.")
    write_skill(alpha / ".agents" / "skills", "alpha-only")
    write_skill(beta / ".agents" / "skills", "beta-only")
    registry = SkillRegistry.from_settings(SimpleNamespace(extensions_dirs=[]))
    registry.discover()
    base = SkillService(registry, auto_route=False, router=SkillRouter(use_embeddings=False))
    agents = {}

    def make_agent(session_id):
        agent = recording_agent()
        agents[session_id] = agent
        return agent

    chat = ChatService(
        agent_factory=make_agent,
        memory_factory_builder=lambda sid: lambda: MagicMock(),
        skill_service=base,
    )
    first = chat.start_session(workspace=str(alpha), skill="project-guide")
    second = chat.start_session(workspace=str(beta), skill="project-guide")

    with ThreadPoolExecutor(max_workers=2) as workers:
        first_run = workers.submit(chat.send_message, first.id, "Follow the project instructions.")
        second_run = workers.submit(chat.send_message, second.id, "Follow the project instructions.")
        first_run.result(timeout=10)
        second_run.result(timeout=10)
    chat.send_message(first.id, "Continue with the same project.")

    for session, own, other in ((first, "alpha", "beta"), (second, "beta", "alpha")):
        agent = agents[session.id]
        catalog = agent.set_available_skill_context.call_args.args[0]
        guidance = agent.set_workflow_context.call_args.args[0]
        profile = agent.set_session_profile.call_args.args[0]
        assert "default/shared-guidance" in catalog
        assert f"default/{own}-only" in catalog
        assert f"default/{other}-only" not in catalog
        assert "launch-only" not in catalog
        assert f"Apply {own} project instructions." in guidance
        assert f"Apply {other} project instructions." not in guidance
        assert profile.workflow_id == "project-guide"
    assert base.current is None


def test_existing_session_receives_global_enable_changes_and_project_refresh(tmp_path, monkeypatch):
    user_home = tmp_path / "user"
    project = tmp_path / "project"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    write_skill(user_home / ".agents" / "skills", "shared-guidance")
    write_skill(project / ".agents" / "skills", "project-guide")
    registry = SkillRegistry.from_settings(SimpleNamespace(extensions_dirs=[]))
    registry.discover()
    base = SkillService(registry, auto_route=False, router=SkillRouter(use_embeddings=False))
    chat = ChatService(
        agent_factory=lambda sid: recording_agent(),
        memory_factory_builder=lambda sid: lambda: MagicMock(), skill_service=base,
    )
    session = chat.start_session(workspace=str(project))
    assert "default/shared-guidance" in chat.session_skills(session.id).available_skill_context()

    base.set_enabled("shared-guidance", False)
    write_skill(project / ".agents" / "skills", "new-project-guide")
    base.refresh()

    catalog = chat.session_skills(session.id).available_skill_context()
    assert "default/shared-guidance" not in catalog
    assert "default/project-guide" in catalog
    assert "default/new-project-guide" in catalog


@pytest.fixture
def skill_api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agentnexus.server.deps import get_runtime
    from agentnexus.server.error_handlers import register_error_handlers
    from agentnexus.server.routes.skills import router

    user_home = tmp_path / "user"
    launch = tmp_path / "launch"
    launch.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    monkeypatch.chdir(launch)
    write_skill(user_home / ".agents" / "skills", "shared-guidance")
    write_skill(launch / ".agents" / "skills", "launch-only")
    project = tmp_path / "project"
    write_skill(project / ".agents" / "skills", "project-guide", "Apply project instructions.")
    registry = SkillRegistry.from_settings(SimpleNamespace(extensions_dirs=[]))
    registry.discover()
    base = SkillService(registry, auto_route=False, router=SkillRouter(use_embeddings=False))
    agent = recording_agent()
    chat = ChatService(
        agent_factory=lambda sid: agent,
        memory_factory_builder=lambda sid: lambda: MagicMock(), skill_service=base,
    )
    session = chat.start_session(workspace=str(project))
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/api/skills")
    app.dependency_overrides[get_runtime] = lambda: SimpleNamespace(skill=base, chat=chat)
    client = TestClient(app)
    return client, chat, base, session, project, agent


def test_skill_api_lists_only_the_requested_session_workspace(skill_api):
    client, chat, base, session, project, agent = skill_api

    response = client.get("/api/skills", params={"session_id": session.id})
    assert response.status_code == 200
    ids = {skill["id"] for skill in response.json()["skills"]}
    assert "default/project-guide" in ids
    assert "default/shared-guidance" in ids
    assert "default/launch-only" not in ids
    default_response = client.get("/api/skills")
    default_ids = {skill["id"] for skill in default_response.json()["skills"]}
    assert "default/launch-only" in default_ids
    assert "default/project-guide" not in default_ids


def test_skill_selected_through_api_applies_to_the_session_chat(skill_api):
    client, chat, base, session, project, agent = skill_api
    response = client.post(
        "/api/skills/use", params={"session_id": session.id}, json={"skill_id": "project-guide"},
    )
    assert response.status_code == 200

    chat.send_message(session.id, "Continue using my chosen skill.")

    assert "Apply project instructions." in agent.set_workflow_context.call_args.args[0]
    assert agent.set_session_profile.call_args.args[0].workflow_id == "project-guide"
    assert base.current is None


@pytest.mark.parametrize("remove", [False, True], ids=["updated", "removed"])
def test_refresh_replaces_or_clears_the_selected_project_skill(skill_api, remove):
    client, chat, base, session, project, agent = skill_api
    params = {"session_id": session.id}
    assert client.post("/api/skills/use", params=params, json={"skill_id": "project-guide"}).status_code == 200
    path = project / ".agents" / "skills" / "project-guide" / "SKILL.md"
    if remove:
        path.unlink()
    else:
        write_skill(project / ".agents" / "skills", "project-guide", "Apply updated instructions.")

    assert client.post("/api/skills/refresh", params=params).status_code == 200
    chat.send_message(session.id, "Continue with the refreshed skills.")

    guidance = agent.set_workflow_context.call_args.args[0]
    assert "Apply project instructions." not in guidance
    if remove:
        assert guidance == ""
        assert agent.set_session_profile.call_args.args[0] is None
    else:
        assert "Apply updated instructions." in guidance


def test_skill_api_context_detail_and_unknown_session_are_scoped(skill_api):
    client, chat, base, session, project, agent = skill_api
    params = {"session_id": session.id}
    context = client.get("/api/skills/context", params=params)
    assert context.status_code == 200
    assert "default/project-guide" in context.json()["context"]
    assert "launch-only" not in context.json()["context"]
    detail = client.get("/api/skills/default/project-guide", params=params)
    assert detail.status_code == 200
    assert detail.json()["id"] == "default/project-guide"
    unknown = client.get("/api/skills", params={"session_id": "unknown-session"})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not_found"


def test_sessions_using_the_startup_workspace_isolate_skill_selection(skill_api):
    client, chat, base, session, project, agent = skill_api
    first = chat.start_session()
    second = chat.start_session()

    chat.session_skills(first.id).use("launch-only")

    assert chat.session_skills(first.id).snapshot().current == "default/launch-only"
    assert chat.session_skills(second.id).snapshot().current == "default/default"
    assert base.current is None


def test_user_and_project_definitions_still_report_same_id_conflicts(tmp_path, monkeypatch):
    user_home = tmp_path / "user"
    project = tmp_path / "project"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    write_skill(user_home / ".agents" / "skills", "duplicate-guide")
    write_skill(project / ".agents" / "skills", "duplicate-guide")
    registry = SkillRegistry.from_settings(
        SimpleNamespace(extensions_dirs=[]), workspace_path=project,
    )

    registry.discover()

    with pytest.raises(ValueError, match="Duplicate skill id"):
        registry.get("default/duplicate-guide")
    assert "default/duplicate-guide" in registry.duplicate_ids


def test_missing_agents_directories_are_skipped_without_creation(tmp_path, monkeypatch):
    user_home = tmp_path / "user"
    project = tmp_path / "project"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    registry = SkillRegistry.from_settings(SimpleNamespace(extensions_dirs=[]), workspace_path=project)

    registry.discover()

    assert registry.errors == []
    assert registry.get("review/code_review") is not None
    assert not (user_home / ".agents").exists()
    assert not (project / ".agents").exists()
