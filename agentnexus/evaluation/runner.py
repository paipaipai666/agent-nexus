"""Evaluation runners — wrap agents as harness-callable trial executors."""

from __future__ import annotations

from typing import Any

from agentnexus.evaluation.task import EvalTask
from agentnexus.evaluation.trial import TrialResult


class ReActAgentRunner:
    """将 ReActAgent 包装为 EvalHarness 可调用的 AgentRunner。

    执行流程:
    1. 创建隔离的 agent 实例
    2. 执行 task.input.prompt
    3. 收集 transcript (从 trace 系统)
    4. 返回 TrialResult
    """

    def __init__(self, settings: Any | None = None):
        self._settings = settings

    def __call__(self, task: EvalTask, trial_index: int) -> TrialResult:
        """运行单次 trial。"""
        import time as _time

        start = _time.monotonic()
        transcript: list[dict[str, Any]] = []
        outcome: dict[str, Any] = {}
        metadata: dict[str, Any] = {}
        error: str | None = None

        try:
            from agentnexus.agents.re_act_agent import ReActAgent
            from agentnexus.core.config import get_settings
            from agentnexus.core.llm import AgentLLM
            from agentnexus.tools.registry import ToolRegistry

            settings = self._settings or get_settings()

            llm = AgentLLM()
            tool_registry = ToolRegistry()
            # 与 AppRuntime 对齐: 内置工具按 provider 注册, 否则 eval 下
            # agent 拿到空工具集, tool_use 类 task 被系统性低估。
            from agentnexus.tools import register_all_tools
            from agentnexus.tools.confirm_bridge import ConfirmBridge
            register_all_tools(
                tool_registry,
                non_interactive=True,
                llm_client=llm,
                subagent_confirm=ConfirmBridge(),
                mcp_manager=None,
                todo_list=None,
            )
            agent = ReActAgent(
                llm_client=llm,
                tool_executor=tool_registry,
                max_steps=task.max_turns or getattr(settings, "max_agent_steps", 5),
            )

            prompt = task.input.get("prompt", "")
            # 真实 trace 上下文: agent 循环只在 active trace 下埋点,
            # eval 若不建 trace, transcript 只能重建并丢失真实时间轴。
            trace_ctx = self._start_trace(task)
            try:
                result = agent.run(question=prompt)
            finally:
                self._end_trace(trace_ctx)

            transcript = self._collect_transcript(agent, result, trace_ctx)
            outcome = self._collect_outcome(result)
            metadata = {
                "runner": "react_agent",
                "model": getattr(settings, "llm_model_id", "unknown"),
                "trial_index": trial_index,
            }
            if trace_ctx is not None:
                metadata["trace_id"] = trace_ctx.trace_id

            usage = getattr(agent, "_total_usage", {})
            if isinstance(usage, dict):
                in_tok = usage.get("input_tokens", 0)
                out_tok = usage.get("output_tokens", 0)
                metadata["input_tokens"] = in_tok
                metadata["output_tokens"] = out_tok
                metadata["n_total_tokens"] = in_tok + out_tok

        except Exception as e:
            error = str(e)
            metadata = {"runner": "react_agent", "error": error, "trial_index": trial_index}

        elapsed = (_time.monotonic() - start) * 1000

        return TrialResult(
            task_id=task.id,
            trial_index=trial_index,
            transcript=transcript,
            outcome=outcome,
            metadata=metadata,
            duration_ms=elapsed,
            error=error,
        )

    # ── Trace lifecycle ────────────────────────────────────────────

    @staticmethod
    def _start_trace(task: EvalTask) -> Any | None:
        """Open a real trace context for the trial, unless one is already active."""
        try:
            from agentnexus.observability.tracer import trace_manager
            if trace_manager.active is not None:
                return None  # 上游已开 trace, 不劫持别人的上下文
            return trace_manager.start_trace(task.id, metadata={"eval_task": task.id})
        except Exception:
            return None

    @staticmethod
    def _end_trace(trace_ctx: Any | None) -> None:
        if trace_ctx is None:
            return
        try:
            from agentnexus.observability.tracer import trace_manager
            trace_manager.end_trace()
        except Exception:
            pass

    @staticmethod
    def _span_to_transcript(span: Any, trace_id: str) -> dict[str, Any]:
        """Serialize a TraceSpan to the grader transcript format (real clock)."""
        from agentnexus.observability.tracer import TraceManager

        record = TraceManager._span_record(trace_id, span)
        # Grader 协议: LLM 步骤约定名为 "llm", agent 循环的埋点名是 "plan_node"。
        if record["name"] == "plan_node":
            record["name"] = "llm"
        return record

    def _collect_transcript(
        self, agent: Any, result: Any, trace_ctx: Any | None = None,
    ) -> list[dict[str, Any]]:
        """收集 transcript spans — 优先真实 trace (真实时间轴), 退化时按 step 重建。"""
        if trace_ctx is not None:
            real = [self._span_to_transcript(s, trace_ctx.trace_id) for s in trace_ctx.spans]
            if real:
                return real
        return self._reconstruct_from_steps(result)

    def _reconstruct_from_steps(self, result: Any) -> list[dict[str, Any]]:
        """无 trace 可用时的兜底: 保留步骤顺序, 但不编造时间戳。"""
        spans: list[dict[str, Any]] = []

        steps = getattr(result, "steps", []) or []
        for i, step in enumerate(steps):
            content = getattr(step, "content", "") or ""
            reasoning = getattr(step, "reasoning_content", "") or ""
            error = getattr(step, "error_message", None)
            strategy = getattr(step, "strategy_used", None)
            strategy_name = strategy.name if strategy else "unknown"

            llm_span: dict[str, Any] = {
                "name": "llm",
                # 没有真实时钟来源 —— 省略时间键; graders 的 start_time 排序
                # 缺键得到 0, 稳定排序保持这里的插入序 (即真实 step 顺序)。
                "input": {"step_id": getattr(step, "step_id", i)},
                "output": {"content": content[:500]},
                "metadata": {
                    "status": "error" if error else "ok",
                    "strategy": strategy_name,
                    "reasoning": reasoning[:300],
                    "reconstructed": True,
                },
            }
            if error:
                llm_span["output"]["error"] = str(error)[:200]
            spans.append(llm_span)

            tool_calls = getattr(step, "tool_calls", []) or []
            tool_outputs = getattr(step, "tool_outputs", []) or []
            for j, tc in enumerate(tool_calls):
                tool_name = tc.get("name", "") or tc.get("function", {}).get("name", "")
                tool_params = tc.get("arguments", {}) or tc.get("function", {}).get("arguments", {})
                if isinstance(tool_params, str):
                    try:
                        import json as _json
                        tool_params = _json.loads(tool_params)
                    except Exception:
                        tool_params = {"raw": tool_params[:200]}

                output_data: dict[str, Any] = {}
                if j < len(tool_outputs):
                    out = tool_outputs[j]
                    output_data = {
                        "result_summary": str(out.get("output", ""))[:300],
                    }
                    if out.get("error"):
                        output_data["error"] = str(out["error"])[:200]

                tool_span: dict[str, Any] = {
                    "name": "tool",
                    "input": {
                        "tool_name": tool_name,
                        "params": tool_params,
                    },
                    "output": output_data,
                    "metadata": {
                        "status": "error" if (j < len(tool_outputs) and tool_outputs[j].get("error")) else "ok",
                        "reconstructed": True,
                    },
                }
                spans.append(tool_span)

        answer = getattr(result, "answer", None)
        if answer:
            spans.append({
                "name": "final_answer",
                "input": {},
                "output": {"answer": answer},
                "metadata": {"status": "ok", "reconstructed": True},
            })

        return spans

    def _collect_outcome(self, result: Any) -> dict[str, Any]:
        """从 agent 结果中收集 outcome。"""
        outcome: dict[str, Any] = {}
        if hasattr(result, "answer"):
            outcome["answer"] = result.answer
        if hasattr(result, "steps"):
            outcome["steps_count"] = len(result.steps) if result.steps else 0
        return outcome


