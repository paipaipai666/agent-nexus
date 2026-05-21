"""
ReAct Agent 兜底机制 —— 专为不支持 Tool Calling 的模型设计
包含三层兜底策略：
  Layer 1: 严格 Prompt + 多模式正则解析
  Layer 2: 自我修正（解析失败 → 反馈错误 → 模型重试）
  Layer 3: 强制 JSON 模式（最后防线）
"""

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

import anthropic

client = anthropic.Anthropic()

# ============================================================
# 工具层
# ============================================================

TOOLS: dict[str, Callable] = {
    "search": lambda q: f"搜索 '{q}' 的结果：这是关于{q}的模拟信息",
    "calculator": lambda expr: str(eval(re.sub(r"[^0-9+\-*/().\s]", "", expr))),
    "get_time": lambda _: (
        __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ),
    "get_weather": lambda city: {"北京": "晴 28°C", "上海": "多云 25°C"}.get(
        city, "未知城市"
    ),
}

TOOL_DESC = "\n".join(
    [
        "- search[query]       : 搜索信息",
        "- calculator[expr]    : 计算数学表达式",
        "- get_time[any]       : 获取当前时间",
        "- get_weather[city]   : 查询城市天气",
        "- finish[answer]      : 返回最终答案",
    ]
)


# ============================================================
# Layer 1：严格 Prompt + 多模式正则解析
# ============================================================

# 主格式（标准 ReAct）
ACTION_PATTERNS = [
    # 标准格式：Action: tool[param]
    re.compile(r"Action\s*:\s*(\w+)\s*\[([^\]]*)\]", re.IGNORECASE),
    # 变体：Action: tool("param")  或  tool('param')
    re.compile(r'Action\s*:\s*(\w+)\s*\(["\']?([^"\')\n]*)["\']?\)', re.IGNORECASE),
    # 变体：**Action**: tool[param]（模型喜欢加 markdown）
    re.compile(r"\*+Action\*+\s*:\s*(\w+)\s*\[([^\]]*)\]", re.IGNORECASE),
    # 变体：`tool[param]`（代码块包裹）
    re.compile(r"`(\w+)\[([^\]]*)\]`"),
    # 最宽松：任意位置的 tool_name[param]
    re.compile(r"\b(search|calculator|get_time|get_weather|finish)\s*\[([^\]]*)\]"),
]

STRICT_SYSTEM_PROMPT = """你是一个 ReAct Agent，必须严格按照以下格式输出，不得偏离。

可用工具：
{tool_desc}

【强制输出格式】每一步必须且只能输出：
Thought: <你的推理，一行>
Action: <工具名>[<参数>]

规则：
1. Thought 和 Action 必须同时出现，缺一不可
2. Action 后面不能有任何多余文字
3. 参数不能包含中括号 [ ]
4. 没有最终答案时不能调用 finish
5. 有了足够信息后必须调用 finish[答案]

错误示例（禁止）：
  Action: search("query")      ← 不能用圆括号
  Action: 搜索[query]          ← 工具名必须是英文
  思考：我需要搜索             ← 不能用中文标签

正确示例：
  Thought: 我需要查询北京天气
  Action: get_weather[北京]
"""


def parse_action_multilayer(text: str) -> tuple[Optional[str], Optional[str]]:
    """
    多层正则兜底解析
    按优先级逐一尝试，任意一个命中即返回
    """
    for i, pattern in enumerate(ACTION_PATTERNS):
        match = pattern.search(text)
        if match:
            tool, param = match.group(1).strip(), match.group(2).strip()
            if i > 0:
                print(f"  ⚠️  使用备用解析规则 #{i + 1} 成功")
            return tool.lower(), param
    return None, None


# ============================================================
# Layer 2：自我修正（Self-Correction）
# ============================================================

CORRECTION_PROMPT = """你上一次的输出格式有误，无法解析。

错误输出：
{bad_output}

错误原因：{reason}

请严格按照以下格式重新输出：
Thought: <你的推理>
Action: <工具名>[<参数>]

可用工具名（只能用这些）：search, calculator, get_time, get_weather, finish
"""


