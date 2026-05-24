"""Tests for agentnexus.agents.exceptions."""

import pytest

from agentnexus.agents.exceptions import ToolExecutionError


class TestToolExecutionError:
    def test_can_be_raised(self):
        with pytest.raises(ToolExecutionError):
            raise ToolExecutionError

    def test_can_be_raised_with_message(self):
        with pytest.raises(ToolExecutionError, match="oops"):
            raise ToolExecutionError("oops")

    def test_is_exception_subclass(self):
        assert issubclass(ToolExecutionError, Exception)
