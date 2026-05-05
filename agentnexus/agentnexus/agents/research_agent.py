"""Research Agent — 检索 + 来源强制引用 + 自我评估循环。

每条事实声明必须带 SourceClaim。无来源 → Critic 硬规则直接 FAIL。
内部自评: 检索不足时自动换关键词补充搜索，最多 3 轮迭代。
"""

from __future__ import annotations

import json
import re
from typing import Optional

from agentnexus.agents.schema import ResearchOutput, SourceClaim
from agentnexus.core.llm import get_default_llm
from agentnexus.prompts import get_current_date, load_prompt
from agentnexus.rag.router import retrieve
from agentnexus.tools.web_search import web_search_structured

RESEARCH_PROMPT = load_prompt("research")
SUFFICIENCY_PROMPT = load_prompt("research_sufficiency")
MAX_RESEARCH_ITERATIONS = 3


class ResearchAgent:
    def __init__(self):
        self._llm = get_default_llm()
        self.last_output: Optional[ResearchOutput] = None
        self.last_error: str = ""

    def run(self, query: str) -> str:
        output = self.search(query)
        return output.summary if output.summary else "检索未产生有效结果"

    def search(self, query: str) -> ResearchOutput:
        from agentnexus.tools.web_search import _seen_urls
        _seen_urls.clear()

        all_results: list[dict] = []
        queries_used: list[str] = []

        for iteration in range(MAX_RESEARCH_ITERATIONS):
            queries_used.append(query)
            results = self._retrieve(query)
            all_results.extend(results)

            if iteration == MAX_RESEARCH_ITERATIONS - 1:
                break

            assessment = self._assess_sufficiency(query, all_results, iteration + 1)
            if assessment.get("is_sufficient", True):
                break
            followup = assessment.get("next_query", "").strip()
            if followup:
                query = followup

        return self._synthesize(query, all_results, queries_used)

    def _retrieve(self, query: str) -> list[dict]:
        kb_parts: list[dict] = []
        for r in retrieve(query, top_k=5):
            source = r.get("source", "local")
            if "file" in r:
                kb_parts.append({
                    "text": f"[{source}] {r['file']}:{r.get('line', '')}\n{r['text']}",
                    "source": source,
                })
            else:
                kb_parts.append({
                    "text": f"[{source}] {r['text']}",
                    "source": source,
                })

        try:
            web_results = web_search_structured(query)
            for wr in web_results:
                url = wr.get("url", "")
                date_str = f" ({wr['published_date']})" if wr.get("published_date") else ""
                kb_parts.append({
                    "text": f"[web] {wr['title']}{date_str}\n来源: {url}\n{wr['content']}",
                    "source": "web",
                })
        except Exception:
            pass

        return kb_parts

    def _assess_sufficiency(self, task: str, results: list[dict], iterations: int) -> dict:
        results_summary = self._summarize_results(results)

        try:
            prompt = SUFFICIENCY_PROMPT.format(
                task=task,
                iterations=iterations,
                results_summary=results_summary,
                date=get_current_date(),
            )
            raw = self._llm.think([{"role": "user", "content": prompt}], silent=True) or "{}"

            json_text = None
            match = re.search(r"```json\s*\n?(.*?)```", raw, re.DOTALL)
            if match:
                json_text = match.group(1).strip()
            elif raw.strip().startswith("{"):
                json_text = raw.strip()

            if json_text:
                return json.loads(json_text)
        except Exception:
            pass

        # Deterministic fallback: when LLM can't assess, default to insufficient
        # (conservative — better to search again than to skip needed research)
        return {"is_sufficient": False, "gap": "无法评估检索充分性（LLM 不可用），建议补充搜索", "next_query": task}

    def _synthesize(self, task: str, results: list[dict], queries: list[str]) -> ResearchOutput:
        if not results:
            return ResearchOutput(
                summary="本地无相关知识，网络搜索也不可用。",
                claims=[],
                gaps="无任何检索结果",
            )

        kb = "\n\n".join(r["text"] for r in results)
        web = "\n\n".join(
            r["text"] for r in results if r.get("source") == "web"
        )
        if not web:
            web = "网络搜索不可用"

        prompt = RESEARCH_PROMPT.format(
            kb=kb,
            web=web,
            query=f"<user_query>{task}</user_query>",
            date=get_current_date(),
        )

        try:
            raw = (
                self._llm.think([{"role": "user", "content": prompt}], silent=True)
                or ""
            )
            parsed = self._parse_output(raw, task)
        except Exception as exc:
            parsed = ResearchOutput(
                summary=f"检索过程出错: {exc}",
                claims=[],
                gaps=str(exc),
            )
            self.last_error = str(exc)

        if queries and len(queries) > 1:
            query_log = " → ".join(queries)
            parsed = ResearchOutput(
                summary=f"{parsed.summary}\n\n（检索路径: {query_log}）",
                claims=parsed.claims,
                gaps=parsed.gaps,
            )

        self.last_output = parsed
        return parsed

    @staticmethod
    def _summarize_results(results: list[dict]) -> str:
        texts = [r["text"] for r in results]
        return "\n".join(texts)

    def get_sources(self) -> list[SourceClaim]:
        if self.last_output is None:
            return []
        return self.last_output.claims

    def has_sources(self) -> bool:
        return len(self.get_sources()) > 0

    def _parse_output(self, raw: str, query: str) -> ResearchOutput:
        json_text = None
        match = re.search(r"```json\s*\n?(.*?)```", raw, re.DOTALL)
        if match:
            json_text = match.group(1).strip()
        if json_text is None and raw.strip().startswith("{") and raw.strip().endswith("}"):
            json_text = raw.strip()

        if json_text:
            try:
                data = json.loads(json_text)
                claims = [
                    SourceClaim(
                        claim=c.get("claim", ""),
                        source=c.get("source", "unknown"),
                        url=c.get("url", ""),
                        confidence=float(c.get("confidence", 0.0)),
                    )
                    for c in data.get("claims", [])
                ]
                return ResearchOutput(
                    summary=data.get("summary", raw),
                    claims=claims,
                    gaps=data.get("gaps", ""),
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        return ResearchOutput(
            summary=raw if raw else "LLM 无输出",
            claims=[],
            gaps="LLM 输出格式不符合 JSON Schema，无法提取结构化来源引用",
        )