def call_model_with_self_correction(
    messages: list,
    system: str,
    max_retries: int = 3,
) -> tuple[str, list]:
    """
    调用模型，解析失败时自动注入错误反馈让模型重试
    返回 (解析后的文本, 更新后的messages)
    """
    retry_messages = messages.copy()

    for attempt in range(max_retries):
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",  # 假设这是一个能力有限的模型
            max_tokens=256,
            system=system,
            messages=retry_messages,
            stop_sequences=["Observation:"],
        )
        output = response.content[0].text.strip()

        # 尝试解析
        tool, param = parse_action_multilayer(output)

        if tool:
            # 解析成功，返回原始输出
            return output, retry_messages

        # 解析失败 → 构造纠错消息，让模型重试
        print(f"  ❌ 第 {attempt + 1} 次解析失败，触发自我修正...")
        print(f"     原始输出: {repr(output[:80])}")

        # 找出具体错误原因
        reason = diagnose_format_error(output)

        correction = CORRECTION_PROMPT.format(
            bad_output=output,
            reason=reason,
        )
        # 把错误输出和纠错指令加入历史
        retry_messages = retry_messages + [
            {"role": "assistant", "content": output},
            {"role": "user", "content": correction},
        ]

    # 全部重试失败，返回最后一次原始输出（由调用方处理）
    print(f"  💀 自我修正 {max_retries} 次后仍失败")
    return output, retry_messages


def diagnose_format_error(text: str) -> str:
    """诊断格式错误原因，用于给模型更精准的纠错提示"""
    if "Action" not in text and "action" not in text.lower():
        return "缺少 Action 行，必须输出 Action: 工具名[参数]"
    if re.search(r"Action\s*:\s*\w+\s*\(", text):
        return "Action 参数使用了圆括号 ()，必须改为方括号 []"
    if re.search(r"Action\s*:\s*[\u4e00-\u9fff]", text):
        return "工具名使用了中文，工具名必须是英文"
    if not re.search(r"Thought", text, re.IGNORECASE):
        return "缺少 Thought 行"
    return "Action 格式不正确，请严格使用 Action: 工具名[参数] 格式"


# ============================================================
# Layer 3：强制 JSON 模式（终极兜底）
# ============================================================

JSON_SYSTEM_PROMPT = """你是一个 ReAct Agent。

可用工具：
{tool_desc}

【极其重要】你的每次回复必须是且只能是一个 JSON 对象，绝对不能有任何其他文字，格式如下：

调用工具时：
{{"thought": "推理过程", "action": "工具名", "param": "参数"}}

给出最终答案时：
{{"thought": "已得到答案", "action": "finish", "param": "最终答案"}}

合法的工具名：search, calculator, get_time, get_weather, finish
"""

JSON_CLEANUP_PATTERNS = [
    # 去掉 markdown 代码块
    re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL),
    # 提取第一个 {...}
    re.compile(r"\{[^{}]*\}", re.DOTALL),
]


def parse_json_output(text: str) -> Optional[dict]:
    """
    健壮的 JSON 解析：先直接解析，失败则逐步清洗
    """
    text = text.strip()

    # 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试各种清洗方式
    for pattern in JSON_CLEANUP_PATTERNS:
        match = pattern.search(text)
        if match:
            candidate = match.group(1) if match.lastindex else match.group(0)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    return None


# ============================================================
# 完整 Agent：三层兜底串联
# ============================================================


class ParseStrategy(Enum):
    REGEX = "regex"  # Layer 1
    SELF_CORRECTION = "sc"  # Layer 2
    JSON_MODE = "json"  # Layer 3


@dataclass
class StepResult:
    tool: str
    param: str
    strategy_used: ParseStrategy
    raw_output: str


