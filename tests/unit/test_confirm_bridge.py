"""Tests for agentnexus.tools.confirm_bridge."""

from unittest.mock import MagicMock

from agentnexus.tools.confirm_bridge import ConfirmBridge


class TestConfirmBridge:
    def test_default_returns_false(self):
        bridge = ConfirmBridge()
        assert bridge("anything") is False

    def test_set_target_none_returns_false(self):
        bridge = ConfirmBridge()
        bridge.set_target(None)
        assert bridge("anything") is False

    def test_set_target_callable_returns_result(self):
        bridge = ConfirmBridge()
        bridge.set_target(lambda s: True)
        assert bridge("summary") is True

    def test_set_target_callable_returns_false(self):
        bridge = ConfirmBridge()
        bridge.set_target(lambda s: False)
        assert bridge("summary") is False

    def test_set_target_replaces_previous(self):
        bridge = ConfirmBridge()
        bridge.set_target(lambda s: False)
        bridge.set_target(lambda s: True)
        assert bridge("summary") is True

    def test_callable_receives_summary(self):
        bridge = ConfirmBridge()
        mock = MagicMock(return_value=True)
        bridge.set_target(mock)
        bridge("test summary")
        mock.assert_called_once_with("test summary")

    def test_callable_return_value_is_bool_coerced(self):
        bridge = ConfirmBridge()
        bridge.set_target(lambda s: 1)
        assert bridge("x") is True

        bridge.set_target(lambda s: 0)
        assert bridge("x") is False

        bridge.set_target(lambda s: "")
        assert bridge("x") is False
