"""Concurrent terminal draft persistence at the filesystem boundary."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from agentnexus.tui.commands import TerminalCommands
from agentnexus.tui.drafts import DraftStore


def test_concurrent_draft_saves_keep_complete_readable_snapshots():
    store = DraftStore()

    def save_many(text):
        for _ in range(80):
            store.save("shared", {"input": text, "draft": "保留的文件草稿"})
            restored = store.load("shared")
            assert restored["input"] in {"界面输入", "后台命令", "加载草稿", "附件更新"}
            assert restored["draft"] == "保留的文件草稿"

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(save_many, ["界面输入", "后台命令", "加载草稿", "附件更新"]))
    store.save("shared", {"input": "最终内容", "draft": "文件草稿"})
    assert store.load("shared") == {"input": "最终内容", "draft": "文件草稿", "attachments": []}


def test_concurrent_input_updates_restore_the_latest_command_state():
    commands = TerminalCommands(SimpleNamespace())
    commands.drafts["shared"] = "文件草稿"

    def save_many(text):
        for _ in range(50):
            commands.save_draft("shared", text=text)

    with ThreadPoolExecutor(3) as pool:
        list(pool.map(save_many, ["界面输入", "命令结果", "加载内容"]))
    commands.save_draft("shared", text="最终输入")
    restored = TerminalCommands(SimpleNamespace())
    restored.load_draft("shared")
    assert restored.inputs["shared"] == "最终输入"
    assert restored.drafts["shared"] == "文件草稿"
