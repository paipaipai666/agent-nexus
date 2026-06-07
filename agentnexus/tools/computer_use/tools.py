"""Computer Use tool functions — sync wrappers for the async manager.

These are the public API functions registered with the ToolRegistry.
Each follows the sync wrapper + _run_async() pattern from browser.py.
"""

from __future__ import annotations

import logging
from typing import Any

from agentnexus.core.config import get_settings
from agentnexus.tools.computer_use.manager import (
    ComputerUseManager,
    _check_hitl_rules,
    _error,
    _is_blocked_app,
    _run_async,
    _warning,
)
from agentnexus.tools.computer_use.snapshot import (
    format_desktop_numbered,
    format_desktop_yaml,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Async implementations
# ---------------------------------------------------------------------------


async def _async_snapshot(
    app_name: str | None,
    window_title: str | None,
    mode: str,
    task_id: str,
) -> str:
    """Get accessibility tree snapshot for a desktop window."""
    settings = get_settings()
    if not settings.computer_use_enabled:
        return _error(
            "桌面自动化功能未启用",
            hint="请在配置中设置 computer_use_enabled: true",
        )

    mgr = ComputerUseManager.instance()

    try:
        elements = await mgr.get_snapshot(task_id, app_name, window_title)
    except Exception as e:
        return _error("获取快照失败", detail=str(e))

    if not elements:
        return _warning("未找到匹配的窗口", detail=f"app={app_name}, title={window_title}")

    max_nodes = settings.computer_use_snapshot_max_nodes

    if mode == "full":
        return format_desktop_yaml(elements, max_nodes=max_nodes * 2)
    elif mode == "interactive":
        # Filter to interactive elements only
        from agentnexus.tools.computer_use.snapshot import INTERACTIVE_ROLES
        filtered = _filter_by_roles(elements, INTERACTIVE_ROLES)
        return format_desktop_numbered(filtered, max_nodes=max_nodes)
    else:
        # "reading" mode (default)
        return format_desktop_numbered(elements, max_nodes=max_nodes)


async def _async_list_windows(task_id: str) -> str:
    """List all visible desktop windows."""
    settings = get_settings()
    if not settings.computer_use_enabled:
        return _error("桌面自动化功能未启用")

    mgr = ComputerUseManager.instance()
    try:
        windows = await mgr.list_windows(task_id)
    except Exception as e:
        return _error("列出窗口失败", detail=str(e))

    if not windows:
        return "没有找到可见窗口。"

    lines = []
    for i, w in enumerate(windows, 1):
        title = w.get("title", "")
        app = w.get("app_name", "")
        bounds = w.get("bounds", (0, 0, 0, 0))
        lines.append(f"[{i}] {app} - \"{title}\" [{bounds[0]},{bounds[1]},{bounds[2]},{bounds[3]}]")
    return "\n".join(lines)


async def _async_switch_window(
    window_index: int | None,
    app_name: str | None,
    window_title: str | None,
    task_id: str,
) -> str:
    """Switch to a specific window."""
    settings = get_settings()
    if not settings.computer_use_enabled:
        return _error("桌面自动化功能未启用")

    mgr = ComputerUseManager.instance()

    try:
        windows = await mgr.list_windows(task_id)
    except Exception as e:
        return _error("列出窗口失败", detail=str(e))

    if not windows:
        return _error("没有找到可见窗口")

    target = None

    if window_index is not None:
        if window_index < 1 or window_index > len(windows):
            return _error(
                f"窗口索引 {window_index} 超出范围",
                detail=f"共 {len(windows)} 个窗口，索引 1-{len(windows)}",
            )
        target = windows[window_index - 1]
    elif app_name or window_title:
        for w in windows:
            if app_name and app_name.lower() not in w.get("app_name", "").lower():
                continue
            if window_title and window_title.lower() not in w.get("title", "").lower():
                continue
            target = w
            break
        if not target:
            return _error(
                "未找到匹配的窗口",
                detail=f"app={app_name}, title={window_title}",
            )
    else:
        return _error("必须提供 window_index、app_name 或 window_title 中的至少一个参数")

    await mgr.set_focused_window(task_id, target)
    title = target.get("title", "")
    app = target.get("app_name", "")
    return f"已切换到窗口: {app} - \"{title}\""


async def _async_launch(
    app_path: str,
    args: list[str] | None,
    task_id: str,
) -> str:
    """Launch an application."""
    settings = get_settings()
    if not settings.computer_use_enabled:
        return _error("桌面自动化功能未启用")

    # Check blocklist
    if _is_blocked_app(app_path):
        return _error(
            f"应用 {app_path} 在黑名单中，禁止启动",
            hint=f"黑名单: {', '.join(settings.computer_use_blocked_apps)}",
        )

    mgr = ComputerUseManager.instance()
    try:
        result = await mgr.launch_app(task_id, app_path, args)
    except Exception as e:
        return _error("启动应用失败", detail=str(e))

    pid = result.get("pid", "")
    return f"已启动应用: {app_path} (PID: {pid})"


async def _async_click(
    element_id: str,
    button: str,
    clicks: int,
    role: str | None,
    name: str | None,
    task_id: str,
) -> str:
    """Click a desktop element."""
    settings = get_settings()
    if not settings.computer_use_enabled:
        return _error("桌面自动化功能未启用")

    # HITL check
    if _check_hitl_rules("click", role, name):
        return _error(
            "操作被 HITL 规则阻止",
            detail=f"click on {role} '{name}'",
            hint="需要用户确认后才能执行",
        )

    mgr = ComputerUseManager.instance()
    try:
        await mgr.click(task_id, element_id, button, clicks)
    except Exception as e:
        return _error("点击失败", detail=str(e))

    action = "双击" if clicks == 2 else "右键点击" if button == "right" else "点击"
    return f"{action}成功: {element_id}"


async def _async_type(
    element_id: str,
    text: str,
    clear: bool,
    role: str | None,
    name: str | None,
    task_id: str,
) -> str:
    """Type text into a desktop element."""
    settings = get_settings()
    if not settings.computer_use_enabled:
        return _error("桌面自动化功能未启用")

    # HITL check
    if _check_hitl_rules("type", role, name):
        return _error(
            "操作被 HITL 规则阻止",
            detail=f"type into {role} '{name}'",
        )

    mgr = ComputerUseManager.instance()
    try:
        await mgr.type_text(task_id, element_id, text, clear)
    except Exception as e:
        return _error("输入失败", detail=str(e))

    return f"输入成功: '{text}'"


async def _async_key(keys: str, task_id: str) -> str:
    """Press a key combination."""
    settings = get_settings()
    if not settings.computer_use_enabled:
        return _error("桌面自动化功能未启用")

    # HITL check for dangerous key combos
    if _check_hitl_rules("key", None, None):
        return _error("操作被 HITL 规则阻止")

    mgr = ComputerUseManager.instance()
    try:
        await mgr.press_key(task_id, keys)
    except Exception as e:
        return _error("按键失败", detail=str(e))

    return f"按键成功: {keys}"


async def _async_select(
    element_id: str,
    value: str,
    role: str | None,
    name: str | None,
    task_id: str,
) -> str:
    """Select a value in a combobox."""
    settings = get_settings()
    if not settings.computer_use_enabled:
        return _error("桌面自动化功能未启用")

    if _check_hitl_rules("select", role, name):
        return _error("操作被 HITL 规则阻止")

    mgr = ComputerUseManager.instance()
    try:
        await mgr.select(task_id, element_id, value)
    except Exception as e:
        return _error("选择失败", detail=str(e))

    return f"选择成功: '{value}'"


async def _async_toggle(
    element_id: str,
    checked: bool | None,
    role: str | None,
    name: str | None,
    task_id: str,
) -> str:
    """Toggle a checkbox or switch."""
    settings = get_settings()
    if not settings.computer_use_enabled:
        return _error("桌面自动化功能未启用")

    if _check_hitl_rules("toggle", role, name):
        return _error("操作被 HITL 规则阻止")

    mgr = ComputerUseManager.instance()
    try:
        await mgr.toggle(task_id, element_id, checked)
    except Exception as e:
        return _error("切换失败", detail=str(e))

    if checked is True:
        return f"已勾选: {element_id}"
    elif checked is False:
        return f"已取消勾选: {element_id}"
    return f"已切换: {element_id}"


async def _async_scroll(
    element_id: str | None,
    direction: str,
    amount: int,
    task_id: str,
) -> str:
    """Scroll an element or the screen."""
    settings = get_settings()
    if not settings.computer_use_enabled:
        return _error("桌面自动化功能未启用")

    if direction not in ("up", "down", "left", "right"):
        return _error(f"无效的滚动方向: {direction}", hint="可选: up, down, left, right")

    mgr = ComputerUseManager.instance()
    try:
        await mgr.scroll(task_id, element_id, direction, amount)
    except Exception as e:
        return _error("滚动失败", detail=str(e))

    return f"滚动成功: {direction} {amount} 步"


# ---------------------------------------------------------------------------
# Sync wrappers (public tool API)
# ---------------------------------------------------------------------------


def computer_snapshot(
    app_name: str | None = None,
    window_title: str | None = None,
    mode: str = "reading",
    *,
    task_id: str = "",
) -> str:
    """获取桌面应用的可访问性树快照。

    返回与浏览器 snapshot 相同风格的 YAML 格式元素列表。
    参数: app_name(应用名,可选), window_title(窗口标题,可选),
    mode(interactive/reading/full,默认reading)。
    不指定窗口时使用当前聚焦窗口。
    """
    return _run_async(_async_snapshot(app_name, window_title, mode, task_id))


def computer_list_windows(*, task_id: str = "") -> str:
    """列出所有可见的桌面窗口。

    返回每个窗口的索引、应用名、标题和位置。
    用于了解当前有哪些窗口可用。
    """
    return _run_async(_async_list_windows(task_id))


def computer_switch_window(
    window_index: int | None = None,
    app_name: str | None = None,
    window_title: str | None = None,
    *,
    task_id: str = "",
) -> str:
    """切换到指定的桌面窗口。

    参数: window_index(窗口索引,从computer_list_windows获取),
    app_name(应用名,可选), window_title(窗口标题,可选)。
    至少提供一个参数。
    """
    return _run_async(_async_switch_window(window_index, app_name, window_title, task_id))


def computer_launch(
    app_path: str,
    args: list[str] | None = None,
    *,
    task_id: str = "",
) -> str:
    """启动一个桌面应用程序。

    参数: app_path(应用路径或名称,必填), args(命令行参数,可选)。
    受应用黑名单和白名单限制。
    """
    return _run_async(_async_launch(app_path, args, task_id))


def computer_click(
    element_id: str,
    button: str = "left",
    clicks: int = 1,
    role: str | None = None,
    name: str | None = None,
    *,
    task_id: str = "",
) -> str:
    """点击桌面应用中的元素。

    参数: element_id(元素标识,从snapshot获取), button(left/right/middle,默认left),
    clicks(点击次数,1=单击2=双击,默认1)。
    """
    return _run_async(_async_click(element_id, button, clicks, role, name, task_id))


def computer_type(
    element_id: str,
    text: str,
    clear: bool = True,
    role: str | None = None,
    name: str | None = None,
    *,
    task_id: str = "",
) -> str:
    """在桌面应用的输入框中键入文本。

    参数: element_id(元素标识,从snapshot获取), text(要输入的文本,必填),
    clear(是否先清空,默认true)。
    """
    return _run_async(_async_type(element_id, text, clear, role, name, task_id))


def computer_key(
    keys: str,
    *,
    task_id: str = "",
) -> str:
    """按键组合。

    参数: keys(按键组合,如 ctrl+s, alt+tab, enter, 必填)。
    """
    return _run_async(_async_key(keys, task_id))


def computer_select(
    element_id: str,
    value: str,
    role: str | None = None,
    name: str | None = None,
    *,
    task_id: str = "",
) -> str:
    """在下拉框中选择值。

    参数: element_id(元素标识), value(要选择的值,必填)。
    """
    return _run_async(_async_select(element_id, value, role, name, task_id))


def computer_toggle(
    element_id: str,
    checked: bool | None = None,
    role: str | None = None,
    name: str | None = None,
    *,
    task_id: str = "",
) -> str:
    """切换复选框或开关状态。

    参数: element_id(元素标识), checked(true=勾选/false=取消/null=切换,默认null)。
    """
    return _run_async(_async_toggle(element_id, checked, role, name, task_id))


def computer_scroll(
    element_id: str | None = None,
    direction: str = "down",
    amount: int = 3,
    *,
    task_id: str = "",
) -> str:
    """滚动指定区域或屏幕。

    参数: element_id(元素标识,可选,不指定则滚动屏幕),
    direction(up/down/left/right,默认down), amount(滚动步数,默认3)。
    """
    return _run_async(_async_scroll(element_id, direction, amount, task_id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _filter_by_roles(
    elements: list,
    roles: set[str],
) -> list:
    """Filter a DesktopElement tree to only include elements with matching roles."""
    result = []
    for elem in elements:
        if elem.role in roles:
            result.append(elem)
        elif elem.children:
            filtered_children = _filter_by_roles(list(elem.children), roles)
            if filtered_children:
                from dataclasses import replace
                result.append(replace(elem, children=tuple(filtered_children)))
    return result
