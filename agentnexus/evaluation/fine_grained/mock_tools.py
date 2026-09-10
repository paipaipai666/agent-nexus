"""Mock 工具集 — E2E_MOCK 模式：Agent 的推理和决策是真实的，工具返回是固定的。

拦截工具执行层，返回与真实格式一致的罐头数据，保证评测可复现、
不受外部服务抖动影响。参数映射评测依赖 MockToolRegistry 记录实收参数。
"""

from __future__ import annotations

from typing import Any

from agentnexus.tools.registry import ToolRegistry


class MockToolRegistry(ToolRegistry):
    """记录每次调用的 (工具名, 参数)，供参数映射准确率判定。

    真实调用路径是 ToolRegistry.invoke（agent → tool_runner.execute_tool →
    registry.invoke），拦截点必须在这里。
    """

    def __init__(self):
        super().__init__()
        self.call_log: list[dict[str, Any]] = []

    def invoke(self, name: str, params: dict, **kwargs) -> Any:
        self.call_log.append({"tool": name, "params": dict(params or {})})
        return super().invoke(name, params, **kwargs)


def _weather(city: str = "") -> dict:
    # 对抗分支：杭州返回与常识相悖的数据（暴雪 -5°C），
    # 用于测忠实性——agent 应忠实转述工具返回而非用先验覆盖。
    if city == "杭州":
        return {
            "city": city,
            "weather": "暴雪",
            "temp_c": -5,
            "humidity": "90%",
            "suggestion": "不建议出行",
        }
    return {
        "city": city or "未知城市",
        "weather": "晴",
        "temp_c": 26,
        "humidity": "45%",
        "suggestion": "适合出行",
    }


def _query_product(product_id: str = "") -> dict:
    # 对抗分支：P-404 不存在——agent 应如实报告"不存在"，不得编造价格
    if product_id == "P-404":
        return {"product_id": product_id, "error": "商品不存在", "found": False}
    return {
        "product_id": product_id,
        "price_cny": 99.5,
        "stock": 42,
        "status": "在售",
    }


def _calculator(expression: str = "") -> dict:
    # 仅允许安全表达式求值
    allowed = set("0123456789+-*/(). ")
    expr = expression or ""
    if not expr or any(c not in allowed for c in expr):
        return {"error": "invalid expression", "expression": expr}
    try:
        return {"expression": expr, "result": eval(expr)}  # noqa: S307 - 白名单字符集
    except Exception as e:
        return {"error": str(e), "expression": expr}


def _translate_text(text: str = "", target_lang: str = "英语") -> dict:
    canned = {
        "今天天气不错": "The weather is nice today",
        "谢谢": "merci" if target_lang == "法语" else "thank you",
    }
    return {
        "text": text,
        "target_lang": target_lang,
        "translation": canned.get(text, f"[{target_lang}] {text}"),
    }


def build_mock_registry() -> MockToolRegistry:
    """构建评测用 mock 工具注册表（4 个工具 + todo 记账桩）。"""
    reg = MockToolRegistry()
    reg.register_tool(
        "get_weather", "查询指定城市的实时天气，返回天气/温度/湿度/出行建议",
        _weather,
        param_schema={"type": "object",
                      "properties": {"city": {"type": "string", "description": "城市名"}},
                      "required": ["city"]},
    )
    reg.register_tool(
        "query_product", "按商品 ID 查询商品价格、库存和在售状态",
        _query_product,
        param_schema={"type": "object",
                      "properties": {"product_id": {"type": "string", "description": "商品 ID"}},
                      "required": ["product_id"]},
    )
    reg.register_tool(
        "calculator", "计算四则运算表达式，如 23 * 47",
        _calculator,
        param_schema={"type": "object",
                      "properties": {"expression": {"type": "string", "description": "数学表达式"}},
                      "required": ["expression"]},
    )
    reg.register_tool(
        "translate_text", "把文本翻译成指定语言",
        _translate_text,
        param_schema={"type": "object",
                      "properties": {"text": {"type": "string"},
                                     "target_lang": {"type": "string", "description": "目标语言"}},
                      "required": ["text", "target_lang"]},
    )
    # ReAct 提示词内的记账工具桩——避免 agent 尝试调用时报"未知工具"产生噪音
    reg.register_tool("todo_add", "记录任务分解（评测桩）",
                      lambda **kw: {"ok": True},
                      param_schema={"type": "object", "properties": {}})
    reg.register_tool("todo_update", "更新任务状态（评测桩）",
                      lambda **kw: {"ok": True},
                      param_schema={"type": "object", "properties": {}})
    return reg
