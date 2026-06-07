"""Unit tests for the computer_use module.

Tests element model, role mapping, snapshot formatting, HITL rules,
manager, and provider registration. Uses mocks — no real desktop required.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentnexus.tools.computer_use.element import (
    ROLE_MAPS,
    DesktopElement,
    normalize_role,
)
from agentnexus.tools.computer_use.snapshot import (
    INTERACTIVE_ROLES,
    READING_ROLES,
    format_desktop_numbered,
    format_desktop_yaml,
)


# ---------------------------------------------------------------------------
# Async test helper
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously for testing."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings():
    """Mock settings for computer_use."""
    ns = SimpleNamespace(
        computer_use_enabled=True,
        computer_use_backend="auto",
        computer_use_snapshot_max_nodes=100,
        computer_use_hitl_rules=[],
        computer_use_allowed_apps=[],
        computer_use_blocked_apps=["taskmgr", "regedit", "cmd", "powershell"],
    )
    with patch("agentnexus.tools.computer_use.manager.get_settings", return_value=ns), \
         patch("agentnexus.tools.computer_use.tools.get_settings", return_value=ns):
        yield ns


@pytest.fixture
def sample_elements():
    """Create a sample DesktopElement tree for testing."""
    return [
        DesktopElement(
            role="window",
            name="Notepad",
            children=(
                DesktopElement(
                    role="menubar",
                    name="",
                    children=(
                        DesktopElement(role="menuitem", name="File"),
                        DesktopElement(role="menuitem", name="Edit"),
                    ),
                ),
                DesktopElement(
                    role="group",
                    name="",
                    children=(
                        DesktopElement(
                            role="textbox",
                            name="Text area",
                            value="Hello world",
                            focused=True,
                            bounds=(10, 50, 400, 300),
                        ),
                    ),
                ),
                DesktopElement(
                    role="status",
                    name="Ready",
                ),
            ),
        )
    ]


@pytest.fixture
def mock_backend():
    """Create a mock ComputerUseBackend."""
    backend = AsyncMock()
    backend.list_windows = AsyncMock(return_value=[
        {"title": "Notepad", "app_name": "notepad.exe", "bounds": (0, 0, 800, 600)},
    ])
    backend.get_snapshot = AsyncMock(return_value=[])
    backend.click = AsyncMock()
    backend.type_text = AsyncMock()
    backend.press_key = AsyncMock()
    backend.scroll = AsyncMock()
    backend.select = AsyncMock()
    backend.toggle = AsyncMock()
    backend.launch_app = AsyncMock(return_value={"pid": 1234, "title": "Notepad", "app_name": "notepad"})
    backend.get_clipboard = AsyncMock(return_value="clipboard content")
    backend.set_clipboard = AsyncMock()
    backend.close = AsyncMock()
    return backend


@pytest.fixture
def manager(mock_settings, mock_backend):
    """Create a ComputerUseManager with mocked backend."""
    from agentnexus.tools.computer_use.manager import ComputerUseManager

    ComputerUseManager._instance = None
    mgr = ComputerUseManager()
    mgr._ttl_enabled = False
    mgr._backend = mock_backend
    mgr._backend_platform = "windows"

    yield mgr

    # Cleanup
    mgr._focused_window.clear()
    mgr._last_access.clear()
    mgr._active_tasks.clear()
    mgr._backend = None
    ComputerUseManager._instance = None


# ---------------------------------------------------------------------------
# Test: DesktopElement
# ---------------------------------------------------------------------------


class TestDesktopElement:
    """Tests for the DesktopElement dataclass."""

    def test_basic_creation(self):
        elem = DesktopElement(role="button", name="OK")
        assert elem.role == "button"
        assert elem.name == "OK"
        assert elem.enabled is True
        assert elem.focused is False
        assert elem.checked is None
        assert elem.value is None
        assert elem.children == ()

    def test_frozen(self):
        elem = DesktopElement(role="button", name="OK")
        with pytest.raises(AttributeError):
            elem.role = "changed"

    def test_bounds_properties(self):
        elem = DesktopElement(role="button", name="OK", bounds=(10, 20, 100, 50))
        assert elem.x == 10
        assert elem.y == 20
        assert elem.width == 100
        assert elem.height == 50

    def test_children(self):
        child = DesktopElement(role="text", name="Label")
        parent = DesktopElement(role="group", name="", children=(child,))
        assert len(parent.children) == 1
        assert parent.children[0].role == "text"

    def test_to_dict(self):
        elem = DesktopElement(
            role="button", name="OK", value="v", enabled=True,
            focused=True, checked=True, bounds=(0, 0, 100, 30),
            platform_role="Button", platform_id="btn-ok",
        )
        d = elem.to_dict()
        assert d["role"] == "button"
        assert d["name"] == "OK"
        assert d["value"] == "v"
        assert d["focused"] is True
        assert d["checked"] is True
        assert d["platform_role"] == "Button"


# ---------------------------------------------------------------------------
# Test: Role mapping
# ---------------------------------------------------------------------------


class TestRoleMapping:
    """Tests for cross-platform role normalization."""

    def test_windows_roles(self):
        assert normalize_role("windows", "Button") == "button"
        assert normalize_role("windows", "Edit") == "textbox"
        assert normalize_role("windows", "Text") == "text"
        assert normalize_role("windows", "CheckBox") == "checkbox"
        assert normalize_role("windows", "Hyperlink") == "link"
        assert normalize_role("windows", "Window") == "window"

    def test_linux_roles(self):
        assert normalize_role("linux", "push_button") == "button"
        assert normalize_role("linux", "entry") == "textbox"
        assert normalize_role("linux", "check_box") == "checkbox"
        assert normalize_role("linux", "toggle_button") == "switch"
        assert normalize_role("linux", "frame") == "window"

    def test_macos_roles(self):
        assert normalize_role("macos", "AXButton") == "button"
        assert normalize_role("macos", "AXTextField") == "textbox"
        assert normalize_role("macos", "AXCheckBox") == "checkbox"
        assert normalize_role("macos", "AXWindow") == "window"
        assert normalize_role("macos", "AXStaticText") == "text"

    def test_unknown_role_falls_back_to_generic(self):
        assert normalize_role("windows", "UnknownType") == "generic"
        assert normalize_role("linux", "unknown_role") == "generic"
        assert normalize_role("macos", "AXUnknown") == "generic"

    def test_unknown_platform(self):
        assert normalize_role("unknown_platform", "Button") == "generic"

    def test_all_platforms_have_maps(self):
        assert "windows" in ROLE_MAPS
        assert "linux" in ROLE_MAPS
        assert "macos" in ROLE_MAPS


# ---------------------------------------------------------------------------
# Test: Snapshot formatting
# ---------------------------------------------------------------------------


class TestSnapshotFormatting:
    """Tests for YAML and numbered formatting."""

    def test_yaml_format_basic(self, sample_elements):
        result = format_desktop_yaml(sample_elements)
        assert "window" in result
        assert "Notepad" in result
        assert "menuitem" in result
        assert "textbox" in result

    def test_yaml_format_empty(self):
        result = format_desktop_yaml([])
        assert result == "(empty desktop)"

    def test_yaml_annotations(self, sample_elements):
        result = format_desktop_yaml(sample_elements)
        assert '[value="Hello world"]' in result
        assert "[focused]" in result

    def test_yaml_checked_annotation(self):
        elem = DesktopElement(role="checkbox", name="Remember", checked=True)
        result = format_desktop_yaml([elem])
        assert "[checked]" in result

    def test_yaml_unchecked_annotation(self):
        elem = DesktopElement(role="checkbox", name="Remember", checked=False)
        result = format_desktop_yaml([elem])
        assert "[unchecked]" in result

    def test_numbered_format_basic(self, sample_elements):
        result = format_desktop_numbered(sample_elements)
        lines = result.split("\n")
        assert lines[0].startswith("[1]")
        assert "window" in lines[0]
        assert "Notepad" in lines[0]

    def test_numbered_format_empty(self):
        result = format_desktop_numbered([])
        assert result == "(no elements)"

    def test_numbered_format_start_idx(self, sample_elements):
        result = format_desktop_numbered(sample_elements, start_idx=5)
        lines = result.split("\n")
        assert lines[0].startswith("[5]")

    def test_truncation_by_max_nodes(self, sample_elements):
        result = format_desktop_numbered(sample_elements, max_nodes=3)
        lines = result.split("\n")
        assert len(lines) <= 3

    def test_yaml_disabled_annotation(self):
        elem = DesktopElement(role="button", name="OK", enabled=False)
        result = format_desktop_yaml([elem])
        assert "[disabled]" in result


# ---------------------------------------------------------------------------
# Test: HITL rules
# ---------------------------------------------------------------------------


class TestHitlRules:
    """Tests for HITL rule checking."""

    def test_no_rules(self, mock_settings):
        from agentnexus.tools.computer_use.manager import _check_hitl_rules
        assert _check_hitl_rules("click", "button", "OK") is False

    def test_matching_action(self, mock_settings):
        mock_settings.computer_use_hitl_rules = [{"action": "click"}]
        from agentnexus.tools.computer_use.manager import _check_hitl_rules
        assert _check_hitl_rules("click", "button", "OK") is True

    def test_non_matching_action(self, mock_settings):
        mock_settings.computer_use_hitl_rules = [{"action": "type"}]
        from agentnexus.tools.computer_use.manager import _check_hitl_rules
        assert _check_hitl_rules("click", "button", "OK") is False

    def test_matching_role(self, mock_settings):
        mock_settings.computer_use_hitl_rules = [{"role": "button"}]
        from agentnexus.tools.computer_use.manager import _check_hitl_rules
        assert _check_hitl_rules("click", "button", "OK") is True
        assert _check_hitl_rules("click", "textbox", "name") is False

    def test_matching_name_pattern(self, mock_settings):
        mock_settings.computer_use_hitl_rules = [{"name_pattern": "删除|确认"}]
        from agentnexus.tools.computer_use.manager import _check_hitl_rules
        assert _check_hitl_rules("click", "button", "确认删除") is True
        assert _check_hitl_rules("click", "button", "Cancel") is False

    def test_combined_rules(self, mock_settings):
        mock_settings.computer_use_hitl_rules = [
            {"action": "click", "role": "button", "name_pattern": "支付|确认"},
        ]
        from agentnexus.tools.computer_use.manager import _check_hitl_rules
        assert _check_hitl_rules("click", "button", "确认支付") is True
        assert _check_hitl_rules("click", "button", "Cancel") is False
        assert _check_hitl_rules("type", "button", "确认支付") is False


# ---------------------------------------------------------------------------
# Test: App blocklist
# ---------------------------------------------------------------------------


class TestAppBlocklist:
    """Tests for application blocklist/allowlist."""

    def test_blocked_app(self, mock_settings):
        from agentnexus.tools.computer_use.manager import _is_blocked_app
        assert _is_blocked_app("taskmgr") is True
        assert _is_blocked_app("regedit") is True
        assert _is_blocked_app("notepad") is False

    def test_allowed_app_empty_list(self, mock_settings):
        from agentnexus.tools.computer_use.manager import _is_allowed_app
        mock_settings.computer_use_allowed_apps = []
        assert _is_allowed_app("anything") is True

    def test_allowed_app_with_list(self, mock_settings):
        from agentnexus.tools.computer_use.manager import _is_allowed_app
        mock_settings.computer_use_allowed_apps = ["notepad", "calc"]
        assert _is_allowed_app("notepad") is True
        assert _is_allowed_app("calc") is True
        assert _is_allowed_app("taskmgr") is False


# ---------------------------------------------------------------------------
# Test: Error/warning formatting
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for error/warning formatting helpers."""

    def test_error_format(self):
        from agentnexus.tools.computer_use.manager import _error
        result = _error("something failed")
        assert result == "ERROR: something failed"

    def test_error_with_detail(self):
        from agentnexus.tools.computer_use.manager import _error
        result = _error("failed", detail="reason")
        assert "ERROR: failed" in result
        assert "DETAIL: reason" in result

    def test_error_with_hint(self):
        from agentnexus.tools.computer_use.manager import _error
        result = _error("failed", hint="try this")
        assert "HINT: try this" in result

    def test_warning_format(self):
        from agentnexus.tools.computer_use.manager import _warning
        result = _warning("something")
        assert result == "WARNING: something"

    def test_warning_with_detail(self):
        from agentnexus.tools.computer_use.manager import _warning
        result = _warning("something", detail="info")
        assert "DETAIL: info" in result


