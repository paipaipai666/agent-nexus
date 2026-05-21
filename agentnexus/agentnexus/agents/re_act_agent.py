import json
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from agentnexus.core.capabilities import SessionCapabilityTracker
from agentnexus.core.config import get_settings
from agentnexus.core.llm import AgentLLM
from agentnexus.prompts import load_prompt
from agentnexus.tools.tool_executor import ToolExecutor

REACT_PROMPT_TEMPLATE = load_prompt("react")


class CallingStrategy(Enum):
    """How the agent communicates with the LLM for tool use."""
    NATIVE_TOOLS = auto()   # tools → LLM native tool_calls (tier 1)
    JSON_MODE = auto()      # response_format={"type":"json_object"} + text parse (tier 2)
    PROMPT_JSON = auto()    # prompt instructs JSON format + text parse (tier 3)
    PLAIN_TEXT = auto()     # pure natural language, no structured output (tier 4)


@dataclass
class AgentStep:
    """Single ReAct decision step — complete audit entity."""
    step_id: int
    strategy_used: CallingStrategy = CallingStrategy.NATIVE_TOOLS
    reasoning_content: str = ""
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    tool_outputs: list[dict] = field(default_factory=list)
    error_message: Optional[str] = None


class ReActAgent:
    def __init__(self, llm_client: AgentLLM, tool_executor: ToolExecutor, max_steps: int | None = None,
                 output=None, confirm_fn=None, async_confirm=None, conversation_mode: bool = False):
        self.llm_client = llm_client
        self.tool_executor = tool_executor
        self.max_steps = max_steps if max_steps is not None else get_settings().max_agent_steps
        self._output = output or print
        self._confirm = confirm_fn or self._default_confirm
        self._async_confirm = async_confirm
        self.conversation_mode = conversation_mode
        self._total_usage: dict = {"input_tokens": 0, "output_tokens": 0}

    _HITL_TOOLS = {"python_execute", "code_executor", "shell_exec"}

    @property
    def total_usage(self) -> dict:
        return dict(self._total_usage)

    @property
    def model_id(self) -> str:
        return self.llm_client.model

    def _select_strategy(self, session_caps: SessionCapabilityTracker) -> CallingStrategy:
        caps = self.llm_client.capabilities
        if session_caps.is_available("tool_calling", caps.supports_tool_calling):
            return CallingStrategy.NATIVE_TOOLS
        elif session_caps.is_available("json_mode", caps.supports_json_mode):
            return CallingStrategy.JSON_MODE
        else:
            return CallingStrategy.PROMPT_JSON

    @staticmethod
    def _robust_json_parse(raw_text: str) -> dict:
        if not raw_text or not raw_text.strip():
            return {"type": "error", "reason": "empty response"}

        clean = raw_text.strip()

        # Tier 1: Strip markdown code fences
        markdown_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', clean, re.DOTALL)
        if markdown_match:
            clean = markdown_match.group(1).strip()

        # Tier 2: Standard parse
        try:
            data = json.loads(clean)
            return ReActAgent._classify_parsed(data)
        except (json.JSONDecodeError, ValueError):
            pass

        # Tier 3: Fix trailing commas and retry
        try:
            fixed = re.sub(r',\s*([}\]])', r'\1', clean)
            data = json.loads(fixed)
            return ReActAgent._classify_parsed(data)
        except (json.JSONDecodeError, ValueError):
            pass

        # Tier 4: Delegate to legacy _try_fix_json (brace matching)
        data = ReActAgent._try_fix_json(clean)
        if data:
            return ReActAgent._classify_parsed(data)

        return {"type": "error", "reason": "JSON parse failed after all repair attempts",
                "raw": raw_text[:500]}

    @staticmethod
    def _classify_parsed(data: dict) -> dict:
        if not isinstance(data, dict):
            return {"type": "error", "reason": "JSON is not an object"}
        if "tool" in data and "params" in data:
            tool = str(data["tool"])
            params = data["params"] if isinstance(data["params"], dict) else {}
            return {"type": "tool_call", "tool": tool, "params": params}
        if "answer" in data:
            return {"type": "answer", "text": str(data["answer"])}
        if len(data) == 1:
            key = next(iter(data))
            return {"type": "answer", "text": str(data[key])}
        return {"type": "error", "reason": "JSON missing 'tool' or 'answer' key"}

    @staticmethod
    def _try_fix_json(text: str) -> dict | None:
        if not text:
            return None
        s = text.strip()
        start = s.find("{")
        if start == -1:
            return None
        depth = 0
        end = -1
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end == -1:
            s = s + "}"
            end = len(s) - 1
        candidate = s[start:end + 1]
        candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _parse_json_response(text: str) -> dict:
        if not text or not text.strip():
            return {"type": "error", "reason": "empty response"}
        data = ReActAgent._try_fix_json(text)
        if not data:
            return {"type": "error", "reason": "not valid JSON"}
        if not isinstance(data, dict):
            return {"type": "error", "reason": "JSON is not an object"}
        if "tool" in data and "params" in data:
            tool = str(data["tool"])
            params = data["params"] if isinstance(data["params"], dict) else {}
            return {"type": "tool_call", "tool": tool, "params": params}
        if "answer" in data:
            return {"type": "answer", "text": str(data["answer"])}
        if len(data) == 1:
            key = next(iter(data))
            return {"type": "answer", "text": str(data[key])}
        return {"type": "error", "reason": "JSON missing 'tool' or 'answer' key"}

    @staticmethod
    def _build_json_format_section() -> str:
        return (
            "== 输出格式（严格遵守）==\n"
            "你必须在每次回复中输出合法的 JSON 对象。\n\n"
            "调用工具时:\n"
            '{"tool": "工具名", "params": {"参数名": "值", ...}}\n\n'
            "给出最终答案时:\n"
            '{"answer": "你的完整回答"}\n\n'
            "答案中的换行用 \\n 表示，双引号用 \\\" 转义。"
        )

    def _default_confirm(self, code: str) -> bool:
        self._output(f"[警告] 即将执行代码 (预览): {code}")
        try:
            response = input("确认执行? [y/N] ").strip().lower()
            return response == "y"
        except (EOFError, OSError):
            return True

    def _build_prompt(self, tools_desc: str, question: str, history_str: str,
                       memory_context: str, conversation_context: str) -> str:
        return REACT_PROMPT_TEMPLATE.format(
            tools=tools_desc,
            question=question,
            history=history_str,
            memory_context=memory_context,
            conversation_context=conversation_context,
        )

    def _build_conversation_context(self, memory_manager, per_msg_limit: int = 500) -> str:
        if not memory_manager or not memory_manager.short_term:
            return ""
        stm = memory_manager.short_term
        summary = stm.get_summary()
        messages = stm.get_all()
        user_assistant_msgs = [m for m in messages if m["role"] in ("user", "assistant")]

        if summary:
            recent = user_assistant_msgs[-3:] if len(user_assistant_msgs) > 3 else user_assistant_msgs
            parts = ["== 对话历史摘要 ==", summary]
            if recent:
                parts.append("\n== 最近对话 ==")
                for m in recent:
                    role_label = "用户" if m["role"] == "user" else "助手"
                    content = m["content"][:per_msg_limit]
                    parts.append(f"{role_label}: {content}")
            return "\n".join(parts) + "\n\n"

        if not user_assistant_msgs:
            return ""
        recent = user_assistant_msgs[-6:]
        lines = []
        for m in recent:
            role_label = "用户" if m["role"] == "user" else "助手"
            content = m["content"][:per_msg_limit]
            lines.append(f"{role_label}: {content}")
        return "== 近期对话 ==\n" + "\n".join(lines) + "\n\n"

    def run(self, question: str, memory_manager=None):
        self._total_usage = {"input_tokens": 0, "output_tokens": 0}
        current_step = 0
        json_retries = 0
        MAX_JSON_RETRIES = 2
        session_caps = SessionCapabilityTracker()
        strategy = self._select_strategy(session_caps)
        thinking_enabled = self.llm_client.capabilities.supports_thinking

        memory_context = ""
        if memory_manager:
            memory_context = memory_manager.init_session(question)
            memory_manager.append("user", question)

        tools = self.tool_executor.registry.to_openai_tools()
        tools_desc = self.tool_executor.getAvailableTools()

        conv_ctx = ""
        if self.conversation_mode and memory_manager:
            conv_ctx = self._build_conversation_context(memory_manager, per_msg_limit=800)

        system_content = self._build_prompt(
            tools_desc, question, "", memory_context, conv_ctx)

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": question},
        ]

        if memory_manager:
            def _rebuild_system_prompt():
                nonlocal memory_context, conv_ctx
                new_tools_desc = self.tool_executor.getAvailableTools()
                if self.conversation_mode:
                    conv_ctx = self._build_conversation_context(memory_manager, per_msg_limit=800)
                new_system = self._build_prompt(
                    new_tools_desc, question, "", memory_context, conv_ctx)
                messages[0] = {"role": "system", "content": new_system}
            memory_manager._on_after_compact = _rebuild_system_prompt

        while current_step < self.max_steps:
            current_step += 1
            self._output(f"--- 第 {current_step} 步 ---")

            # ── Strategy-driven parameter selection ──
            if strategy == CallingStrategy.NATIVE_TOOLS:
                think_tools = tools
                think_rfmt = None
            elif strategy == CallingStrategy.JSON_MODE:
                think_tools = None
                think_rfmt = {"type": "json_object"}
            elif strategy == CallingStrategy.PROMPT_JSON:
                think_tools = None
                think_rfmt = None
                if "== 输出格式" not in messages[-1].get("content", ""):
                    messages[-1]["content"] += "\n\n" + self._build_json_format_section()
            else:
                think_tools = None
                think_rfmt = None

            projection_fn = memory_manager.build_projection if memory_manager else None
            response_text = self.llm_client.think(
                messages=messages, tools=think_tools, response_format=think_rfmt,
                projection_fn=projection_fn, thinking=thinking_enabled)

            step = AgentStep(
                step_id=current_step,
                strategy_used=strategy,
                reasoning_content=self.llm_client.last_reasoning_content,
                content=response_text,
            )

            cur = getattr(self.llm_client, "last_usage", {})
            if not isinstance(cur, dict):
                cur = {}
            self._total_usage["input_tokens"] += cur.get("input_tokens", 0)
            self._total_usage["output_tokens"] += cur.get("output_tokens", 0)

            if memory_manager:
                memory_manager.mark_api_call()

            # ── NATIVE_TOOLS: check for tool_calls ──
            if strategy == CallingStrategy.NATIVE_TOOLS:
                tool_calls = self.llm_client.last_tool_calls
                if not isinstance(tool_calls, list):
                    tool_calls = []

                if tool_calls:
                    step.tool_calls = list(tool_calls)
                    if response_text:
                        self._output(f"思考: {response_text.strip()[:500]}")
                    if memory_manager:
                        memory_manager.append("assistant", response_text)

                    assistant_msg: dict = {"role": "assistant", "content": response_text}
                    if self.llm_client.last_reasoning_content:
                        assistant_msg["reasoning_content"] = self.llm_client.last_reasoning_content
                    assistant_tool_calls = []
                    for tc in tool_calls:
                        assistant_tool_calls.append({
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                            },
                        })
                    if assistant_tool_calls:
                        assistant_msg["tool_calls"] = assistant_tool_calls
                    messages.append(assistant_msg)

                    for tc in tool_calls:
                        self._output(f"行动: {tc['name']}({', '.join(f'{k}={v}' for k, v in tc['arguments'].items())})")
                        observation = self._execute_tool(tc["name"], tc["arguments"])
                        self._output(f"观察: {observation}")
                        step.tool_outputs.append({"tool": tc["name"], "output": observation})
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": str(observation),
                        })
                        if memory_manager:
                            memory_manager.append("tool",
                                f"Action: {tc['name']}[{json.dumps(tc['arguments'], ensure_ascii=False)}]\n"
                                f"Observation: {observation}")
                            if tc['name'] in ('read', 'file_read', 'file_read_text'):
                                filepath = tc['arguments'].get('file_path', tc['arguments'].get('path', ''))
                                if filepath:
                                    memory_manager.bridge_read(str(filepath), str(observation)[:5000])
                            if memory_manager.has_new_memories():
                                memory_context = memory_manager.refresh_ltm_context(question)

                    json_retries = 0
                    continue

                # No tool_calls in NATIVE_TOOLS → model chose to answer directly
                if not response_text:
                    session_caps.mark_failed("tool_calling")
                    strategy = self._select_strategy(session_caps)
                    continue

                self._output(f"最终答案: {response_text}")
                if memory_manager:
                    memory_manager.append("system", f"[最终答案] {response_text}")
                    memory_manager.conclude(question, response_text)
                return response_text

            # ── Non-NATIVE_TOOLS strategies: JSON or prompt-driven ──
            if not response_text:
                if json_retries < MAX_JSON_RETRIES:
                    json_retries += 1
                    err_hint = f" (LLM last_error: {self.llm_client.last_error[:200]})" if self.llm_client.last_error else ""
                    self._output(f"[重试 {json_retries}/{MAX_JSON_RETRIES}] LLM 返回空响应{err_hint}。提示给出答案...")
                    messages.append({"role": "user", "content": "请根据工具执行结果，直接给出清晰完整的最终答案。"})
                    continue
                err_hint = f" (LLM last_error: {self.llm_client.last_error[:200]})" if self.llm_client.last_error else ""
                self._output(f"错误: LLM 未能返回有效响应。{err_hint}")
                break

            # ── Robust JSON parsing ──
            parsed = self._robust_json_parse(response_text)

            if parsed["type"] == "tool_call":
                self._output(f"行动: {parsed['tool']}({', '.join(f'{k}={v}' for k, v in parsed['params'].items())})")
                observation = self._execute_tool(parsed["tool"], parsed["params"])
                self._output(f"观察: {observation}")
                step.tool_calls = [{"name": parsed["tool"], "arguments": parsed["params"]}]
                step.tool_outputs.append({"tool": parsed["tool"], "output": observation})
                messages.append({"role": "assistant", "content": response_text})
                messages.append({"role": "user", "content":
                    f"工具执行结果:\n{observation}\n\n请根据结果继续。如果信息充分，输出最终答案。\n"
                    f"格式: {{\"answer\": \"你的回答\"}}"})
                if memory_manager:
                    memory_manager.append("assistant", response_text)
                    memory_manager.append("tool",
                        f"Action: {parsed['tool']}[{json.dumps(parsed['params'], ensure_ascii=False)}]\n"
                        f"Observation: {observation}")
                    if memory_manager.has_new_memories():
                        memory_context = memory_manager.refresh_ltm_context(question)
                json_retries = 0
                continue

            if parsed["type"] == "answer":
                answer = parsed["text"]
                self._output(f"最终答案: {answer}")
                if memory_manager:
                    memory_manager.append("system", f"[最终答案] {answer}")
                    memory_manager.conclude(question, answer)
                return answer

            # ── Parse error: retry or degrade ──
            if parsed["type"] == "error" and json_retries < MAX_JSON_RETRIES:
                json_retries += 1
                self._output(f"[JSON 重试 {json_retries}/{MAX_JSON_RETRIES}] {parsed['reason']}")
                messages.append({"role": "assistant", "content": response_text})
                messages.append({"role": "user", "content":
                    f"你的上一次回复不是合法的 JSON。错误: {parsed['reason']}。\n"
                    f"{self._build_json_format_section()}"})
                if memory_manager:
                    memory_manager.append("assistant", response_text)
                if json_retries >= MAX_JSON_RETRIES:
                    if strategy == CallingStrategy.JSON_MODE:
                        session_caps.mark_failed("json_mode")
                        strategy = self._select_strategy(session_caps)
                continue

            # ── PLAIN_TEXT final fallback ──
            step.error_message = f"JSON parse failed: {parsed.get('reason', 'unknown')}"
            self._output(f"最终答案: {response_text}")
            if memory_manager:
                memory_manager.append("system", f"[最终答案] {response_text}")
                memory_manager.conclude(question, response_text)
            return response_text

        self._output("已达到最大步数，流程终止。")
        return None

    def _execute_tool(self, name: str, arguments: dict) -> str:
        need_hitl = name in self._HITL_TOOLS
        try:
            return str(self.tool_executor.registry.invoke(
                name=name,
                params=arguments,
                caller="react_agent",
                hitl_approver=self._confirm if need_hitl else None,
            ))
        except Exception as e:
            return f"错误: 工具 '{name}' 执行失败: {e}"
