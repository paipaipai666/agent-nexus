"""Pure method tests for ConfirmDialog — init, compose, key/button handlers."""

from unittest.mock import MagicMock, patch

from agentnexus.tui.widgets.confirm_dialog import ConfirmDialog


class TestConfirmDialog:
    def test_init_stores_values(self):
        """Constructor stores tool_name, params_summary, and risk_level."""
        dialog = ConfirmDialog("test_tool", "some params", risk_level="medium")
        assert dialog._tool_name == "test_tool"
        assert dialog._params_summary == "some params"
        assert dialog._risk_level == "medium"

    def test_params_truncated_at_500(self):
        """Params summary is truncated to 500 characters."""
        long_text = "x" * 1000
        dialog = ConfirmDialog("t", long_text)
        assert len(dialog._params_summary) == 500

    def test_default_risk_level(self):
        """Default risk level is 'high'."""
        dialog = ConfirmDialog("t", "p")
        assert dialog._risk_level == "high"

    def test_short_params_not_truncated(self):
        """Short params are stored in full."""
        dialog = ConfirmDialog("t", "short")
        assert dialog._params_summary == "short"

    def test_on_button_pressed_confirm(self):
        """Confirm button triggers dismiss(True)."""
        dialog = ConfirmDialog("t", "p")
        mock_event = MagicMock()
        mock_event.button.id = "btn-confirm"
        with patch.object(dialog, "dismiss") as mock_dismiss:
            dialog.on_button_pressed(mock_event)
            mock_dismiss.assert_called_once_with(True)

    def test_on_button_pressed_cancel(self):
        """Cancel button triggers dismiss(False)."""
        dialog = ConfirmDialog("t", "p")
        mock_event = MagicMock()
        mock_event.button.id = "btn-cancel"
        with patch.object(dialog, "dismiss") as mock_dismiss:
            dialog.on_button_pressed(mock_event)
            mock_dismiss.assert_called_once_with(False)

    def test_on_button_pressed_unknown(self):
        """Unknown button id falls through to else branch → dismiss(False)."""
        dialog = ConfirmDialog("t", "p")
        mock_event = MagicMock()
        mock_event.button.id = "btn-other"
        with patch.object(dialog, "dismiss") as mock_dismiss:
            dialog.on_button_pressed(mock_event)
            mock_dismiss.assert_called_once_with(False)

    def test_on_key_y_confirms(self):
        """Lowercase 'y' key triggers dismiss(True)."""
        dialog = ConfirmDialog("t", "p")
        with patch.object(dialog, "dismiss") as mock_dismiss:
            mock_event = MagicMock()
            mock_event.key = "y"
            dialog.on_key(mock_event)
            mock_dismiss.assert_called_once_with(True)

    def test_on_key_uppercase_y_confirms(self):
        """Uppercase 'Y' key triggers dismiss(True)."""
        dialog = ConfirmDialog("t", "p")
        with patch.object(dialog, "dismiss") as mock_dismiss:
            mock_event = MagicMock()
            mock_event.key = "Y"
            dialog.on_key(mock_event)
            mock_dismiss.assert_called_once_with(True)

    def test_on_key_n_cancels(self):
        """Lowercase 'n' key triggers dismiss(False)."""
        dialog = ConfirmDialog("t", "p")
        with patch.object(dialog, "dismiss") as mock_dismiss:
            mock_event = MagicMock()
            mock_event.key = "n"
            dialog.on_key(mock_event)
            mock_dismiss.assert_called_once_with(False)

    def test_on_key_escape_cancels(self):
        """Escape key triggers dismiss(False)."""
        dialog = ConfirmDialog("t", "p")
        with patch.object(dialog, "dismiss") as mock_dismiss:
            mock_event = MagicMock()
            mock_event.key = "escape"
            dialog.on_key(mock_event)
            mock_dismiss.assert_called_once_with(False)

    def test_on_key_unhandled_ignored(self):
        """Arbitrary keys are ignored (no dismiss call)."""
        dialog = ConfirmDialog("t", "p")
        with patch.object(dialog, "dismiss") as mock_dismiss:
            mock_event = MagicMock()
            mock_event.key = "ctrl+q"
            dialog.on_key(mock_event)
            mock_dismiss.assert_not_called()