# ---------------------------------------------------------------------------
# Test: ComputerUseManager
# ---------------------------------------------------------------------------


class TestComputerUseManager:
    """Tests for the ComputerUseManager singleton."""

    def test_singleton(self):
        from agentnexus.tools.computer_use.manager import ComputerUseManager
        ComputerUseManager._instance = None
        m1 = ComputerUseManager.instance()
        m2 = ComputerUseManager.instance()
        assert m1 is m2
        ComputerUseManager._instance = None

    def test_singleton_reset(self):
        from agentnexus.tools.computer_use.manager import ComputerUseManager
        ComputerUseManager._instance = None
        m1 = ComputerUseManager.instance()
        ComputerUseManager._instance = None
        m2 = ComputerUseManager.instance()
        assert m1 is not m2
        ComputerUseManager._instance = None


class TestManagerOperations:
    """Tests for manager async operations."""

    def test_list_windows(self, manager, mock_backend):
        result = _run(manager.list_windows("task1"))
        assert len(result) == 1
        assert result[0]["title"] == "Notepad"
        mock_backend.list_windows.assert_called_once()

    def test_get_snapshot(self, manager, mock_backend, sample_elements):
        mock_backend.get_snapshot.return_value = sample_elements
        result = _run(manager.get_snapshot("task1", app_name="Notepad"))
        assert len(result) == 1
        mock_backend.get_snapshot.assert_called_once_with("Notepad", None)

    def test_click(self, manager, mock_backend):
        _run(manager.click("task1", "btn-ok", "left", 1))
        mock_backend.click.assert_called_once_with("btn-ok", "left", 1)

    def test_type_text(self, manager, mock_backend):
        _run(manager.type_text("task1", "txt-input", "hello", True))
        mock_backend.type_text.assert_called_once_with("txt-input", "hello", True)

    def test_press_key(self, manager, mock_backend):
        _run(manager.press_key("task1", "ctrl+s"))
        mock_backend.press_key.assert_called_once_with("ctrl+s")

    def test_scroll(self, manager, mock_backend):
        _run(manager.scroll("task1", None, "down", 5))
        mock_backend.scroll.assert_called_once_with(None, "down", 5)

    def test_select(self, manager, mock_backend):
        _run(manager.select("task1", "combo", "Option A"))
        mock_backend.select.assert_called_once_with("combo", "Option A")

    def test_toggle(self, manager, mock_backend):
        _run(manager.toggle("task1", "chk", True))
        mock_backend.toggle.assert_called_once_with("chk", True)

    def test_launch_blocked_app(self, manager, mock_settings):
        with pytest.raises(ValueError, match="黑名单"):
            _run(manager.launch_app("task1", "taskmgr"))

    def test_launch_allowed_app(self, manager, mock_backend):
        result = _run(manager.launch_app("task1", "notepad"))
        assert result["pid"] == 1234
        mock_backend.launch_app.assert_called_once_with("notepad", None)

    def test_clipboard(self, manager, mock_backend):
        content = _run(manager.get_clipboard("task1"))
        assert content == "clipboard content"
        mock_backend.get_clipboard.assert_called_once()

    def test_set_clipboard(self, manager, mock_backend):
        _run(manager.set_clipboard("task1", "new text"))
        mock_backend.set_clipboard.assert_called_once_with("new text")

    def test_focused_window(self, manager):
        _run(manager.set_focused_window("task1", {"title": "Test", "app_name": "test"}))
        result = _run(manager.get_focused_window("task1"))
        assert result["title"] == "Test"

    def test_close_task(self, manager):
        _run(manager.set_focused_window("task1", {"title": "Test"}))
        manager.close_task("task1")
        assert manager._focused_window.get("task1") is None
        assert "task1" not in manager._active_tasks


