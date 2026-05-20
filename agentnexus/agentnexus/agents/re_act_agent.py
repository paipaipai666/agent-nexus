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

    def _parse_output(self, text: str):
        """Three-layer fallback: strict XML → diagnose failure → legacy text format.

        Returns (thought, action, failure_reason). failure_reason is None on success.
        """
        # ── Layer 1: strict XML ──
        thought_match = re.search(r"<thought>\s*(.*?)\s*</thought>", text, re.DOTALL)
        action_match = re.search(r"(<action\s+[^>]*>.*?</action>)", text, re.DOTALL)
        if action_match:
            return (
                thought_match.group(1).strip() if thought_match else None,
                action_match.group(1).strip(),
                None,
            )

        # ── Layer 2: diagnose WHY XML failed ──
        has_action_open = "<action" in text
        has_action_close = "</action>" in text
        if has_action_open and not has_action_close:
            reason = "XML截断: 缺少 </action> 闭合标签，LLM输出可能被截断"
        elif has_action_open and has_action_close:
            # Tag present but didn't match strict regex — likely missing space after <action
            if "<actiontype=" in text or "<action " not in text:
                reason = "XML格式错误: <action 后缺少空格"
            else:
                reason = "XML格式错误: action标签不规范"
        elif thought_match:
            reason = "XML格式错误: 有 <thought> 但无有效 <action>"
        else:
            reason = "XML缺失: LLM未输出 <action> 标签"

        # ── Layer 3: fallback to legacy text format ──
        t_match = re.search(r"Thought:\s*(.*?)(?=\nAction:|$)", text, re.DOTALL)
        a_match = re.search(r"Action:\s*(.*?)$", text, re.DOTALL)
        if a_match:
            return (
                t_match.group(1).strip() if t_match else None,
                a_match.group(1).strip(),
                reason,  # succeeded via fallback — reason is informational
            )

        return (None, None, reason)

    def _parse_finish(self, action_text: str):
        """Parse Finish — XML or legacy text format."""
        # XML format
        match = re.search(r'<action\s+type="finish">\s*(.*?)\s*</action>', action_text, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Legacy format: Finish[answer] or Finish: answer or Finish answer
        m = re.match(r"Finish\s*\[\s*(.*)\s*\]", action_text, re.DOTALL)
        if m:
            return m.group(1).strip()
        m = re.match(r"Finish\s*[:：]\s*(.*)", action_text, re.DOTALL)
        if m:
            return m.group(1).strip()
        if action_text.strip() == "Finish":
            return ""
        remaining = re.sub(r"^Finish\s+", "", action_text, count=1)
        if remaining and remaining != action_text:
            return remaining.strip()
        return None

    def _parse_action(self, action_text: str) -> tuple[str | None, str | dict | None]:
        """Parse tool action — XML or legacy text format.

        Returns (tool_name, params). params is:
        - str: legacy flat text format (backward compatible)
        - dict: structured XML child-element format
        - None: parse failure
        """
        # XML format: <action type="tool" name="web_search">...</action>
        match = re.search(
            r'<action\s+type="tool"\s+name="([^"]*)">\s*(.*?)\s*</action>',
            action_text, re.DOTALL,
        )
        if match:
            tool_name = match.group(1)
            inner = match.group(2).strip()
            # Detect structured format: inner starts with < → XML child elements
            if inner.startswith("<"):
                params = self._parse_structured_params(inner)
                if params is not None:
                    return tool_name, params
            # Unstructured → return raw string (backward compatible)
            return tool_name, inner

        # Legacy format: tool_name[params]
        m = re.match(r"([\w-]+)\[(.*)\]", action_text, re.DOTALL)
        if m:
            return m.group(1), m.group(2)
        return None, None

    def _normalize_params(self, tool_name: str, params) -> dict:
        """Convert string params to dict using the tool's param schema.

        When LLM outputs raw string params (legacy format), wrap them
        into a dict keyed by the first required field, or the first
        property if no required fields are declared.
        """
        if isinstance(params, dict):
            return params
        entry = self.tool_executor.registry._tools.get(tool_name)
        if entry:
            meta, _ = entry
            required = meta.param_schema.get("required", [])
            if required:
                return {required[0]: params}
            props = meta.param_schema.get("properties", {})
            if props:
                first_key = next(iter(props))
                return {first_key: params}
        return {"input": params}

    @staticmethod
    def _parse_structured_params(inner: str) -> dict | None:
        """Parse XML child elements inside <action> into a dict.

        Input: "<query>北京天气</query><max_results>10</max_results>"
        Output: {"query": "北京天气", "max_results": 10}
        """
        pattern = r"<(\w+)>(.*?)</\1>"
        matches = re.findall(pattern, inner, re.DOTALL)
        if not matches:
            return None
        params: dict = {}
        for key, value in matches:
            value = value.strip()
            if value.isdigit():
                params[key] = int(value)
            elif value.lower() in ("true", "false"):
                params[key] = value.lower() == "true"
            else:
                params[key] = value
        return params

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

        history = []
        current_step = 0

        memory_context = ""
        conversation_context = ""
        if memory_manager:
            memory_context = memory_manager.init_session(question)
            memory_manager.append("user", question)
            if self.conversation_mode:
                conversation_context = self._build_conversation_context(memory_manager, per_msg_limit=800)

        while current_step < self.max_steps:
            current_step += 1
            self._output(f"--- 第 {current_step} 步 ---")

            # Periodic LTM context refresh
            if memory_manager and current_step % 3 == 0:
                memory_context = memory_manager.init_session(question)

            # Build prompt
            tools_desc = self.tool_executor.getAvailableTools()
            history_str = "\n".join(history)
            prompt = self._build_prompt(
                tools_desc, question, history_str,
                memory_context, conversation_context,
            )

            messages = [{"role": "user", "content": prompt}]
            response_text = self.llm_client.think(messages=messages)
            cur = getattr(self.llm_client, "last_usage", {})
            if not isinstance(cur, dict):
                cur = {}
            self._total_usage["input_tokens"] += cur.get("input_tokens", 0)
            self._total_usage["output_tokens"] += cur.get("output_tokens", 0)

            if not response_text:
                self._output("错误:LLM未能返回有效响应。")
                break

            thought, action, parse_reason = self._parse_output(response_text)

            if thought:
                self._output(f"思考: {thought}")

            if memory_manager:
                memory_manager.append("assistant", response_text)
                if self.conversation_mode:
                    conversation_context = self._build_conversation_context(memory_manager, per_msg_limit=800)

            if not action:
                reason_detail = f": {parse_reason}" if parse_reason else ""
                self._output(f"警告:未能解析出有效的Action{reason_detail}，流程终止。")
                break

            if 'type="finish"' in action:
                final_answer = self._parse_finish(action)
                if final_answer is None:
                    self._output(f"警告: Finish指令格式无法解析，原始Action为: {action}")
                    self._output("流程终止，请检查LLM输出格式。")
                    return None
                self._output(f"最终答案: {final_answer}")
                if memory_manager:
                    memory_manager.append("system", f"[最终答案] {final_answer}")
                    memory_manager.conclude(question, final_answer)
                return final_answer

            tool_name, tool_params = self._parse_action(action)
            if not tool_name or tool_params is None:
                continue

            if isinstance(tool_params, dict):
                display = f"{tool_name}({', '.join(f'{k}={v}' for k, v in tool_params.items())})"
            else:
                display = f"{tool_name}[{tool_params}]"
            self._output(f"行动: {display}")

            tool_function = self.tool_executor.getTool(tool_name)
            if not tool_function:
                observation = f"错误:未找到名为 '{tool_name}' 的工具。"
            else:
                params_dict = self._normalize_params(tool_name, tool_params)
                need_hitl = tool_name in self._HITL_TOOLS

                try:
                    observation = self.tool_executor.registry.invoke(
                        name=tool_name,
                        params=params_dict,
                        caller="react_agent",
                        hitl_approver=self._confirm if need_hitl else None,
                    )
                except Exception as e:
                    observation = f"错误: 工具 '{tool_name}' 执行失败: {e}"

            self._output(f"观察: {observation}")

            if thought:
                history.append(f"Thought: {thought}")
            history.append(f"Action: {action}")
            history.append(f"Observation: {observation}")
            if memory_manager:
                memory_manager.append("tool", f"Action: {action}\nObservation: {observation}")

        self._output("已达到最大步数，流程终止。")
        return None
