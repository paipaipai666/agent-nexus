from agentnexus.tools.tool_executor import ToolExecutor
from agentnexus.core.llm import AgentLLM
from agentnexus.core.config import get_settings
from agentnexus.prompts import load_prompt
import re


REACT_PROMPT_TEMPLATE = load_prompt("react")

class ReActAgent:
    def __init__(self, llm_client: AgentLLM, tool_executor: ToolExecutor, max_steps: int | None = None,
                 output=None):
        self.llm_client = llm_client
        self.tool_executor = tool_executor
        self.max_steps = max_steps if max_steps is not None else get_settings().max_agent_steps
        self.history = []
        self._output = output or print

    def _parse_output(self, text: str):
        """解析LLM的输出，提取Thought和Action。
        """
        # Thought: 匹配到 Action: 或文本末尾
        thought_match = re.search(r"Thought:\s*(.*?)(?=\nAction:|$)", text, re.DOTALL)
        # Action: 匹配到文本末尾
        action_match = re.search(r"Action:\s*(.*?)$", text, re.DOTALL)
        thought = thought_match.group(1).strip() if thought_match else None
        action = action_match.group(1).strip() if action_match else None
        return thought, action

    def _parse_finish(self, action_text: str):
        """解析Finish指令，兼容多种LLM输出格式。返回答案字符串，解析失败返回None。"""
        # 标准格式: Finish[答案]
        match = re.match(r"Finish\s*\[\s*(.*?)\s*\]", action_text, re.DOTALL)
        if match:
            return match.group(1).strip()
        # 冒号格式: Finish：答案 或 Finish: 答案
        match = re.match(r"Finish\s*[:：]\s*(.*)", action_text, re.DOTALL)
        if match:
            return match.group(1).strip()
        # 纯文本格式: Finish 答案 —— 移除前缀后返回剩余文本
        if action_text.strip() == "Finish":
            return ""
        remaining = re.sub(r"^Finish\s+", "", action_text, count=1)
        if remaining and remaining != action_text:
            return remaining.strip()
        return None

    def _parse_action(self, action_text: str):
        """解析Action字符串，提取工具名称和输入。
        """
        match = re.match(r"([\w-]+)\[(.*)\]", action_text, re.DOTALL)
        if match:
            return match.group(1), match.group(2)
        return None, None

    def _ask_confirm(self, code: str) -> bool:
        self._output(f"[警告] 即将执行代码 (预览): {code}")
        try:
            response = input("确认执行? [y/N] ").strip().lower()
            return response == "y"
        except (EOFError, OSError):
            return True

    def run(self, question: str, memory_manager=None):
        self.history = []
        current_step = 0

        memory_context = ""
        if memory_manager:
            memory_context = memory_manager.init_session(question)
            memory_manager.append("user", question)

        while current_step < self.max_steps:
            current_step += 1
            self._output(f"--- 第 {current_step} 步 ---")

            tools_desc = self.tool_executor.getAvailableTools()
            history_str = "\n".join(self.history)
            prompt = REACT_PROMPT_TEMPLATE.format(
                tools=tools_desc,
                question=question,
                history=history_str,
                memory_context=memory_context,
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

            if not action:
                self._output("警告:未能解析出有效的Action，流程终止。")
                break

            if action.startswith("Finish"):
                final_answer = self._parse_finish(action)
                if final_answer is None:
                    self._output(f"警告: Finish指令格式无法解析，原始Action为: {action}")
                    self._output("流程终止，请检查LLM输出格式。")
                    return None
                self._output(f"最终答案: {final_answer}")
                if memory_manager:
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
                    if not self._ask_confirm(tool_input):
                        observation = "用户取消了代码执行"
                    else:
                        observation = tool_function(tool_input)
                else:
                    observation = tool_function(tool_input)

                self._output(f"观察: {observation}")
            
            # 将本轮的Action和Observation添加到历史记录中
            self.history.append(f"Action: {action}")
            self.history.append(f"Observation: {observation}")

        # 循环结束
        self._output("已达到最大步数，流程终止。")
        return None