# ---------------------------------------------------------------------------
# Test: Backend auto-detection
# ---------------------------------------------------------------------------


class TestBackendDetection:
    """Tests for platform backend auto-detection."""

    def test_detect_windows(self):
        with patch("sys.platform", "win32"):
            from agentnexus.tools.computer_use.backends import _detect_platform
            assert _detect_platform() == "windows"

    def test_detect_macos(self):
        with patch("sys.platform", "darwin"):
            from agentnexus.tools.computer_use.backends import _detect_platform
            assert _detect_platform() == "macos"

    def test_detect_linux(self):
        with patch("sys.platform", "linux"):
            from agentnexus.tools.computer_use.backends import _detect_platform
            assert _detect_platform() == "linux"


# ---------------------------------------------------------------------------
# Test: Provider registration
# ---------------------------------------------------------------------------


class TestProviderRegistration:
    """Tests for ComputerUseToolProvider registration."""

    def test_provider_in_default_list(self):
        from agentnexus.tools.providers import ComputerUseToolProvider, default_tool_providers
        providers = default_tool_providers()
        provider_types = [type(p) for p in providers]
        assert ComputerUseToolProvider in provider_types

    def test_provider_metadata(self):
        from agentnexus.tools.providers import ComputerUseToolProvider
        provider = ComputerUseToolProvider()
        meta = provider.metadata()
        assert meta.name == "computer-use"
        assert "桌面" in meta.description

    def test_all_tools_registered(self):
        from agentnexus.tools.providers import ComputerUseToolProvider, ToolProviderContext
        from agentnexus.tools.registry import ToolRegistry

        registry = ToolRegistry()
        provider = ComputerUseToolProvider()
        ctx = ToolProviderContext(non_interactive=True)
        provider.register(registry, ctx)

        tools = set(registry.list_tools())
        expected = {
            "computer_snapshot",
            "computer_list_windows",
            "computer_switch_window",
            "computer_launch",
            "computer_click",
            "computer_type",
            "computer_key",
            "computer_select",
            "computer_toggle",
            "computer_scroll",
        }
        assert expected.issubset(tools), f"Missing tools: {expected - tools}"


