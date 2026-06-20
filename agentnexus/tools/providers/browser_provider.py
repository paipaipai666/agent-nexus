"""Browser tool provider — browser automation via Playwright."""

from __future__ import annotations

from agentnexus.tools.providers.base import ProviderSpec, ToolProviderContext
from agentnexus.tools.registry import ToolRegistry


class BrowserToolProvider:
    """Browser automation tools via Playwright."""

    def metadata(self) -> ProviderSpec:
        return ProviderSpec(
            "browser",
            description="浏览器自动化操控 (Playwright)：导航、点击、输入、截图、页面快照等。",
        )

    def register(self, executor: ToolRegistry, context: ToolProviderContext) -> None:
        from agentnexus.tools.browser import (
            browser_click,
            browser_dismiss_popup,
            browser_evaluate,
            browser_list_pages,
            browser_navigate,
            browser_read,
            browser_screenshot,
            browser_scroll,
            browser_scroll_to,
            browser_snapshot,
            browser_switch_page,
            browser_type,
            browser_wait,
            browser_wait_navigation,
        )

        before = set(executor.list_tools())

        if context.want("browser_navigate"):
            executor.register_tool(
                "browser_navigate",
                "导航浏览器到指定URL。参数: url(必填), wait_until(load/domcontentloaded/networkidle,默认load)。"
                "返回页面标题、URL、readyState和页面结构概览。"
                "⚠️ 导航后所有旧ref失效，必须重新调用browser_snapshot获取新ref。"
                "[不适用] 抓取已知URL内容(用web_fetch, 更快)。",
                browser_navigate,
                param_schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "目标URL（必填）"},
                        "wait_until": {
                            "type": "string",
                            "enum": ["load", "domcontentloaded", "networkidle"],
                            "description": "等待策略。SPA建议用networkidle",
                            "default": "load",
                        },
                    },
                    "required": ["url"],
                },
                risk_level="low",
                rate_limit_per_min=10,
                recoverable=True,
            )

        if context.want("browser_snapshot"):
            executor.register_tool(
                "browser_snapshot",
                "获取当前页面的可访问性快照(Accessibility Tree)，返回结构化的页面元素列表。"
                "参数: scope(CSS选择器限定区域,可选), mode(interactive/reading/full,默认reading), "
                "include_offscreen(是否包含视口外元素,默认false)。"
                "返回Skeleton(页面结构)和Detail(可交互元素列表,含ref索引)。"
                "⚠️ ref仅在当前页面视图有效，页面跳转后需重新snapshot。"
                "[最优] 理解页面结构、发现可交互元素、决策下一步操作。"
                "[不适用] 提取大段文本内容(用browser_read), 批量抓取已知URL(用web_fetch)。",
                browser_snapshot,
                param_schema={
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "description": "CSS选择器限定区域（如 #login-form, .modal）",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["interactive", "reading", "full"],
                            "description": "interactive=仅可交互元素, reading=可交互+阅读元素（默认）, full=全量",
                            "default": "reading",
                        },
                        "include_offscreen": {
                            "type": "boolean",
                            "description": "是否包含视口外元素",
                            "default": False,
                        },
                        "wait_stable": {
                            "type": "boolean",
                            "description": "是否等待DOM稳定后再快照（默认true，快速轮询时可设false）",
                            "default": True,
                        },
                        "include_generic": {
                            "type": "boolean",
                            "description": "是否包含generic等非语义节点（抖音/B站等SPA页面内容在非语义div中时设true）",
                            "default": False,
                        },
                    },
                    "required": [],
                },
                risk_level="low",
                rate_limit_per_min=10,
                recoverable=True,
                concurrency_safe=True,
            )

        if context.want("browser_click"):
            executor.register_tool(
                "browser_click",
                "点击页面元素。定位优先级: pos坐标 → role+name → name → selector。"
                "参数: pos(坐标'x,y,w,h',从snapshot的box值复制,最可靠)/role+name/selector(至少一个)。"
                "点击后不自动等待，如需等待DOM更新请用browser_wait。"
                "无名称的元素必须用pos定位。",
                browser_click,
                param_schema={
                    "type": "object",
                    "properties": {
                        "pos": {"type": "string", "description": "坐标x,y,w,h(从snapshot的box值复制)"},
                        "role": {"type": "string", "description": "元素角色（如 button, link）"},
                        "name": {"type": "string", "description": "元素名称"},
                        "selector": {"type": "string", "description": "CSS选择器"},
                        "double_click": {"type": "boolean", "description": "是否双击", "default": False},
                    },
                    "required": [],
                },
                risk_level="medium",
                rate_limit_per_min=20,
                recoverable=True,
            )

        if context.want("browser_type"):
            executor.register_tool(
                "browser_type",
                "在输入框中键入文本。定位优先级: pos坐标 → role+name → selector。"
                "参数: text(要输入的文本), pos(坐标'x,y,w,h',从snapshot的box值复制)/role+name/selector(至少一个), "
                "clear(是否先清空,默认true), press_enter(输入后是否按回车)。"
                "无名称的输入框必须用pos定位。",
                browser_type,
                param_schema={
                    "type": "object",
                    "properties": {
                        "pos": {"type": "string", "description": "坐标 'x,y,w,h'（从snapshot的[box=x,y,w,h]复制）"},
                        "role": {"type": "string", "description": "元素角色（如 textbox, searchbox）"},
                        "name": {"type": "string", "description": "元素名称"},
                        "selector": {"type": "string", "description": "CSS选择器"},
                        "text": {"type": "string", "description": "要输入的文本"},
                        "clear": {"type": "boolean", "description": "是否先清空输入框", "default": True},
                        "press_enter": {"type": "boolean", "description": "输入后是否按回车", "default": False},
                    },
                    "required": ["text"],
                },
                risk_level="medium",
                rate_limit_per_min=20,
                recoverable=True,
            )

        if context.want("browser_read"):
            executor.register_tool(
                "browser_read",
                "阅读页面元素的文本内容。职责: 提取和阅读内容（与snapshot的决策操作分离）。"
                "参数: selector(CSS选择器)/ref(元素ref), max_chars(最大返回字符数,默认5000)。"
                "不指定selector和ref时读取整个页面body。"
                "内容被截断时末尾附带截断提示。"
                "[最优] 提取页面某区域的文本内容, 阅读文章/表格数据。"
                "[不适用] 判断页面有哪些可交互元素(用browser_snapshot), 批量抓取已知URL(用web_fetch)。",
                browser_read,
                param_schema={
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS选择器（指定区域）"},
                        "ref": {"type": "string", "description": "元素ref"},
                        "max_chars": {"type": "integer", "description": "最大返回字符数", "default": 5000},
                    },
                    "required": [],
                },
                risk_level="low",
                rate_limit_per_min=20,
                recoverable=True,
                concurrency_safe=True,
            )

        if context.want("browser_screenshot"):
            executor.register_tool(
                "browser_screenshot",
                "截取当前页面截图。目标用户: 人类开发者（调试用），Agent无法消费图片文件。"
                "参数: path(保存路径,可选), full_page(是否截取完整页面,默认false)。"
                "[不适用] Agent需要页面结构信息(用browser_snapshot)。",
                browser_screenshot,
                param_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "保存路径（可选）"},
                        "full_page": {"type": "boolean", "description": "是否截取完整页面", "default": False},
                    },
                    "required": [],
                },
                risk_level="medium",
                rate_limit_per_min=10,
                concurrency_safe=True,
            )

        if context.want("browser_evaluate"):
            executor.register_tool(
                "browser_evaluate",
                "在页面上执行JavaScript表达式。⚠️ 默认禁用！需config中browser_allow_js_execution=true。"
                "未开启时返回错误。开启后require_hitl=true（无条件），每次调用都需用户确认。",
                browser_evaluate,
                param_schema={
                    "type": "object",
                    "properties": {
                        "expression": {"type": "string", "description": "JavaScript表达式"},
                    },
                    "required": ["expression"],
                },
                risk_level="high",
                require_hitl=True,
                timeout_sec=60,
                rate_limit_per_min=5,
            )

        if context.want("browser_wait"):
            executor.register_tool(
                "browser_wait",
                "等待元素出现或文本出现。用于click后等待DOM更新。"
                "参数: role+name/ref/text(至少一个), timeout(超时毫秒数,默认5000)。"
                "超时后返回WARNING（不报错）。",
                browser_wait,
                param_schema={
                    "type": "object",
                    "properties": {
                        "role": {"type": "string", "description": "元素角色"},
                        "name": {"type": "string", "description": "元素名称"},
                        "ref": {"type": "string", "description": "元素ref"},
                        "text": {"type": "string", "description": "等待出现的文本"},
                        "timeout": {"type": "integer", "description": "超时毫秒数", "default": 5000},
                    },
                    "required": [],
                },
                risk_level="low",
                rate_limit_per_min=10,
                concurrency_safe=True,
            )

        if context.want("browser_scroll"):
            executor.register_tool(
                "browser_scroll",
                "滚动页面。参数: direction(up/down/left/right,默认down), amount(像素数,默认500)。",
                browser_scroll,
                param_schema={
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "string",
                            "enum": ["up", "down", "left", "right"],
                            "description": "滚动方向",
                            "default": "down",
                        },
                        "amount": {"type": "integer", "description": "滚动像素数", "default": 500},
                    },
                    "required": [],
                },
                risk_level="low",
                rate_limit_per_min=30,
            )

        if context.want("browser_scroll_to"):
            executor.register_tool(
                "browser_scroll_to",
                "滚动到指定元素。定位优先级: landmark(语义区域名) → ref → selector。"
                "参数: landmark/ref/selector(至少一个)。"
                "landmark示例: footer, search-results, main-content。",
                browser_scroll_to,
                param_schema={
                    "type": "object",
                    "properties": {
                        "landmark": {"type": "string", "description": "语义区域名（如 footer, search-results）"},
                        "ref": {"type": "string", "description": "元素ref（来自browser_snapshot）"},
                        "selector": {"type": "string", "description": "CSS选择器"},
                    },
                    "required": [],
                },
                risk_level="low",
                rate_limit_per_min=20,
            )

        if context.want("browser_wait_navigation"):
            executor.register_tool(
                "browser_wait_navigation",
                "等待页面导航完成。仅用于click/submit触发跳转后等待。"
                "参数: url_contains(等待URL包含此字符串,可选), timeout(超时毫秒数,默认10000)。"
                "与browser_navigate的wait_until不重叠。"
                "超时后返回WARNING + 当前URL和标题。",
                browser_wait_navigation,
                param_schema={
                    "type": "object",
                    "properties": {
                        "url_contains": {
                            "type": "string",
                            "description": "等待URL包含此字符串（如 /dashboard）",
                        },
                        "timeout": {"type": "integer", "description": "超时毫秒数", "default": 10000},
                    },
                    "required": [],
                },
                risk_level="low",
                rate_limit_per_min=10,
                concurrency_safe=True,
            )

        if context.want("browser_dismiss_popup"):
            executor.register_tool(
                "browser_dismiss_popup",
                "自动检测并关闭页面弹窗（登录弹窗、cookie同意框、广告弹窗等）。"
                "按优先级尝试: 关闭按钮 → 取消按钮 → Escape键。"
                "无需参数，自动检测当前页面弹窗。",
                browser_dismiss_popup,
                param_schema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                risk_level="low",
                rate_limit_per_min=10,
            )

        if context.want("browser_list_pages"):
            executor.register_tool(
                "browser_list_pages",
                "列出当前任务所有打开的标签页。"
                "返回每个标签页的索引、标题、URL和是否为当前活跃页。"
                "用于在多标签场景下了解有哪些页面可切换。",
                browser_list_pages,
                param_schema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                risk_level="low",
                rate_limit_per_min=30,
                concurrency_safe=True,
            )

        if context.want("browser_switch_page"):
            executor.register_tool(
                "browser_switch_page",
                "切换到指定索引的标签页。"
                "参数: index(标签页索引,从browser_list_pages获取)。"
                "切换后后续所有浏览器操作将在新标签页上执行。",
                browser_switch_page,
                param_schema={
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "标签页索引（从 browser_list_pages 获取）",
                        },
                    },
                    "required": ["index"],
                },
                risk_level="low",
                rate_limit_per_min=30,
            )

        context.mark_registered(executor, before)
