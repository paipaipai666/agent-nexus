"""Export the real terminal UI with deterministic, offline demonstration data."""

import argparse
import asyncio
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def save_screenshot(app, path, title):
    """Preserve terminal cell widths for CJK text in Rich's SVG export."""
    from rich.cells import cell_len

    root = ET.fromstring(app.export_screenshot(title=title))
    for node in root.iter("{http://www.w3.org/2000/svg}text"):
        text = node.text or ""
        if text and node.get("textLength"):
            node.set("textLength", str(float(node.get("textLength")) * cell_len(text) / len(text)))
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")


async def preview(output, size, theme):
    """Capture welcome, task and inspector states without an LLM request."""
    from textual.widgets import Input

    from agentnexus.core.config import get_settings
    from agentnexus.memory.short_term import ShortTermMemory
    from agentnexus.memory.versioned import ConversationVersionManager
    from agentnexus.services.chat import ChatService, SessionHandle
    from agentnexus.tui.app import AgentNexusTUI

    answer = "已检查入口与测试。\n\n- 对话保持单列，任务详情按需展开。\n- 终端缩放后保留输入和会话。\n- 工具结果可用 Tab 选择，再按 Enter 展开。\n\n下一步可以继续检查协作流程。"
    agent = MagicMock()
    agent.agent_id, agent.model_id, agent.max_steps = "main", "demo-model", 5
    agent.llm_client.model = "demo-model"
    agent.llm_client.base_url = ""
    agent.llm_client.capabilities = SimpleNamespace(supports_thinking=True, supports_tool_calling=True, max_context_tokens=128000)
    agent._on_event, agent._todo_list, agent._total_usage, agent.total_usage, agent._step_count = None, None, {}, {}, 0
    agent.run.return_value = SimpleNamespace(answer=answer)
    memory = SimpleNamespace(short_term=ShortTermMemory(), estimate_stm_tokens=lambda: 0)
    workspace = str(Path.cwd())
    version = ConversationVersionManager("preview", get_settings().memory_db_path, workspace_path=workspace)
    chat = ChatService(lambda _sid: agent, lambda _sid: lambda: memory, version_manager=version)
    chat.attach_session(SessionHandle("preview", workspace=workspace), agent=agent, memory=memory, version=version)
    runtime = SimpleNamespace(agent=agent, memory_manager=memory, version_manager=version, chat=chat,
                              session_id="preview", mcp_manager=None, skill=None, capability_runtime=None, llm=agent.llm_client)
    app = AgentNexusTUI(runtime=runtime)
    app.theme = theme
    output.mkdir(parents=True, exist_ok=True)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        save_screenshot(app, output / "welcome.svg", "AgentNexus · welcome")
        app.screen.query_one("#chat-input", Input).value = "检查终端布局和协作流程"
        await pilot.press("enter")
        for _ in range(200):
            if chat.get_session_recovery("preview") and not chat.is_processing:
                break
            await pilot.pause(.02)
        child = chat._subagents.register("preview", "demo-parent", "源码检查", "explorer", "检查终端入口与布局")
        chat._subagents.update(child, status="completed", latest_progress="入口与窗口缩放已检查", steps_used=3)
        child = chat._subagents.register("preview", "demo-parent", "交互检查", "reviewer", "检查键盘操作与工具展开")
        chat._subagents.update(child, status="waiting", latest_progress="需要确认多行输入快捷键", waiting_questions=1)
        app.screen._chat_area.add_tool_call("file_read · agentnexus/tui/screens/chat.py", "示例源码结果\nfrom textual.screen import Screen", 18)
        app.screen._refresh_workspace()
        await pilot.pause()
        app.screen._chat_area.scroll_home(animate=False)
        await pilot.pause()
        save_screenshot(app, output / "conversation.svg", "AgentNexus · offline demo")
        await pilot.press("ctrl+p")
        save_screenshot(app, output / "commands.svg", "AgentNexus · actions")
        await pilot.press("escape", "f3")
        save_screenshot(app, output / "inspector.svg", "AgentNexus · task inspector")
    print(f"Actual Textual screenshots: {output}")
    version._conn.close()
    from agentnexus.observability.timeline import get_timeline_store

    get_timeline_store()._conn.close()


def main():
    """Export screenshots with configuration and storage isolated in a temporary home."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=80)
    parser.add_argument("--height", type=int, default=24)
    parser.add_argument("--theme", choices=["nexus-dark", "nexus-light", "ansi-dark", "ansi-light"], default="nexus-dark")
    args = parser.parse_args()
    os.environ.pop("NO_COLOR", None)
    with tempfile.TemporaryDirectory(prefix="nexus-preview-") as home:
        os.environ["AGENTNEXUS_HOME"] = home
        asyncio.run(preview(args.output.resolve(), (args.width, args.height), args.theme))


if __name__ == "__main__":
    main()