# ---------------------------------------------------------------------------
# Test: Tool functions (sync wrappers)
# ---------------------------------------------------------------------------


class TestToolFunctions:
    """Tests for the sync tool wrapper functions."""

    def test_snapshot_disabled(self, mock_settings):
        mock_settings.computer_use_enabled = False
        from agentnexus.tools.computer_use.tools import computer_snapshot
        result = computer_snapshot(task_id="t1")
        assert "ERROR" in result
        assert "未启用" in result

    def test_list_windows_disabled(self, mock_settings):
        mock_settings.computer_use_enabled = False
        from agentnexus.tools.computer_use.tools import computer_list_windows
        result = computer_list_windows(task_id="t1")
        assert "ERROR" in result

    def test_click_disabled(self, mock_settings):
        mock_settings.computer_use_enabled = False
        from agentnexus.tools.computer_use.tools import computer_click
        result = computer_click(element_id="btn", task_id="t1")
        assert "ERROR" in result

    def test_type_disabled(self, mock_settings):
        mock_settings.computer_use_enabled = False
        from agentnexus.tools.computer_use.tools import computer_type
        result = computer_type(element_id="txt", text="hello", task_id="t1")
        assert "ERROR" in result

    def test_key_disabled(self, mock_settings):
        mock_settings.computer_use_enabled = False
        from agentnexus.tools.computer_use.tools import computer_key
        result = computer_key(keys="ctrl+s", task_id="t1")
        assert "ERROR" in result

    def test_scroll_invalid_direction(self, mock_settings):
        from agentnexus.tools.computer_use.tools import computer_scroll
        with patch("agentnexus.tools.computer_use.tools._run_async", side_effect=lambda coro: coro):
            # Direct async test
            from agentnexus.tools.computer_use.tools import _async_scroll
            result = _run(_async_scroll(None, "invalid", 3, "t1"))
            assert "ERROR" in result
            assert "无效" in result
