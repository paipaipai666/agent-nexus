"""Composer behavior through the same keys and paste events used in a terminal."""

from pathlib import Path

import pytest
from textual.app import App
from textual.events import Paste

from agentnexus.tui.widgets.input_bar import Composer, InputBar


class ComposerApp(App):
    AUTO_FOCUS = "#chat-input"
    CSS_PATH = str(Path(__file__).parents[2] / "agentnexus/tui/styles/input_area.tcss")
    BINDINGS = [("f4", "multiline", "多行")]

    def __init__(self):
        super().__init__()
        self.submissions = []
        self.changes = []

    def compose(self):
        yield InputBar(id="input-area")

    def action_multiline(self):
        self.query_one(InputBar).toggle_multiline()

    def on_input_bar_app_submit(self, event):
        self.submissions.append(event.text)

    def on_input_bar_app_input_changed(self, event):
        self.changes.append(event.text)


@pytest.mark.asyncio
async def test_enter_sends_the_complete_buffer_once():
    app = ComposerApp()
    async with app.run_test() as pilot:
        app.query_one(Composer).value = "  保留代码缩进"
        await pilot.press("enter")
        assert app.submissions == ["  保留代码缩进"]
        assert app.changes[-1] == "  保留代码缩进"


@pytest.mark.asyncio
async def test_blank_messages_are_not_submitted():
    app = ComposerApp()
    async with app.run_test() as pilot:
        app.query_one(Composer).value = " \n "
        await pilot.press("ctrl+s")
        assert app.submissions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("separator", ["\n", "\r\n", "\r"])
async def test_paste_is_inserted_once_and_waits_for_explicit_send(separator):
    app = ComposerApp()
    async with app.run_test() as pilot:
        composer = app.query_one(Composer)
        composer.post_message(Paste("第一行" + separator + "第二行"))
        await pilot.pause()
        assert composer.text == "第一行\n第二行"
        assert app.submissions == []
        await pilot.press("ctrl+s")
        assert app.submissions == ["第一行\n第二行"]


@pytest.mark.asyncio
async def test_multiline_mode_keeps_enter_for_newlines_and_ctrl_s_for_send():
    app = ComposerApp()
    async with app.run_test() as pilot:
        composer = app.query_one(Composer)
        composer.value = "first"
        await pilot.press("f4", "enter", "s", "e", "c", "o", "n", "d")
        assert composer.text == "first\nsecond"
        assert app.submissions == []
        assert "Enter 换行" in str(app.query_one("#composer-hints").render())
        await pilot.press("ctrl+s")
        assert app.submissions == ["first\nsecond"]


@pytest.mark.asyncio
async def test_shift_enter_inserts_a_newline_in_send_mode():
    app = ComposerApp()
    async with app.run_test() as pilot:
        app.query_one(Composer).value = "first"
        await pilot.press("shift+enter")
        assert app.query_one(Composer).text == "first\n"
        assert app.submissions == []


@pytest.mark.asyncio
async def test_long_drafts_remain_bounded_and_resizable():
    app = ComposerApp()
    async with app.run_test(size=(60, 20)) as pilot:
        composer = app.query_one(Composer)
        composer.value = "很长的中文消息\n" * 15
        await pilot.pause()
        assert composer.region.height == 6
        await pilot.resize_terminal(40, 16)
        assert composer.region.width > 20
        assert composer.text == "很长的中文消息\n" * 15
