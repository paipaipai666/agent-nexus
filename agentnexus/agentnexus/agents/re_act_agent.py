import re

from agentnexus.core.config import get_settings
from agentnexus.core.llm import AgentLLM
from agentnexus.prompts import load_prompt
from agentnexus.tools.tool_executor import ToolExecutor

REACT_PROMPT_TEMPLATE = load_prompt("react")

class ReActAgent:
    def __init__(self, llm_client: AgentLLM, tool_executor: ToolExecutor, max_steps: int | None = None,
                 output=None, confirm_fn=None, conversation_mode: bool = False):
        self.llm_client = llm_client
        self.tool_executor = tool_executor
        self.max_steps = max_steps if max_steps is not None else get_settings().max_agent_steps
        self._output = output or print
        self._confirm = confirm_fn or self._default_confirm
        self.conversation_mode = conversation_mode

    def _parse_output(self, text: str):
        """解析LLM的输出，提取Thought和Action（XML格式）。
        """
        thought_match = re.search(r"<thought>\s*(.*?)\s*</thought>", text, re.DOTALL)
        action_match = re.search(r"(<action\s+[^>]*>.*?</action>)", text, re.DOTALL)
        thought = thought_match.group(1).strip() if thought_match else None
        action = action_match.group(1).strip() if action_match else None
        return thought, action

    def _parse_finish(self, action_text: str):
        """解析Finish指令（XML格式）。返回答案字符串，解析失败返回None。"""
        match = re.search(r'<action\s+type="finish">\s*(.*?)\s*</action>', action_text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

    def _parse_action(self, action_text: str):
        """解析Action字符串（XML格式），提取工具名称和输入。
        """
        match = re.search(r'<action\s+type="tool"\s+name="([^"]*)">\s*(.*?)\s*</action>', action_text, re.DOTALL)
        if match:
            return match.group(1), match.group(2)
        return None, None

    def _default_confirm(self, code: str) -> bool:
        self._output(f"[警告] 即将执行代码 (预览): {code}")
        try:
            response = input("确认执行? [y/N] ").strip().lower()
            return response == "y"
        except (EOFError, OSError):
            return True

    def _build_conversation_context(self, memory_manager) -> str:
        """Build conversation context from STM, prioritizing compressed summary."""
        if not memory_manager or not memory_manager.short_term:
            return ""
        stm = memory_manager.short_term
        summary = stm.get_summary()
        messages = stm.get_all()
        user_assistant_msgs = [m for m in messages if m["role"] in ("user", "assistant")]

        if summary:
            # Compressed summary available: show it prominently, plus minimal recent messages
            recent = user_assistant_msgs[-3:] if len(user_assistant_msgs) > 3 else user_assistant_msgs
            parts = ["== 对话历史摘要 ==", summary]
            if recent:
                parts.append("\n== 最近对话 ==")
                for m in recent:
                    role_label = "用户" if m["role"] == "user" else "助手"
                    content = m["content"][:500]
                    parts.append(f"{role_label}: {content}")
            return "\n".join(parts) + "\n\n"

        # No summary: use recent messages directly
        if not user_assistant_msgs:
            return ""
        recent = user_assistant_msgs[-6:]
        lines = []
        for m in recent:
            role_label = "用户" if m["role"] == "user" else "助手"
            content = m["content"][:500]
            lines.append(f"{role_label}: {content}")
        return "== 近期对话 ==\n" + "\n".join(lines) + "\n\n"

    def run(self, question: str, memory_manager=None):
        history = []
        current_step = 0

        memory_context = ""
        conversation_context = ""
        if memory_manager:
            memory_context = memory_manager.init_session(question)
            memory_manager.append("user", question)
            if self.conversation_mode:
                conversation_context = self._build_conversation_context(memory_manager)

        while current_step < self.max_steps:
            current_step += 1
            self._output(f"--- 第 {current_step} 步 ---")

            tools_desc = self.tool_executor.getAvailableTools()
            history_str = "\n".join(history)
            prompt = REACT_PROMPT_TEMPLATE.format(
                tools=tools_desc,
                question=question,
                history=history_str,
                memory_context=memory_context,
                conversation_context=conversation_context,
            )

            messages = [{"role": "user", "content": prompt}]
            response_text = self.llm_client.think(messages=messages)

            if not response_text:
                self._output("错误:LLM未能返回有效响应。")
                break

            thought, action = self._parse_output(response_text)

            if thought:
                self._output(f"思考: {thought}")
            if memory_manager:
                memory_manager.append("assistant", response_text)
                # Refresh conversation context after potential compaction (triggered by append)
                if self.conversation_mode:
                    conversation_context = self._build_conversation_context(memory_manager)

            if not action:
                self._output("警告:未能解析出有效的Action，流程终止。")
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

            tool_name, tool_input = self._parse_action(action)
            if not tool_name or not tool_input:
                # ... 处理无效Action格式 ...
                continue

            self._output(f"行动: {tool_name}[{tool_input}]")

            tool_function = self.tool_executor.getTool(tool_name)
            if not tool_function:
                observation = f"错误:未找到名为 '{tool_name}' 的工具。"
            else:
                if tool_name in ("python_execute", "code_executor"):
                    if not self._confirm(tool_input):
                        observation = "用户取消了代码执行"
                    else:
                        observation = tool_function(tool_input)
                else:
                    observation = tool_function(tool_input)

                self._output(f"观察: {observation}")

            # Append to ReAct loop history and STM
            history.append(f"Action: {action}")
            history.append(f"Observation: {observation}")
            if memory_manager:
                memory_manager.append("tool", f"Action: {action}\nObservation: {observation}")

        # 循环结束
        self._output("已达到最大步数，流程终止。")
        return None
