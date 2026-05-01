from agentnexus.tools.tool_executor import ToolExecutor
from agentnexus.core.llm import AgentLLM
from agentnexus.core.config import get_settings
import re

REACT_PROMPT_TEMPLATE = """
你是一个能够调用外部工具的智能助手。

== 可用工具 ==
{tools}

== 输出格式（严格遵守）==
每次回复必须且只能包含 Thought 和 Action 两个字段，格式如下：

Thought: <你的推理过程，可以多行>
Action: <必须从以下两种格式中二选一>

Action 可选格式：
1. 调用工具：tool_name[工具参数]
2. 结束任务：Finish[最终答案]

【重要规则】
- Action: 后必须直接跟 Finish[你的答案] 或 tool_name[参数]，不得有任何其他文字。
- 当你收集到足够信息能回答用户时，必须使用 Finish[...] 结束。
- 答案中不要出现方括号，避免破坏格式。
- 不要在 Action 行添加额外说明、解释或标点。

== 当前任务 ==
Question: {question}

== 历史记录 ==
{history}
"""

class ReActAgent:
    def __init__(self, llm_client: AgentLLM, tool_executor: ToolExecutor, max_steps: int | None = None):
        self.llm_client = llm_client
        self.tool_executor = tool_executor
        self.max_steps = max_steps if max_steps is not None else get_settings().max_agent_steps
        self.history = []

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
        match = re.match(r"(\w+)\[(.*)\]", action_text, re.DOTALL)
        if match:
            return match.group(1), match.group(2)
        return None, None

    def run(self, question: str, memory_manager=None):
        self.history = []
        current_step = 0

        memory_context = ""
        if memory_manager:
            memory_context = memory_manager.init_session(question)
            memory_manager.append("user", question)

        while current_step < self.max_steps:
            current_step += 1
            print(f"--- 第 {current_step} 步 ---")

            tools_desc = self.tool_executor.getAvailableTools()
            history_str = "\n".join(self.history)
            prompt = REACT_PROMPT_TEMPLATE.format(
                tools=tools_desc,
                question=question,
                history=history_str,
            )
            if memory_context:
                prompt = prompt.replace("== 当前任务 ==", f"{memory_context}\n== 当前任务 ==")

            messages = [{"role": "user", "content": prompt}]
            response_text = self.llm_client.think(messages=messages)

            if not response_text:
                print("错误:LLM未能返回有效响应。")
                break

            thought, action = self._parse_output(response_text)

            if thought:
                print(f"思考: {thought}")
            if memory_manager:
                memory_manager.append("assistant", response_text)

            if not action:
                print("警告:未能解析出有效的Action，流程终止。")
                break

            if action.startswith("Finish"):
                final_answer = self._parse_finish(action)
                if final_answer is None:
                    print(f"警告: Finish指令格式无法解析，原始Action为: {action}")
                    print("流程终止，请检查LLM输出格式。")
                    return None
                print(f"最终答案: {final_answer}")
                if memory_manager:
                    memory_manager.conclude(question, final_answer)
                return final_answer
            
            tool_name, tool_input = self._parse_action(action)
            if not tool_name or not tool_input:
                # ... 处理无效Action格式 ...
                continue

            print(f"行动: {tool_name}[{tool_input}]")
            
            tool_function = self.tool_executor.getTool(tool_name)
            if not tool_function:
                observation = f"错误:未找到名为 '{tool_name}' 的工具。"
            else:
                observation = tool_function(tool_input) # 调用真实工具

                print(f"观察: {observation}")
            
            # 将本轮的Action和Observation添加到历史记录中
            self.history.append(f"Action: {action}")
            self.history.append(f"Observation: {observation}")

        # 循环结束
        print("已达到最大步数，流程终止。")
        return None
