"""Computer use tool provider — desktop automation via accessibility APIs."""

from __future__ import annotations

from agentnexus.tools.providers.base import ProviderSpec, ToolProviderContext
from agentnexus.tools.registry import ToolRegistry


class ComputerUseToolProvider:
    """Desktop automation tools via OS-level accessibility APIs."""

    def metadata(self) -> ProviderSpec:
        return ProviderSpec(
            "computer-use",
            description="桌面应用自动化操控 (UIA/AT-SPI/AX)：快照、点击、输入、按键等。",
        )

    def register(self, executor: ToolRegistry, context: ToolProviderContext) -> None:
        from agentnexus.tools.computer_use.tools import (
            computer_click,
            computer_key,
            computer_launch,
            computer_list_windows,
            computer_scroll,
            computer_select,
            computer_snapshot,
            computer_switch_window,
            computer_toggle,
            computer_type,
        )

        before = set(executor.list_tools())

        if context.want("computer_snapshot"):
            executor.register_tool(
                "computer_snapshot",
                "获取桌面应用的可访问性树快照(Accessibility Tree)，返回结构化的元素列表。"
                "参数: app_name(应用名,可选), window_title(窗口标题,可选), "
                "mode(interactive/reading/full,默认reading)。"
                "不指定窗口时使用当前聚焦窗口。"
                "返回元素列表，每个元素包含 role、name、value 等属性。"
                "[最优] 理解桌面应用结构、发现可交互元素、决策下一步操作。"
                "[不适用] 操控浏览器网页(用browser_snapshot)。",
                computer_snapshot,
                param_schema={
                    "type": "object",
                    "properties": {
                        "app_name": {
                            "type": "string",
                            "description": "应用名称（部分匹配）",
                        },
                        "window_title": {
                            "type": "string",
                            "description": "窗口标题（部分匹配）",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["interactive", "reading", "full"],
                            "description": "interactive=仅可交互元素, reading=可交互+阅读元素（默认）, full=全量",
                            "default": "reading",
                        },
                    },
                    "required": [],
                },
                risk_level="low",
                rate_limit_per_min=10,
                recoverable=True,
            )

        if context.want("computer_list_windows"):
            executor.register_tool(
                "computer_list_windows",
                "列出所有可见的桌面窗口。"
                "返回每个窗口的索引、应用名、标题和位置。"
                "用于了解当前有哪些窗口可用。",
                computer_list_windows,
                param_schema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                risk_level="low",
                rate_limit_per_min=30,
                recoverable=True,
            )

        if context.want("computer_switch_window"):
            executor.register_tool(
                "computer_switch_window",
                "切换到指定的桌面窗口。"
                "参数: window_index(窗口索引,从computer_list_windows获取), "
                "app_name(应用名,可选), window_title(窗口标题,可选)。"
                "至少提供一个参数。"
                "切换后后续所有桌面操作将在新窗口上执行。",
                computer_switch_window,
                param_schema={
                    "type": "object",
                    "properties": {
                        "window_index": {
                            "type": "integer",
                            "description": "窗口索引（从 computer_list_windows 获取）",
                        },
                        "app_name": {
                            "type": "string",
                            "description": "应用名称（部分匹配）",
                        },
                        "window_title": {
                            "type": "string",
                            "description": "窗口标题（部分匹配）",
                        },
                    },
                    "required": [],
                },
                risk_level="low",
                rate_limit_per_min=30,
                recoverable=True,
            )

        if context.want("computer_launch"):
            executor.register_tool(
                "computer_launch",
                "启动一个桌面应用程序。"
                "参数: app_path(应用路径或名称,必填), args(命令行参数,可选)。"
                "受应用黑名单和白名单限制。",
                computer_launch,
                param_schema={
                    "type": "object",
                    "properties": {
                        "app_path": {
                            "type": "string",
                            "description": "应用路径或名称（必填）",
                        },
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "命令行参数",
                        },
                    },
                    "required": ["app_path"],
                },
                risk_level="medium",
                rate_limit_per_min=10,
            )

        if context.want("computer_click"):
            executor.register_tool(
                "computer_click",
                "点击桌面应用中的元素。"
                "参数: element_id(元素标识,从snapshot获取,必填), "
                "button(left/right/middle,默认left), "
                "clicks(点击次数,1=单击2=双击,默认1)。",
                computer_click,
                param_schema={
                    "type": "object",
                    "properties": {
                        "element_id": {
                            "type": "string",
                            "description": "元素标识（从 computer_snapshot 获取）",
                        },
                        "button": {
                            "type": "string",
                            "enum": ["left", "right", "middle"],
                            "description": "鼠标按钮",
                            "default": "left",
                        },
                        "clicks": {
                            "type": "integer",
                            "description": "点击次数 (1=单击, 2=双击)",
                            "default": 1,
                        },
                    },
                    "required": ["element_id"],
                },
                risk_level="medium",
                rate_limit_per_min=20,
                recoverable=True,
            )

        if context.want("computer_type"):
            executor.register_tool(
                "computer_type",
                "在桌面应用的输入框中键入文本。"
                "参数: element_id(元素标识,从snapshot获取,必填), "
                "text(要输入的文本,必填), clear(是否先清空,默认true)。",
                computer_type,
                param_schema={
                    "type": "object",
                    "properties": {
                        "element_id": {
                            "type": "string",
                            "description": "元素标识（从 computer_snapshot 获取）",
                        },
                        "text": {
                            "type": "string",
                            "description": "要输入的文本（必填）",
                        },
                        "clear": {
                            "type": "boolean",
                            "description": "是否先清空输入框",
                            "default": True,
                        },
                    },
                    "required": ["element_id", "text"],
                },
                risk_level="medium",
                rate_limit_per_min=20,
                recoverable=True,
            )

        if context.want("computer_key"):
            executor.register_tool(
                "computer_key",
                "按键组合。"
                "参数: keys(按键组合,如 ctrl+s, alt+tab, enter, 必填)。"
                "常用组合: ctrl+c(复制), ctrl+v(粘贴), ctrl+z(撤销), "
                "alt+tab(切换窗口), enter(回车), escape(退出), tab(制表)。",
                computer_key,
                param_schema={
                    "type": "object",
                    "properties": {
                        "keys": {
                            "type": "string",
                            "description": "按键组合（如 ctrl+s, alt+tab, enter）",
                        },
                    },
                    "required": ["keys"],
                },
                risk_level="medium",
                rate_limit_per_min=30,
                recoverable=True,
            )

        if context.want("computer_select"):
            executor.register_tool(
                "computer_select",
                "在下拉框中选择值。"
                "参数: element_id(元素标识,从snapshot获取,必填), value(要选择的值,必填)。",
                computer_select,
                param_schema={
                    "type": "object",
                    "properties": {
                        "element_id": {
                            "type": "string",
                            "description": "元素标识（从 computer_snapshot 获取）",
                        },
                        "value": {
                            "type": "string",
                            "description": "要选择的值（必填）",
                        },
                    },
                    "required": ["element_id", "value"],
                },
                risk_level="medium",
                rate_limit_per_min=20,
                recoverable=True,
            )

        if context.want("computer_toggle"):
            executor.register_tool(
                "computer_toggle",
                "切换复选框或开关状态。"
                "参数: element_id(元素标识,从snapshot获取,必填), "
                "checked(true=勾选/false=取消/null=切换,默认null)。",
                computer_toggle,
                param_schema={
                    "type": "object",
                    "properties": {
                        "element_id": {
                            "type": "string",
                            "description": "元素标识（从 computer_snapshot 获取）",
                        },
                        "checked": {
                            "type": "boolean",
                            "description": "true=勾选, false=取消勾选, null=切换",
                        },
                    },
                    "required": ["element_id"],
                },
                risk_level="medium",
                rate_limit_per_min=20,
                recoverable=True,
            )

        if context.want("computer_scroll"):
            executor.register_tool(
                "computer_scroll",
                "滚动指定区域或屏幕。"
                "参数: element_id(元素标识,可选,不指定则滚动屏幕), "
                "direction(up/down/left/right,默认down), amount(滚动步数,默认3)。",
                computer_scroll,
                param_schema={
                    "type": "object",
                    "properties": {
                        "element_id": {
                            "type": "string",
                            "description": "元素标识（可选，不指定则滚动屏幕）",
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["up", "down", "left", "right"],
                            "description": "滚动方向",
                            "default": "down",
                        },
                        "amount": {
                            "type": "integer",
                            "description": "滚动步数",
                            "default": 3,
                        },
                    },
                    "required": [],
                },
                risk_level="low",
                rate_limit_per_min=30,
            )

        context.mark_registered(executor, before)
