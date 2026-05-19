"""Coder Agent — 代码生成 + Schema 校验 + AST 完整性检查 + 执行。"""
import ast
import json
import re
from typing import Optional

from agentnexus.agents.schema import CodeOutput, ErrorType
from agentnexus.core.llm import AgentLLM
from agentnexus.prompts import load_prompt
from agentnexus.tools.code_executor import python_execute

CODER_PROMPT = load_prompt("coder")


class CoderAgent:
    def __init__(self):
        self._llm = AgentLLM()
        self.last_output: Optional[CodeOutput] = None
        self.last_error: str = ""

    def generate(self, spec: str) -> CodeOutput:
        """LLM 生成代码 → 返回结构化 CodeOutput。

        如果 LLM 输出无法解析为有效 JSON，返回 reasoning 中有错误说明、
        code 为空的 CodeOutput（由上层 detect_error_type 识别为
        MISSING_CODE 或 SCHEMA_VIOLATION）。
        """
        try:
            prompt = CODER_PROMPT.format(spec=spec)
            raw = self._llm.think([{"role": "user", "content": prompt}], silent=True) or ""

            parsed = self._parse_output(raw, spec)
            self.last_output = parsed
            return parsed

        except Exception as exc:
            self.last_output = CodeOutput(
                reasoning=f"代码生成异常: {exc}",
                code="",
            )
            self.last_error = str(exc)
            return self.last_output

    def run(self, spec: str) -> str:
        """运行完整流程：生成 → 校验 → 执行。

        返回执行结果字符串或错误信息。
        """
        output = self.generate(spec)

        if not output.code or not output.code.strip():
            self.last_error = "LLM 未生成有效代码"
            return f"[Coder Error] {self.last_error}"

        try:
            result = python_execute(output.code)
            return str(result)
        except Exception as exc:
            self.last_error = str(exc)
            return f"代码执行出错: {exc}"

    def detect_error_type(self) -> Optional[ErrorType]:
        """根据 last_output 和 last_error 自动判断错误类型。"""
        if self.last_output is None:
            return ErrorType.MISSING_CODE

        if not self.last_output.code or not self.last_output.code.strip():
            return ErrorType.MISSING_CODE

        if self.last_error:
            if "SyntaxError" in self.last_error:
                return ErrorType.RUNTIME_ERROR
            if "Schema" in self.last_error or "JSON" in self.last_error:
                return ErrorType.SCHEMA_VIOLATION
            return ErrorType.RUNTIME_ERROR

        return None

    def _parse_output(self, raw: str, spec: str) -> CodeOutput:
        json_text = self._extract_json_block(raw)
        if json_text:
            try:
                data = json.loads(json_text)
                code = data.get("code", "")
                reasoning = data.get("reasoning", "")
                return CodeOutput(
                    reasoning=reasoning,
                    code=code,
                )
            except (json.JSONDecodeError, TypeError):
                pass

        try:
            data = json.loads(raw.strip())
            code = data.get("code", "")
            return CodeOutput(
                reasoning=data.get("reasoning", ""),
                code=code,
            )
        except (json.JSONDecodeError, TypeError):
            pass

        code = self._extract_code_block(raw)
        if code:
            truncated = self._check_truncation(code)
            return CodeOutput(
                reasoning=f"从非结构化输出中提取代码 (任务: {spec}){truncated}",
                code=code,
            )

        return CodeOutput(
            reasoning=raw if raw else "LLM 无输出",
            code="",
        )

    @staticmethod
    def _check_truncation(code: str) -> str:
        try:
            ast.parse(code)
        except SyntaxError as e:
            if "unexpected EOF" in str(e) or "EOF" in str(e):
                return f" | 警告: 代码可能截断 — 语法错误: {e}"
            return f" | 语法错误: {e}"
        return ""

    @staticmethod
    def _extract_json_block(text: str) -> Optional[str]:
        """提取 ```json ... ``` 代码块中的内容。"""
        match = re.search(r"```json\s*\n?(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

    @staticmethod
    def _extract_code_block(text: str) -> Optional[str]:
        """提取 ```python ... ``` 或 ``` ... ``` 代码块中的内容。"""
        match = re.search(r"```(?:python|py)?\s*\n?(.*?)```", text, re.DOTALL)
        if match:
            code = match.group(1).strip()
            # 过滤掉明显不是代码的 JSON / yaml
            if code.startswith("{") or code.startswith("---"):
                return None
            return code
        return None
