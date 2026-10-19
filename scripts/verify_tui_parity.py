"""Verify terminal behavior, runtime assets and installed wheel startup."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args, capture=False):
    """Run a check through RTK and preserve its exit status."""
    return subprocess.run(["rtk", "proxy", *map(str, args)], cwd=ROOT, check=True,
                          capture_output=capture, text=True, encoding="utf-8").stdout


def verify_resources():
    """Check that every declared application stylesheet is available."""
    sys.path.insert(0, str(ROOT))
    from agentnexus.tui.app import AgentNexusTUI

    for name in AgentNexusTUI.CSS_PATH:
        if not Path(name).is_file():
            raise SystemExit(f"Missing terminal stylesheet: {name}")
    print("All terminal stylesheets are available.")


async def smoke(target):
    """Launch the installed Textual app using local test doubles only."""
    sys.path.insert(0, str(target))
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    import agentnexus.tui.app as installed

    if not Path(installed.__file__).resolve().is_relative_to(target.resolve()):
        raise SystemExit("Smoke test imported the checkout instead of the installed wheel")
    agent = MagicMock()
    agent.model_id = "smoke-model"
    agent.tool_executor = None
    agent.llm_client.base_url = ""
    agent.llm_client.capabilities = SimpleNamespace(supports_thinking=False, supports_tool_calling=True, max_context_tokens=128000)
    with tempfile.TemporaryDirectory(prefix="nexus-tui-smoke-") as home:
        os.environ["AGENTNEXUS_HOME"] = home
        from agentnexus.core.config import get_settings
        from agentnexus.memory.versioned import ConversationVersionManager
        from agentnexus.observability.timeline import get_timeline_store

        version = ConversationVersionManager("smoke-session", get_settings().memory_db_path, workspace_path=home)
        timeline = get_timeline_store()
        with patch("agentnexus.tui.screens.chat.ChatScreen._init_skill_registry"):
            app = installed.AgentNexusTUI(agent=agent, memory=None, version=version)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                if app.screen.query_one("#chat-area").region.width != 80:
                    raise SystemExit("The default chat must use the full terminal width")
                if app.screen.query_one("#side-panel").display:
                    raise SystemExit("The inspector must be hidden until requested")
                if "AgentNexus" not in str(app.screen.query_one("#welcome").render()):
                    raise SystemExit("The installed welcome screen is missing")
                if app.screen.query_one("#input-area").region.height != 3:
                    raise SystemExit("The default composer must occupy three rows")
                if not app.current_theme.ansi or not app.native_ansi_color:
                    raise SystemExit("The installed workspace must follow terminal colors")
                await pilot.press("tab")
                if app.focused.id != "chat-area":
                    raise SystemExit("Tab must enter transcript navigation")
                await pilot.press("tab")
                if app.focused.id != "chat-input":
                    raise SystemExit("Tab must return directly to input")
                if "空闲" not in str(app.screen.query_one("#agent-activity").render()):
                    raise SystemExit("Installed session activity is missing")
                await pilot.press("f1")
                if "Alt+M" not in app.screen.query_one("#record-content").text:
                    raise SystemExit("Installed action help must expose direct shortcuts")
                await pilot.press("escape", "/", "t", "h", "tab")
                if app.screen.query_one("#chat-input").text != "/think ":
                    raise SystemExit("Installed slash candidates must support completion")
                await pilot.press("escape")
                await pilot.press("ctrl+p")
                if app.screen.query_one("#palette-search").content_region.height != 1:
                    raise SystemExit("The focused picker search must retain a visible text row")
                await pilot.press("escape", "f4")
                from agentnexus.tui.widgets.input_bar import Composer

                composer = app.screen.query_one("#chat-input", Composer)
                if not composer.multiline or not app.screen.query_one("#chat-area").display:
                    raise SystemExit("Multiline editing must stay inside the conversation")
                await pilot.press("f3")
                app.screen.query_one("#task-detail")
                await pilot.press("escape")
        version._conn.close()
        if timeline._conn is not None:
            timeline._conn.close()
    print("Installed wheel passes three-row layout, region navigation, help, completion, pickers and inline editing at 80x24.")


def verify_wheel(wheel):
    """Check runtime resources, install the wheel and launch that installation."""
    expected = {path.relative_to(ROOT).as_posix()
                for folder, pattern in [("agentnexus/tui/styles", "*.tcss"), ("agentnexus/eval_tasks", "*.yaml"), ("agentnexus/skills/builtin", "*.yaml")]
                for path in (ROOT / folder).rglob(pattern)}
    with zipfile.ZipFile(wheel) as archive:
        missing = expected - set(archive.namelist())
    if missing:
        raise SystemExit(f"Wheel is missing runtime assets: {sorted(missing)}")
    target = ROOT / ".research_tmp" / f"tui-installed-{uuid.uuid4().hex[:10]}"
    run(sys.executable, "-m", "pip", "install", "--no-deps", "--no-index", "--no-compile", "--target", target, wheel)
    run(sys.executable, "-X", "utf8", Path(__file__).resolve(), "--smoke-target", target)


def main():
    """Run reproducible functionality and packaging checks."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--smoke-target", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_target:
        asyncio.run(smoke(args.smoke_target))
        return
    verify_resources()
    if not args.skip_tests:
        run(sys.executable, "-X", "utf8", "-m", "pytest", "tests/unit", "-k",
            "tui or app_runtime or llm_providers or configuration_service or memory_query_service",
            "-q", "-p", "no:cacheprovider", f"--basetemp={Path(tempfile.gettempdir()) / ('nexus-tui-verify-' + uuid.uuid4().hex[:10])}")
    if args.wheel:
        verify_wheel(args.wheel.resolve())


if __name__ == "__main__":
    main()