def run_robust_react_agent(
    user_question: str,
    max_steps: int = 8,
    enable_json_fallback: bool = True,
) -> Optional[str]:
    """
    健壮 ReAct Agent，三层兜底：
      1. 多模式正则解析
      2. 自我修正重试
      3. 切换 JSON 模式
    """
    print(f"\n{'█' * 60}")
    print(f"  问题: {user_question}")
    print(f"{'█' * 60}")

    system = STRICT_SYSTEM_PROMPT.format(tool_desc=TOOL_DESC)
    messages = [{"role": "user", "content": user_question}]

    # 记录连续失败次数，用于决定是否升级到 JSON 模式
    consecutive_failures = 0
    use_json_mode = False

    for step in range(max_steps):
        print(f"\n── Step {step + 1} {'[JSON模式]' if use_json_mode else ''} ──")

        # ── JSON 模式（Layer 3）──
        if use_json_mode:
            json_system = JSON_SYSTEM_PROMPT.format(tool_desc=TOOL_DESC)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                system=json_system,
                messages=messages,
            )
            raw = response.content[0].text.strip()
            print(f"模型输出: {raw}")

            parsed = parse_json_output(raw)
            if parsed and "action" in parsed:
                result = StepResult(
                    tool=parsed["action"].lower(),
                    param=str(parsed.get("param", "")),
                    strategy_used=ParseStrategy.JSON_MODE,
                    raw_output=raw,
                )
                print(f"✓ JSON解析成功 | 思考: {parsed.get('thought', '')}")
            else:
                print("💀 JSON 模式也解析失败，跳过本步")
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    print("终止：连续失败 3 次")
                    break
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": "你的输出不是合法JSON，请只输出一个JSON对象，不要有任何其他文字。",
                    }
                )
                continue

        else:
            # ── Layer 1 + Layer 2 ──
            raw, messages_after_correction = call_model_with_self_correction(
                messages, system, max_retries=2
            )
            print(f"模型输出:\n{raw}")

            tool, param = parse_action_multilayer(raw)

            if tool:
                result = StepResult(
                    tool=tool,
                    param=param,
                    strategy_used=ParseStrategy.REGEX,
                    raw_output=raw,
                )
                consecutive_failures = 0
                # 同步可能已修正的消息历史
                messages = messages_after_correction
            else:
                # Layer 1+2 全败，升级到 JSON 模式
                consecutive_failures += 1
                if enable_json_fallback:
                    print("  🔄 升级到 JSON 模式")
                    use_json_mode = True
                    # 重置消息历史（JSON 模式用不同 system prompt）
                    messages = [{"role": "user", "content": user_question}]
                    continue
                else:
                    print("  终止：解析失败且未启用 JSON 兜底")
                    break

        # ── 执行工具 ──
        print(
            f"🔧 调用: {result.tool}[{result.param}] (via {result.strategy_used.value})"
        )

        if result.tool == "finish":
            print(f"\n✅ 最终答案: {result.param}")
            return result.param

        handler = TOOLS.get(result.tool)
        if not handler:
            observation = (
                f"未知工具 '{result.tool}'，可用工具: {', '.join(TOOLS.keys())}"
            )
        else:
            try:
                observation = handler(result.param)
            except Exception as e:
                observation = f"工具执行出错: {e}"

        print(f"   Observation: {observation}")

        # 更新消息历史
        if use_json_mode:
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {"role": "user", "content": f'{{"observation": "{observation}"}}'}
            )
        else:
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {"role": "user", "content": f"Observation: {observation}\n继续。"}
            )

    return None


# ============================================================
# 额外工具：格式检测 & 模型能力探针
# ============================================================


def probe_model_format_capability(model: str, n: int = 3) -> dict:
    """
    探针：测试模型格式遵从能力
    用于在初始化时决定是否直接进入 JSON 模式
    """
    test_q = "北京天气怎么样"
    system = STRICT_SYSTEM_PROMPT.format(tool_desc=TOOL_DESC)
    success = 0

    for _ in range(n):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=128,
                system=system,
                messages=[{"role": "user", "content": test_q}],
                stop_sequences=["Observation:"],
            )
            tool, _ = parse_action_multilayer(resp.content[0].text)
            if tool:
                success += 1
        except Exception:
            pass

    rate = success / n
    return {
        "model": model,
        "format_success_rate": rate,
        "recommendation": "tool_use"
        if rate >= 0.8
        else ("json_mode" if rate >= 0.4 else "needs_finetune"),
    }


# ============================================================
# 运行示例
# ============================================================

if __name__ == "__main__":
    # 测试 1：正常流程
    run_robust_react_agent("上海今天天气怎么样？")

    # 测试 2：需要计算
    run_robust_react_agent("(123 + 456) * 2 等于多少？")

    # 测试 3：多步骤
    run_robust_react_agent("现在几点了？同时帮我查一下北京天气。")

    # 模型能力探针示例（实际使用时在 Agent 初始化时调用）
    # result = probe_model_format_capability("claude-haiku-4-5-20251001")
    # print(result)
    # → {'model': '...', 'format_success_rate': 0.9, 'recommendation': 'tool_use'}