class TranscriptCollector:
    """从现有的 TraceManager 收集 trial 的完整 transcript。"""

    def __init__(self):
        self._spans: list[dict[str, Any]] = []
        self._active = False
        self._trace_id: str | None = None

    def start(self, trace_id: str | None = None) -> None:
        """开始收集。"""
        self._spans = []
        self._active = True
        self._trace_id = trace_id

        try:
            from agentnexus.observability.tracer import TraceManager
            tm = TraceManager()
            if hasattr(tm, "on_span_end"):
                tm.on_span_end(self._on_span)
        except Exception:
            pass

    def stop(self) -> list[dict[str, Any]]:
        """停止收集并返回 spans。"""
        self._active = False
        return list(self._spans)

    def _on_span(self, span: dict[str, Any]) -> None:
        """span 结束回调。"""
        if not self._active:
            return
        if self._trace_id and span.get("trace_id") != self._trace_id:
            return
        self._spans.append(span)

    def get_tool_calls(self) -> list[dict[str, Any]]:
        """获取工具调用列表。"""
        return [s for s in self._spans if s.get("name") == "tool"]

    def get_outcome(self) -> dict[str, Any]:
        """获取最终状态。"""
        for s in reversed(self._spans):
            if s.get("name") == "final_answer":
                return s.get("output", {})
        return {}
