"""Unit tests for browser automation tools."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_playwright():
    """Mock Playwright module and async_playwright context manager."""
    mock_page = AsyncMock()
    mock_page.title.return_value = "Test Page"
    mock_page.url = "https://example.com"
    mock_page.evaluate.return_value = "complete"
    mock_page.viewport_size = {"width": 1280, "height": 720}

    # locator() is sync, returns a Locator-like mock
    mock_locator = MagicMock()
    mock_locator.aria_snapshot = AsyncMock(
        return_value=(
            '- heading "Welcome" [ref=s1e1]\n'
            '- textbox "Search" [ref=s1e2]\n'
            '- button "Login" [ref=s1e3]\n'
            '- link "About" [ref=s1e4]\n'
            '- banner [ref=s2e1]\n'
            '- navigation [ref=s2e2]'
        )
    )
    mock_locator.count = AsyncMock(return_value=1)
    mock_locator.first = mock_locator
    mock_locator.inner_text = AsyncMock(return_value="Hello World")
    mock_locator.click = AsyncMock()
    mock_locator.dblclick = AsyncMock()
    mock_locator.clear = AsyncMock()
    mock_locator.fill = AsyncMock()
    mock_locator.press = AsyncMock()
    mock_locator.wait_for = AsyncMock()
    mock_locator.scroll_into_view_if_needed = AsyncMock()
    mock_page.locator = MagicMock(return_value=mock_locator)

    # get_by_role / get_by_text are sync, return Locator-like mocks
    mock_role_loc = MagicMock()
    mock_role_loc.count = AsyncMock(return_value=1)
    mock_role_loc.first = mock_role_loc
    mock_role_loc.click = AsyncMock()
    mock_role_loc.dblclick = AsyncMock()
    mock_role_loc.clear = AsyncMock()
    mock_role_loc.fill = AsyncMock()
    mock_role_loc.press = AsyncMock()
    mock_role_loc.wait_for = AsyncMock()
    mock_role_loc.scroll_into_view_if_needed = AsyncMock()
    mock_page.get_by_role = MagicMock(return_value=mock_role_loc)

    mock_text_loc = MagicMock()
    mock_text_loc.count = AsyncMock(return_value=1)
    mock_text_loc.first = mock_text_loc
    mock_page.get_by_text = MagicMock(return_value=mock_text_loc)

    mock_page.goto = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.wait_for_url = AsyncMock()
    mock_page.screenshot = AsyncMock()
    mock_page.mouse = AsyncMock()
    mock_page.close = AsyncMock()

    mock_context = AsyncMock()
    mock_context.new_page.return_value = mock_page
    mock_context.pages = [mock_page]
    mock_context.close = AsyncMock()

    mock_browser = AsyncMock()
    mock_browser.new_context.return_value = mock_context
    mock_browser.contexts = [mock_context]
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch.return_value = mock_browser
    mock_pw.chromium.connect_over_cdp.return_value = mock_browser
    mock_pw.stop = AsyncMock()

    mock_pw_cm = AsyncMock()
    mock_pw_cm.start.return_value = mock_pw

    with patch("agentnexus.tools.browser.async_playwright", return_value=mock_pw_cm):
        yield SimpleNamespace(
            playwright=mock_pw,
            browser=mock_browser,
            context=mock_context,
            page=mock_page,
        )


@pytest.fixture
def mock_settings():
    """Mock get_settings to return browser config."""
    settings = SimpleNamespace(
        browser_mode="isolated",
        browser_cdp_endpoint="http://localhost:9222",
        browser_headless=True,
        browser_viewport_width=1280,
        browser_viewport_height=720,
        browser_default_timeout=30000,
        browser_networkidle_timeout=5000,
        browser_screenshot_dir="",
        browser_context_ttl=600,
        browser_allow_js_execution=False,
        browser_snapshot_max_nodes=100,
    )
    with patch("agentnexus.tools.browser.get_settings", return_value=settings):
        yield settings


@pytest.fixture
def browser_manager():
    """Fresh BrowserManager instance for each test. TTL cleanup is disabled."""
    from agentnexus.tools.browser import BrowserManager

    BrowserManager._instance = None
    mgr = BrowserManager.instance()
    mgr._ttl_enabled = False  # Disable infinite background loop for testing

    yield mgr

    # Cleanup
    if mgr._ttl_task:
        mgr._ttl_task.cancel()
        mgr._ttl_task = None
    mgr._browser_ready = False
    mgr._browser = None
    mgr._playwright = None
    mgr._pages.clear()
    mgr._contexts.clear()
    mgr._active_tasks.clear()
    mgr._last_access.clear()
    # Recreate lock for next test's event loop
    mgr._lock = asyncio.Lock()
    BrowserManager._instance = None


# ---------------------------------------------------------------------------
# BrowserManager tests
# ---------------------------------------------------------------------------


class TestBrowserManager:
    def test_singleton(self):
        from agentnexus.tools.browser import BrowserManager

        BrowserManager._instance = None
        mgr1 = BrowserManager.instance()
        mgr2 = BrowserManager.instance()
        assert mgr1 is mgr2
        BrowserManager._instance = None

    def test_ensure_browser_isolated_mode(self, browser_manager, mock_playwright, mock_settings):
        mock_settings.browser_mode = "isolated"
        browser = _run(browser_manager.ensure_browser())
        assert browser is mock_playwright.browser
        mock_playwright.playwright.chromium.launch.assert_called_once_with(headless=True)

    def test_ensure_browser_cdp_mode(self, browser_manager, mock_playwright, mock_settings):
        mock_settings.browser_mode = "cdp"
        browser = _run(browser_manager.ensure_browser())
        assert browser is mock_playwright.browser
        mock_playwright.playwright.chromium.connect_over_cdp.assert_called_once_with(
            "http://localhost:9222"
        )

    def test_get_page_creates_context_per_task(self, browser_manager, mock_playwright, mock_settings):
        mock_settings.browser_mode = "isolated"
        page = _run(browser_manager.get_page("task-1"))
        assert page is mock_playwright.page
        assert "task-1" in browser_manager._pages
        assert "task-1" in browser_manager._active_tasks
        mock_playwright.browser.new_context.assert_called_once()

    def test_get_page_cdp_reuses_context(self, browser_manager, mock_playwright, mock_settings):
        mock_settings.browser_mode = "cdp"
        page = _run(browser_manager.get_page("task-1"))
        assert page is mock_playwright.page
        assert browser_manager._shared_context is mock_playwright.context

    def test_get_page_cdp_fallback_new_context(self, browser_manager, mock_playwright, mock_settings):
        mock_settings.browser_mode = "cdp"
        mock_playwright.browser.contexts = []
        page = _run(browser_manager.get_page("task-1"))
        assert page is mock_playwright.page
        mock_playwright.browser.new_context.assert_called_once()

    def test_get_page_reuses_existing(self, browser_manager, mock_playwright, mock_settings):
        page1 = _run(browser_manager.get_page("task-1"))
        page2 = _run(browser_manager.get_page("task-1"))
        assert page1 is page2
        assert mock_playwright.browser.new_context.call_count == 1

    def test_close_task(self, browser_manager, mock_playwright, mock_settings):
        _run(browser_manager.get_page("task-1"))
        _run(browser_manager.close_task("task-1"))
        assert "task-1" not in browser_manager._pages
        assert "task-1" not in browser_manager._active_tasks

    def test_mark_task_inactive(self, browser_manager, mock_playwright, mock_settings):
        _run(browser_manager.get_page("task-1"))
        assert "task-1" in browser_manager._active_tasks
        _run(browser_manager.mark_task_inactive("task-1"))
        assert "task-1" not in browser_manager._active_tasks

    def test_navigate_returns_metadata(self, browser_manager, mock_playwright, mock_settings):
        result = _run(browser_manager.navigate("task-1", "https://example.com", "load"))
        assert result["title"] == "Test Page"
        assert result["url"] == "https://example.com"
        assert result["readyState"] == "complete"
        assert result["timed_out"] is False

    def test_navigate_networkidle_timeout_fallback(self, browser_manager, mock_playwright, mock_settings):
        mock_settings.browser_networkidle_timeout = 100
        mock_playwright.page.wait_for_load_state = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        result = _run(browser_manager.navigate("task-1", "https://example.com", "networkidle"))
        assert result["timed_out"] is True
        assert result["url"] == "https://example.com"


# ---------------------------------------------------------------------------
# Tool function tests
# ---------------------------------------------------------------------------


class TestBrowserNavigate:
    def test_navigate_success(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_navigate

        result = browser_navigate("https://example.com", task_id="task-1")
        assert "已导航至" in result
        assert "Test Page" in result

    def test_navigate_invalid_wait_until(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_navigate

        result = browser_navigate("https://example.com", wait_until="invalid", task_id="task-1")
        assert "已导航至" in result


class TestBrowserSnapshot:
    def test_snapshot_returns_structure(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_snapshot

        result = browser_snapshot(task_id="task-1")
        assert "## 页面结构" in result
        assert "## 可交互元素" in result
        assert "button" in result

    def test_snapshot_interactive_mode(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_snapshot

        result = browser_snapshot(mode="interactive", task_id="task-1")
        assert "mode=interactive" in result

    def test_snapshot_full_mode(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_snapshot

        result = browser_snapshot(mode="full", task_id="task-1")
        assert "mode=full" in result


class TestBrowserClick:
    def test_click_with_pos(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_click

        result = browser_click(pos="100,200,50,30", task_id="task-1")
        assert "已点击坐标" in result

    def test_click_with_role_name(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_click

        result = browser_click(role="button", name="Login", task_id="task-1")
        assert "已点击" in result

    def test_click_ref_returns_error(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_click

        result = browser_click(ref="s1e3", task_id="task-1")
        assert "ERROR" in result

    def test_click_no_params_returns_error(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_click

        result = browser_click(task_id="task-1")
        assert "ERROR" in result


class TestBrowserType:
    def test_type_with_pos(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_type

        result = browser_type(pos="400,620,760,24", text="hello", task_id="task-1")
        assert "已输入" in result

    def test_type_with_role_name(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_type

        result = browser_type(role="textbox", name="Search", text="hello", task_id="task-1")
        assert "已输入" in result

    def test_type_no_params_returns_error(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_type

        result = browser_type(text="hello", task_id="task-1")
        assert "ERROR" in result


class TestBrowserRead:
    def test_read_content(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_read

        mock_playwright.page.locator.return_value.inner_text = AsyncMock(
            return_value="Hello World"
        )
        result = browser_read(selector="article", task_id="task-1")
        assert "Hello World" in result

    def test_read_truncation(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_read

        long_text = "x" * 10000
        mock_playwright.page.locator.return_value.inner_text = AsyncMock(
            return_value=long_text
        )
        result = browser_read(max_chars=1000, task_id="task-1")
        assert "内容已截断" in result
        assert "10000" in result


class TestBrowserEvaluate:
    def test_evaluate_disabled_by_default(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_evaluate

        mock_settings.browser_allow_js_execution = False
        result = browser_evaluate("1+1", task_id="task-1")
        assert "ERROR" in result
        assert "未启用" in result

    def test_evaluate_enabled(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_evaluate

        mock_settings.browser_allow_js_execution = True
        mock_playwright.page.evaluate = AsyncMock(return_value=42)
        result = browser_evaluate("1+1", task_id="task-1")
        assert "42" in result


class TestBrowserWait:
    def test_wait_for_element(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_wait

        mock_playwright.page.get_by_role.return_value.wait_for = AsyncMock()
        result = browser_wait(role="button", name="Submit", task_id="task-1")
        assert "已出现" in result

    def test_wait_no_params_returns_error(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_wait

        result = browser_wait(task_id="task-1")
        assert "ERROR" in result


class TestBrowserScroll:
    def test_scroll_down(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_scroll

        result = browser_scroll(direction="down", amount=300, task_id="task-1")
        assert "已向down滚动" in result
        assert "300" in result

    def test_scroll_invalid_direction(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_scroll

        result = browser_scroll(direction="diagonal", task_id="task-1")
        assert "ERROR" in result


class TestBrowserScrollTo:
    def test_scroll_to_landmark(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_scroll_to

        mock_playwright.page.get_by_role.return_value.count = AsyncMock(return_value=1)
        mock_playwright.page.get_by_role.return_value.first.scroll_into_view_if_needed = AsyncMock()
        result = browser_scroll_to(landmark="footer", task_id="task-1")
        assert "已滚动到区域" in result

    def test_scroll_to_no_params_returns_error(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_scroll_to

        result = browser_scroll_to(task_id="task-1")
        assert "ERROR" in result


class TestBrowserWaitNavigation:
    def test_wait_navigation_success(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_wait_navigation

        result = browser_wait_navigation(task_id="task-1")
        assert "导航完成" in result

    def test_wait_navigation_with_url_contains(self, browser_manager, mock_playwright, mock_settings):
        from agentnexus.tools.browser import browser_wait_navigation

        result = browser_wait_navigation(url_contains="/dashboard", task_id="task-1")
        assert "/dashboard" in result


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_error_format(self):
        from agentnexus.tools.browser import _error

        result = _error("找不到元素", detail="role=button", hint="请snapshot")
        assert "ERROR: 找不到元素" in result
        assert "DETAIL: role=button" in result
        assert "HINT: 请snapshot" in result

    def test_warning_format(self):
        from agentnexus.tools.browser import _warning

        result = _warning("超时", detail="5秒")
        assert "WARNING: 超时" in result
        assert "DETAIL: 5秒" in result

    def test_parse_aria_yaml(self):
        from agentnexus.tools.browser import _parse_aria_yaml

        yaml = (
            '- heading "Welcome" [ref=s1e1]\n'
            '- button "Login" [ref=s1e3]'
        )
        nodes = _parse_aria_yaml(yaml)
        assert len(nodes) == 2
        assert nodes[0]["role"] == "heading"
        assert nodes[0]["name"] == "Welcome"
        assert nodes[0]["ref"] == "s1e1"

    def test_format_a11y_tree(self):
        from agentnexus.tools.browser import _format_a11y_tree

        nodes = [
            {"role": "button", "name": "Login", "ref": "s1e3", "attrs": "", "viewport_status": "visible"},
            {"role": "link", "name": "About", "ref": "s1e4", "attrs": "", "viewport_status": ""},
        ]
        result = _format_a11y_tree(nodes)
        assert '[1] button "Login" ref=s1e3 [visible]' in result
        assert '[2] link "About" ref=s1e4' in result

    def test_in_viewport(self):
        from agentnexus.tools.browser import _in_viewport

        assert _in_viewport([100, 100, 50, 50], 1280, 720) is True
        assert _in_viewport([2000, 2000, 50, 50], 1280, 720) is False
        assert _in_viewport(None, 1280, 720) is True


# ---------------------------------------------------------------------------
# Playwright not installed test
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    def test_playwright_not_installed(self):
        with patch("agentnexus.tools.browser.async_playwright", None):
            from agentnexus.tools.browser import BrowserManager

            BrowserManager._instance = None
            mgr = BrowserManager.instance()
            with pytest.raises(RuntimeError, match="playwright 未安装"):
                _run(mgr.ensure_browser())
            BrowserManager._instance = None


# ---------------------------------------------------------------------------
# Provider registration test
# ---------------------------------------------------------------------------


class TestProviderRegistration:
    def test_browser_provider_in_default_list(self):
        from agentnexus.tools.providers import default_tool_providers

        providers = default_tool_providers()
        provider_names = [p.metadata().name for p in providers]
        assert "browser" in provider_names

    def test_browser_provider_metadata(self):
        from agentnexus.tools.providers import BrowserToolProvider

        provider = BrowserToolProvider()
        meta = provider.metadata()
        assert meta.name == "browser"
        assert "Playwright" in meta.description

    def test_browser_tools_registered(self, mock_playwright, mock_settings):
        from agentnexus.tools.providers import BrowserToolProvider, ToolProviderContext
        from agentnexus.tools.registry import ToolRegistry

        registry = ToolRegistry()
        ctx = ToolProviderContext()
        provider = BrowserToolProvider()
        provider.register(registry, ctx)

        registered = set(registry.list_tools())
        expected = {
            "browser_navigate", "browser_snapshot", "browser_click",
            "browser_type", "browser_read", "browser_screenshot",
            "browser_evaluate", "browser_wait", "browser_scroll",
            "browser_scroll_to", "browser_wait_navigation",
        }
        assert expected.issubset(registered)
