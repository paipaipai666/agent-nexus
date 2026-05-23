"""Tests for InputBar widget."""

from unittest.mock import MagicMock, patch

from textual.widgets import Button, Input, Static

from agentnexus.tui.widgets.input_bar import InputBar


class TestInputBar:
    def test_init(self):
        bar = InputBar()
        assert isinstance(bar, InputBar)

    def test_compose(self):
        bar = InputBar()
        with patch("agentnexus.tui.widgets.input_bar.Horizontal") as mock_h:
            mock_h.return_value.__enter__.return_value = None
            children = list(bar.compose())
        assert len(children) == 2
        assert isinstance(children[0], Static)
        assert isinstance(bar._inp, Input)

    def test_app_submit_message(self):
        msg = InputBar.AppSubmit("hello")
        assert msg.text == "hello"

    def test_app_submit_empty(self):
        msg = InputBar.AppSubmit("")
        assert msg.text == ""

    def test_on_input_submitted(self):
        bar = InputBar()
        bar._inp = MagicMock()
        bar._inp.value = "hello"
        posted = []
        bar.post_message = lambda m: posted.append(m)
        bar.on_input_submitted(Input.Submitted(bar._inp, "hello"))
        assert len(posted) == 1
        assert posted[0].text == "hello"

    def test_on_input_submitted_empty(self):
        bar = InputBar()
        bar._inp = MagicMock()
        bar._inp.value = "   "
        posted = []
        bar.post_message = lambda m: posted.append(m)
        bar.on_input_submitted(Input.Submitted(bar._inp, "   "))
        assert len(posted) == 0

    def test_focus_input_ignores_error(self):
        bar = InputBar()
        bar._focus_input()

    def test_on_button_pressed(self):
        bar = InputBar()
        bar._inp = MagicMock()
        bar._inp.value = "cmd"
        posted = []
        bar.post_message = lambda m: posted.append(m)
        bar.on_button_pressed(Button.Pressed(button=MagicMock()))
        assert len(posted) == 1
        assert posted[0].text == "cmd"

    def test_on_button_pressed_empty(self):
        bar = InputBar()
        bar._inp = MagicMock()
        bar._inp.value = ""
        posted = []
        bar.post_message = lambda m: posted.append(m)
        bar.on_button_pressed(Button.Pressed(button=MagicMock()))
        assert len(posted) == 0
