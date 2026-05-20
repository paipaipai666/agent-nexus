import json
import re

from agentnexus.core.config import get_settings
from agentnexus.core.llm import AgentLLM
from agentnexus.prompts import load_prompt
from agentnexus.tools.tool_executor import ToolExecutor

REACT_PROMPT_TEMPLATE = load_prompt("react")

class ReActAgent:
    def __init__(self, llm_client: AgentLLM, tool_executor: ToolExecutor, max_steps: int | None = None,
                 output=None, confirm_fn=None, async_confirm=None, conversation_mode: bool = False):
        self.llm_client = llm_client
        self.tool_executor = tool_executor
        self.max_steps = max_steps if max_steps is not None else get_settings().max_agent_steps
        self._output = output or print
        self._confirm = confirm_fn or self._default_confirm
        self._async_confirm = async_confirm  # TUI async confirmation (coroutine)
        self.conversation_mode = conversation_mode
        self._total_usage: dict = {"input_tokens": 0, "output_tokens": 0}

    # ── tools that always require HITL confirmation ─────────────
    _HITL_TOOLS = {"python_execute", "code_executor", "shell_exec"}

    @property
    def total_usage(self) -> dict:
        return dict(self._total_usage)

    @property
    def model_id(self) -> str:
        return self.llm_client.model

    # ── JSON response parsing (Tier 2-3 fallback) ──────────────────

    @staticmethod
    def _try_fix_json(text: str) -> dict | None:
        """Attempt to repair common LLM JSON errors.

        Fixes: text after closing brace, trailing commas, missing closing brace.
        Returns parsed dict or None.
        """
        if not text:
            return None
        s = text.strip()
        # Find the outermost JSON object
        start = s.find("{")
        if start == -1:
            return None
        # Find matching closing brace by counting nesting
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
            # No closing brace — try appending one
            s = s + "}"
            end = len(s) - 1
        candidate = s[start:end + 1]
        # Remove trailing commas before } or ]
        candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _parse_json_response(text: str) -> dict:
        """Parse LLM text as JSON. Returns unified response dict.

        Success: {"type": "tool_call", "tool": "grep_search", "params": {...}}
              or {"type": "answer", "text": "final answer"}
        Failure: {"type": "error", "reason": "..."}
        """
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
        # Ambiguous JSON — treat as answer if only one key
        if len(data) == 1:
            key = next(iter(data))
            return {"type": "answer", "text": str(data[key])}
        return {"type": "error", "reason": "JSON missing 'tool' or 'answer' key"}

    @staticmethod
    def _build_json_format_section() -> str:
        """Return prompt section instructing JSON output format."""
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
        """Build conversation context from STM, prioritizing compressed summary.

        Args:
            per_msg_limit: max chars per message — raised in GREEN, lowered in BREAK.
        """
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
        use_tool_calling = True  # Tier 1: native tool calling

        memory_context = ""
        if memory_manager:
            memory_context = memory_manager.init_session(question)
            memory_manager.append("user", question)

        # Build tool definitions
        tools = self.tool_executor.registry.to_openai_tools()
        tools_desc = self.tool_executor.getAvailableTools()

        # Build conversation history context ONCE before the loop (fix: avoid
        # injecting system messages mid-conversation, which confuses many LLMs)
        conv_ctx = ""
        if self.conversation_mode and memory_manager:
            conv_ctx = self._build_conversation_context(memory_manager, per_msg_limit=800)

        system_content = self._build_prompt(
            tools_desc, question, "", memory_context, conv_ctx)

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": question},
        ]

        while current_step < self.max_steps:
            current_step += 1
            self._output(f"--- 第 {current_step} 步 ---")

            # ── Tier 1: Native tool calling ──
            think_tools = tools if use_tool_calling else None
            think_rfmt = {"type": "json_object"} if not use_tool_calling else None
            response_text = self.llm_client.think(
                messages=messages, tools=think_tools, response_format=think_rfmt)

            cur = getattr(self.llm_client, "last_usage", {})
            if not isinstance(cur, dict):
                cur = {}
            self._total_usage["input_tokens"] += cur.get("input_tokens", 0)
            self._total_usage["output_tokens"] += cur.get("output_tokens", 0)

            # Check for native tool_calls
            tool_calls = self.llm_client.last_tool_calls
            if not isinstance(tool_calls, list):
                tool_calls = []

            if tool_calls:
                # ── Tier 1 success: native tool calls ──
                if response_text:
                    self._output(f"思考: {response_text.strip()[:500]}")
                if memory_manager:
                    memory_manager.append("assistant", response_text)

                assistant_msg = {"role": "assistant", "content": response_text}
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
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": str(observation),
                    })
                    if memory_manager:
                        memory_manager.append("tool",
                            f"Action: {tc['name']}[{json.dumps(tc['arguments'], ensure_ascii=False)}]\n"
                            f"Observation: {observation}")
                        if memory_manager.has_new_memories():
                            memory_context = memory_manager.refresh_ltm_context(question)

                json_retries = 0
                continue

            # No tool_calls — try JSON text parsing
            if not response_text:
                if use_tool_calling and json_retries < MAX_JSON_RETRIES:
                    json_retries += 1
                    err_hint = f" (LLM last_error: {self.llm_client.last_error[:200]})" if self.llm_client.last_error else ""
                    self._output(f"[重试 {json_retries}/{MAX_JSON_RETRIES}] LLM 返回空响应{err_hint}。提示给出答案...")
                    messages.append({"role": "user", "content": "请根据工具执行结果，直接给出清晰完整的最终答案。"})
                    continue
                err_hint = f" (LLM last_error: {self.llm_client.last_error[:200]})" if self.llm_client.last_error else ""
                self._output(f"错误: LLM 未能返回有效响应。{err_hint}")
                break

            # ── No tool_calls. If Tier 1 was active, text is final answer ──
            if use_tool_calling:
                self._output(f"最终答案: {response_text}")
                if memory_manager:
                    memory_manager.append("system", f"[最终答案] {response_text}")
                    memory_manager.conclude(question, response_text)
                return response_text

            # ── Text mode: try JSON parse ──
            parsed = self._parse_json_response(response_text)

            # ── Tier 3: auto-fix ──
            if parsed["type"] == "error":
                self._output(f"[JSON 解析失败] {parsed['reason']}，尝试修复...")
                fixed = self._try_fix_json(response_text)
                if fixed:
                    parsed = self._parse_json_response(json.dumps(fixed, ensure_ascii=False))

            # ── Tier 4: retry with error feedback ──
            if parsed["type"] == "error" and json_retries < MAX_JSON_RETRIES:
                json_retries += 1
                self._output(f"[JSON 重试 {json_retries}/{MAX_JSON_RETRIES}] {parsed['reason']}")
                messages.append({"role": "assistant", "content": response_text})
                messages.append({"role": "user", "content":
                    f"你的上一次回复不是合法的 JSON。错误: {parsed['reason']}。\n"
                    f"{self._build_json_format_section()}"})
                if memory_manager:
                    memory_manager.append("assistant", response_text)
                # Disable tool calling on retry — force JSON text mode
                use_tool_calling = False
                continue

            # ── Execute or return ──
            if parsed["type"] == "tool_call":
                self._output(f"行动: {parsed['tool']}({', '.join(f'{k}={v}' for k, v in parsed['params'].items())})")
                observation = self._execute_tool(parsed["tool"], parsed["params"])
                self._output(f"观察: {observation}")
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
                use_tool_calling = True  # restore Tier 1 for next iteration
                continue

            if parsed["type"] == "answer":
                answer = parsed["text"]
                self._output(f"最终答案: {answer}")
                if memory_manager:
                    memory_manager.append("system", f"[最终答案] {answer}")
                    memory_manager.conclude(question, answer)
                return answer

            # ── Tier 5: plain text fallback ──
            self._output(f"最终答案: {response_text}")
            if memory_manager:
                memory_manager.append("system", f"[最终答案] {response_text}")
                memory_manager.conclude(question, response_text)
            return response_text

        self._output("已达到最大步数，流程终止。")
        return None

    def _execute_tool(self, name: str, arguments: dict) -> str:
        """Execute a single tool call via the registry."""
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
