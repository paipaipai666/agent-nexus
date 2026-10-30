"""Export the real terminal UI with deterministic, offline demonstration data."""

import argparse
import asyncio
import io
import os
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def save_screenshot(app, path, title):
    """Preserve terminal cell widths for CJK text in Rich's SVG export."""
    from rich.cells import cell_len
    from rich.console import Console

    console = Console(width=app.size.width, height=app.size.height, file=io.StringIO(),
                      force_terminal=True, color_system="truecolor", record=True, legacy_windows=False)
    console.print(app.screen._compositor.render_update(full=True, screen_stack=app._background_screens))
    root = ET.fromstring(console.export_svg(title=title, theme=app.ansi_theme))
    for node in root.iter("{http://www.w3.org/2000/svg}text"):
        text = node.text or ""
        if text and node.get("textLength"):
            node.set("textLength", str(float(node.get("textLength")) * cell_len(text) / len(text)))
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")


async def preview(output, size, theme):
    """Capture welcome, task and inspector states without an LLM request."""
    from concurrent.futures import ThreadPoolExecutor

    from agentnexus.agents.react_types import ReActEvent, ReActEventType
    from agentnexus.core.config import get_settings
    from agentnexus.memory.short_term import ShortTermMemory
    from agentnexus.memory.versioned import ConversationVersionManager
    from agentnexus.services.chat import ChatService, SessionHandle
    from agentnexus.services.collaboration import ChildTask, CollaborationRun
    from agentnexus.services.subagents import SubagentRunContext
    from agentnexus.storage.timeline import get_timeline_store
    from agentnexus.tui.app import AgentNexusTUI
    from agentnexus.tui.widgets.input_bar import Composer
    from agentnexus.tui.widgets.message import ToolCall

    answer = "已检查终端入口。思考、工具和回答按顺序显示，任务详情使用统一入口。"
    agent = MagicMock()
    agent.agent_id, agent.model_id, agent.max_steps = "main", "demo-model", 5
    agent.llm_client.model = "demo-model"
    agent.llm_client.base_url = ""
    agent.llm_client.capabilities = SimpleNamespace(supports_thinking=True, supports_tool_calling=True, max_context_tokens=128000)
    agent._on_event, agent._todo_list, agent._total_usage, agent.total_usage, agent._step_count = None, None, {}, {}, 0
    reasoning_release, tool_release = threading.Event(), threading.Event()
    def execute(*_args, **_kwargs):
        def emit(kind, **payload):
            agent._on_event(ReActEvent(kind, payload), None, None)

        emit(ReActEventType.STREAM_REASONING, token="先检查会话入口和按键绑定，确认多行输入与会话切换如何交互。")
        reasoning_release.wait(10)
        emit(ReActEventType.TOOL_START, id="read", name="file_read", arguments={"path": "agentnexus/tui/screens/chat.py"})
        tool_release.wait(10)
        emit(ReActEventType.TOOL_DONE, id="read", name="file_read", result="示例源码结果\nfrom textual.screen import Screen", duration_ms=18)
        emit(ReActEventType.STREAM_TOKEN, token=answer)
        return SimpleNamespace(answer=answer)

    agent.run.side_effect = execute
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
    pool = ThreadPoolExecutor(1)
    context = SubagentRunContext(chat, "preview", "demo-parent")
    collaboration = CollaborationRun(context, get_timeline_store(), pool, lambda: False)
    chat._collaborations["demo-parent"] = collaboration
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        save_screenshot(app, output / "welcome.svg", "AgentNexus · welcome")
        app.screen.query_one("#chat-input", Composer).value = "检查终端布局和协作流程"
        await pilot.press("enter")
        try:
            for _ in range(200):
                if "思考" in str(app.screen.query_one("#agent-activity").render()):
                    break
                await pilot.pause(.02)
            await pilot.pause()
            save_screenshot(app, output / "thinking.svg", "AgentNexus · thinking")
            reasoning_release.set()
            for _ in range(200):
                if "读取文件" in str(app.screen.query_one("#agent-activity").render()):
                    break
                await pilot.pause(.02)
            await pilot.pause()
            save_screenshot(app, output / "working.svg", "AgentNexus · working")
        finally:
            reasoning_release.set()
            tool_release.set()
        for _ in range(200):
            if chat.get_session_recovery("preview") and not chat.is_processing:
                break
            await pilot.pause(.02)
        for _ in range(200):
            if all(tool.result != "executing..." for tool in app.screen.query(ToolCall)):
                break
            await pilot.pause(.02)
        else:
            raise RuntimeError("Tool completion did not reach the rendered conversation")
        child = chat._subagents.register("preview", "demo-parent", "源码检查", "explorer", "检查终端入口与布局")
        chat._subagents.update(child, status="completed", latest_progress="入口与窗口缩放已检查", steps_used=3)
        child = context.started("交互检查", "reviewer", "检查键盘操作与工具展开")
        chat._subagents.update(child, status="waiting", latest_progress="需要确认多行输入快捷键", waiting_questions=1, child_run_id="demo-attempt", collaborative=True)
        task = ChildTask(child, "demo-attempt")
        collaboration.children[child.subagent_id] = task
        collaboration._send(task, child.subagent_id, "parent", "question", "多行消息继续使用 Ctrl+S 发送吗？", "demo-question")
        app.screen._refresh_workspace()
        for _ in range(200):
            if app.screen.query_one("#agent-activity").activity.mode == "waiting":
                break
            await pilot.pause(.02)
        else:
            raise RuntimeError("Waiting child status did not reach the rendered conversation")
        app.screen._chat_area.scroll_home(animate=False)
        await pilot.pause()
        save_screenshot(app, output / "conversation.svg", "AgentNexus · offline demo")
        app.screen.query_one(Composer).value = "/thi"
        await pilot.pause()
        save_screenshot(app, output / "suggestions.svg", "AgentNexus · command completion")
        await pilot.press("escape")
        app.screen.query_one(Composer).value = ""
        await pilot.press("f1")
        save_screenshot(app, output / "status.svg", "AgentNexus · usage and keys")
        await pilot.press("escape")
        await pilot.press("ctrl+p")
        save_screenshot(app, output / "commands.svg", "AgentNexus · actions")
        await pilot.press("escape", "f4")
        app.screen.query_one(Composer).value = "继续检查以下操作：\n1. 切换模型与思考强度\n2. 附件与草稿恢复"
        await pilot.pause()
        save_screenshot(app, output / "composer.svg", "AgentNexus · inline composer")
        await pilot.press("f3")
        save_screenshot(app, output / "inspector.svg", "AgentNexus · task inspector")
        await pilot.press("r")
        save_screenshot(app, output / "reply.svg", "AgentNexus · reply editor")
        await pilot.press("escape")
    task.ended = True
    collaboration.close("finished")
    pool.shutdown()
    print(f"Actual Textual screenshots: {output}")
    version._conn.close()
    get_timeline_store()._conn.close()


def main():
    """Export screenshots with configuration and storage isolated in a temporary home."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=80)
    parser.add_argument("--height", type=int, default=24)
    parser.add_argument("--theme", choices=["nexus-dark", "nexus-light", "ansi-dark", "ansi-light"], default="ansi-dark")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()
    if args.no_color:
        os.environ["NO_COLOR"] = "1"
    else:
        os.environ.pop("NO_COLOR", None)
    with tempfile.TemporaryDirectory(prefix="nexus-preview-") as home:
        os.environ["AGENTNEXUS_HOME"] = home
        asyncio.run(preview(args.output.resolve(), (args.width, args.height), args.theme))


if __name__ == "__main__":
    main()
