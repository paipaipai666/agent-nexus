"""The running terminal keeps non-chat workflows usable without an agent package."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("engine_mode", ["disabled", "missing", "custom"])
def test_textual_workflows_without_default_agent_or_memory_packages(tmp_path, engine_mode):
    program = '''
import asyncio
import importlib.abc
import sys
from dataclasses import dataclass, replace

attempts = []
class MissingImplementation(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "agentnexus.agents" or fullname.startswith("agentnexus.agents.") or fullname in {
            "agentnexus.memory.short_term", "agentnexus.memory.long_term"
        }:
            attempts.append(fullname)
            raise ModuleNotFoundError(fullname, name=fullname)

sys.meta_path.insert(0, MissingImplementation())
from agentnexus.app.runtime import AppRuntime
from agentnexus.core.config import get_settings
from agentnexus.core.modules import ModuleFactories
from agentnexus.tui.app import AgentNexusTUI
from agentnexus.tui.widgets.input_bar import Composer
from agentnexus.tui.widgets.message import ChatMessage

@dataclass(frozen=True, slots=True)
class Engine:
    def run(self, question, *, memory_manager=None):
        return "selected engine completed"

async def exercise():
    factories = ModuleFactories.disabled()
    if sys.argv[1] == "custom":
        factories = replace(factories, agent=lambda context: Engine())
    elif sys.argv[1] == "missing":
        factories = replace(factories, agent=ModuleFactories().agent)
    settings = get_settings().model_copy(update={"skill_auto_route": False, "skill_auto_route_llm_fallback": False})
    with AppRuntime.build(settings=settings, module_factories=factories) as runtime:
        runtime.version_manager.commit_with_messages([
            {"role": "user", "content": "saved history question"},
            {"role": "assistant", "content": "saved history answer"},
        ], question="saved history question", answer="saved history answer")
        other = runtime.chat.start_session(workspace=runtime.chat.get_session_workspace(runtime.session_id))
        runtime.chat.session_version(other.id).commit_with_messages([
            {"role": "user", "content": "another saved question"},
            {"role": "assistant", "content": "another saved answer"},
        ], question="another saved question", answer="another saved answer")
        app = AgentNexusTUI(runtime=runtime)
        async with app.run_test(size=(90, 28)) as pilot:
            def transcript():
                return "\\n".join(item.content for item in app.screen.query(ChatMessage))
            async def until(predicate):
                for _ in range(150):
                    if predicate():
                        return
                    await pilot.pause(0.02)
                assert predicate(), transcript()
            assert "saved history answer" in transcript()
            await pilot.press("f1")
            assert "PageUp" in app.screen.query_one("#record-content").text
            await pilot.press("escape", "f2")
            assert app.screen.query_one("#palette-search").has_focus
            await pilot.press("escape")
            app.screen.action_task_record()
            await pilot.pause()
            assert "子任务 0" in str(app.screen.query_one("#task-detail").render())
            await pilot.press("escape")
            composer = app.screen.query_one(Composer)
            composer.value = "/todo"
            await pilot.press("enter")
            await until(lambda: list(app.screen.query(ChatMessage))[-1].content == "[]")
            composer.value = "/session switch " + other.id
            await pilot.press("enter")
            await until(lambda: "another saved answer" in transcript())
            composer.value = "/settings"
            await pilot.press("enter")
            await until(lambda: "settings" in transcript() or "llm" in transcript())
            composer.value = "hello selected runtime"
            await pilot.press("enter")
            if sys.argv[1] == "custom":
                await until(lambda: "selected engine completed" in transcript())
                recovery = runtime.chat.get_session_recovery(other.id)
                assert recovery["persisted"] is True
            else:
                await until(lambda: "请求未能开始" in transcript())
                reason = "disabled" if sys.argv[1] == "disabled" else "unavailable"
                assert "agent" in transcript() and reason in transcript()
            await pilot.press("ctrl+p")
            assert app.screen.query_one("#palette-search").has_focus
            await pilot.press("escape")
            assert composer.has_focus
    if sys.argv[1] == "missing":
        assert attempts and all(name.startswith("agentnexus.agents") for name in attempts), attempts
    else:
        assert attempts == [], attempts

asyncio.run(exercise())
print("headless terminal boundary verified")
'''
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", program, engine_mode],
        cwd=Path(__file__).resolve().parents[2],
        env=dict(os.environ, AGENTNEXUS_HOME=str(tmp_path), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1"),
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-14000:]
    assert "headless terminal boundary verified" in result.stdout
