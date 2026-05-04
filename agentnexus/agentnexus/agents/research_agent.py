"""Research Agent — 检索 + 来源强制引用。

每条事实声明必须带 SourceClaim。无来源 → Critic 硬规则直接 FAIL。
"""

from __future__ import annotations

import json
import re
from typing import Optional

from agentnexus.agents.schema import ResearchOutput, SourceClaim
from agentnexus.core.llm import AgentLLM
from agentnexus.prompts import get_current_date, load_prompt
from agentnexus.rag.router import retrieve
from agentnexus.tools.web_search import web_search

RESEARCH_PROMPT = load_prompt("research")


class ResearchAgent:
    def __init__(self):
        self._llm = AgentLLM()
        self.last_output: Optional[ResearchOutput] = None
        self.last_error: str = ""

    def run(self, query: str) -> str:
        output = self.search(query)
        return output.summary if output.summary else "检索未产生有效结果"

    def search(self, query: str) -> ResearchOutput:
        """检索 → LLM 综合 → 结构化 ResearchOutput（带来源引用）。

        LLM 提示词强制要求每一条声明带 source 字段。
        解析失败时返回带 gaps 说明的弱输出。
        """
        kb_parts = []
        for r in retrieve(query, top_k=5):
            source = f"[{r.get('source', 'local')}]"
            if "file" in r:
                kb_parts.append(
                    f"{source} {r['file']}:{r.get('line', '')}\n{r['text']}"
                )
            else:
                kb_parts.append(f"{source} {r['text']}")

        kb = "\n\n".join(kb_parts) if kb_parts else "本地无相关知识。"

        try:
            web = web_search(query)
        except Exception:
            web = "网络搜索不可用"

        prompt = RESEARCH_PROMPT.format(
            kb=kb[:2000], web=web[:3000], query=query, date=get_current_date()
        )

        try:
            raw = (
                self._llm.think([{"role": "user", "content": prompt}], silent=True)
                or ""
            )
            parsed = self._parse_output(raw, query)
        except Exception as exc:
            parsed = ResearchOutput(
                summary=f"检索过程出错: {exc}",
                claims=[],
                gaps=str(exc),
            )
            self.last_error = str(exc)

        self.last_output = parsed
        return parsed

    def get_sources(self) -> list[SourceClaim]:
        """返回最近一次检索的 SourceClaim 列表。"""
        if self.last_output is None:
            return []
        return self.last_output.claims

    def has_sources(self) -> bool:
        """最近一次检索是否有来源引用。"""
        return len(self.get_sources()) > 0

    def _parse_output(self, raw: str, query: str) -> ResearchOutput:
        """从 LLM 原始输出中解析 ResearchOutput。"""
        # 尝试 JSON 代码块
        json_text = None
        match = re.search(r"```json\s*\n?(.*?)```", raw, re.DOTALL)
        if match:
            json_text = match.group(1).strip()

        if json_text is None:
            # 尝试整段文本是否为 JSON
            stripped = raw.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                json_text = stripped

        if json_text:
            try:
                data = json.loads(json_text)
                claims = [
                    SourceClaim(
                        claim=c.get("claim", ""),
                        source=c.get("source", "unknown"),
                        confidence=float(c.get("confidence", 0.0)),
                    )
                    for c in data.get("claims", [])
                ]
                return ResearchOutput(
                    summary=data.get("summary", raw[:500]),
                    claims=claims,
                    gaps=data.get("gaps", ""),
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        # 兜底: 自由文本 → 无来源声明
        return ResearchOutput(
            summary=raw[:1000] if raw else "LLM 无输出",
            claims=[],
            gaps="LLM 输出格式不符合 JSON Schema，无法提取结构化来源引用",
        )
