"""精细化评测执行器 — 范围装配 → 分数据集执行 → 采集 → 评分 → 报告。

执行器按数据集分发：
- perception_route:  技能路由器离线判定（确定性，无需 LLM）
- planning_tool_choice / tool_param_mapping / e2e_task: ReActAgent + mock 工具
- multi_turn: 共享 STM 的多轮链（文章 §6.4：逐轮执行、整体评判）
- abnormal_input: 异常输入行为分类
- memory_probes: 委托 MemoryEvaluator（离线探针套件）

下游跳过（文章 §5.4）：参数映射等指标在 agent 未调用期望工具时记 skip，
不计入分子分母——上游路由错误不污染下游指标。
"""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace
from typing import Any

from agentnexus.evaluation.fine_grained.cases import (
    JUDGE_CALIBRATION,
    EvalCase,
    cases_for_scope,
    datasets_for_scope,
)
from agentnexus.evaluation.fine_grained.mock_tools import build_mock_registry
from agentnexus.evaluation.fine_grained.report import (
    CaseOutcome,
    FineGrainedReport,
    Judges,
    RunMetrics,
)

logger = logging.getLogger(__name__)


def _usage_snapshot(agent: Any) -> tuple[int, int]:
    usage = getattr(agent.llm_client, "total_usage", None) or {}
    return (int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)))


def _collect_run_metrics(agent: Any, result: Any, wall_ms: float,
                         usage_before: tuple[int, int] = (0, 0)) -> RunMetrics:
    """从 ReActResult.steps + LLM usage 采集单用例 EvalTrace。

    total_usage 是生成器实例上的累计值（评测全程共享一个 generator），
    必须用运行前快照做差，否则每个用例报的是全程累计。
    """
    rm = RunMetrics(wall_ms=wall_ms)
    steps = getattr(result, "steps", None) or []
    rm.steps = len(steps)
    for s in steps:
        tcs = getattr(s, "tool_calls", None) or []
        tos = getattr(s, "tool_outputs", None) or []  # 与 tool_calls 按序对应
        for i, tc in enumerate(tcs):
            result_preview = str(tos[i].get("output", ""))[:300] if i < len(tos) else ""
            rm.tool_calls.append({
                "name": tc.get("name") or tc.get("tool") or "?",
                "params": tc.get("arguments") or tc.get("params") or {},
                "ok": not getattr(s, "error_message", None),
                "result_preview": result_preview,
            })
        rm.json_retries += getattr(s, "json_retries", 0) or 0
    usage = getattr(agent.llm_client, "total_usage", None) or {}
    rm.input_tokens = int(usage.get("input_tokens", 0)) - usage_before[0]
    rm.output_tokens = int(usage.get("output_tokens", 0)) - usage_before[1]
    rm.llm_calls = rm.steps  # 每个 ReAct 步至少一次 LLM 调用
    return rm


class FineGrainedRunner:
    """按评测范围运行精细化评测。"""

    def __init__(self, generator: Any = None, judge_llm: Any = None,
                 max_steps: int = 6, run_memory: bool = True):
        from agentnexus.core.llm import get_default_llm
        self.generator = generator or get_default_llm()
        self.judges = Judges(judge_llm) if judge_llm is not None else None
        self.max_steps = max_steps
        self.run_memory = run_memory

    # ── 主入口 ──

    def run(self, scope: str = "full") -> FineGrainedReport:
        start = time.monotonic()
        outcomes: list[CaseOutcome] = []
        memory_dimensions: dict[str, float] = {}
        memory_probe_rows: list[dict] = []

        for ds in datasets_for_scope(scope):
            if ds == "memory_probes":
                if self.run_memory:
                    memory_dimensions, memory_probe_rows = self._run_memory_probes()
                continue
            if ds == "judge_calibration":
                outcomes.extend(self._run_judge_calibration())
                continue
            for case in [c for c in cases_for_scope(scope) if c.dataset == ds]:
                try:
                    outcome = self._run_case(case)
                except Exception as e:
                    logger.warning("case %s errored: %s", case.id, e)
                    outcome = CaseOutcome(case_id=case.id, dataset=ds,
                                          verdict="error", reasoning=f"{type(e).__name__}: {e}")
                outcomes.append(outcome)

        return FineGrainedReport(
            scope=scope, outcomes=outcomes,
            memory_dimensions=memory_dimensions, memory_probe_rows=memory_probe_rows,
            duration_s=time.monotonic() - start,
        )

    def _run_case(self, case: EvalCase) -> CaseOutcome:
        if case.dataset == "perception_route":
            return self._run_perception(case)
        if case.dataset == "planning_real_registry":
            return self._run_real_registry_case(case)
        if case.dataset in ("planning_tool_choice", "tool_param_mapping", "e2e_task"):
            return self._run_agent_case(case)
        if case.dataset == "multi_turn":
            return self._run_multi_turn(case)
        if case.dataset == "abnormal_input":
            return self._run_abnormal(case)
        raise ValueError(f"no executor for dataset {case.dataset!r}")

    # ── 感知模块：技能路由（确定性，离线） ──

    def _run_perception(self, case: EvalCase) -> CaseOutcome:
        router, _entries = _build_eval_router()
        decision = router.decide(case.user_input, _entries)
        predicted = decision.route.entry.workflow_id if decision.route else None
        hit = 1.0 if predicted == case.expected_skill else 0.0
        # expected_mode：除命中外还要求路由模式正确（如 multi_intent）
        if case.expected_mode and hit:
            hit = 1.0 if decision.mode == case.expected_mode else 0.0
        out = CaseOutcome(
            case_id=case.id, dataset=case.dataset,
            verdict="pass" if hit else "fail",
            metric_values={"intent_accuracy": hit},
            reasoning=f"期望={case.expected_skill} 实际={predicted} "
                      f"(mode={decision.mode}, confidence={decision.confidence:.2f})",
        )
        return out

    # ── 规划 / 工具 / 端到端：真实 agent + mock 工具 ──

    def _make_agent(self, registry: Any) -> Any:
        from agentnexus.agents.re_act_agent import ReActAgent
        return ReActAgent(
            self.generator, registry,
            max_steps=self.max_steps,
            output=lambda *_: None,
            confirm_fn=lambda *a, **k: True,
            conversation_mode=True,
        )

    def _run_agent_case(self, case: EvalCase) -> CaseOutcome:
        registry = build_mock_registry()
        agent = self._make_agent(registry)
        before = _usage_snapshot(agent)
        t0 = time.monotonic()
        result = agent.run(case.user_input)
        wall_ms = (time.monotonic() - t0) * 1000
        rm = _collect_run_metrics(agent, result, wall_ms, usage_before=before)
        answer = result.answer or ""

        out = CaseOutcome(case_id=case.id, dataset=case.dataset, run=rm)
        called = rm.real_tool_names

        # 规划：工具决策（含"不该调用"负例）
        if case.dataset in ("planning_tool_choice", "e2e_task") and (
            case.expected_tool or case.expected_any_tools or case.expected_tool is None
            and case.dataset == "planning_tool_choice"
        ):
            expected = case.expected_tool
            if expected is None and case.dataset == "planning_tool_choice":
                correct = 1.0 if not called else 0.0
                out.metric_values["tool_decision_accuracy"] = correct
                out.reasoning = f"期望不调用工具，实际调用={called or '无'}"
            else:
                wanted = case.expected_any_tools or ([expected] if expected else [])
                correct = 1.0 if any(w in called for w in wanted) else 0.0
                out.metric_values["tool_decision_accuracy"] = correct
                out.reasoning = f"期望={wanted} 实际={called}"
                # 多实体：期望工具至少被调用 N 次
                if correct and case.expected_min_calls:
                    n = called.count(case.expected_tool)
                    out.metric_values["multi_call_correct"] = 1.0 if n >= case.expected_min_calls else 0.0
                    out.reasoning += f"；{case.expected_tool} 调用 {n} 次（期望≥{case.expected_min_calls}）"

        # 工具：参数映射（§5.4 下游跳过——期望工具未被调用时 skip）
        if case.dataset == "tool_param_mapping":
            if case.expected_tool not in called:
                out.verdict = "skip"
                out.reasoning = f"上游未调用 {case.expected_tool}（实际={called or '无'}），参数映射跳过"
                return out
            call = next(c for c in rm.tool_calls if c["name"] == case.expected_tool)

            def _param_ok(expected: Any, actual: Any) -> bool:
                # 期望值可为可接受值列表（语义归一的等价写法）
                accepted = expected if isinstance(expected, list) else [expected]
                return str(actual).strip() in [str(a).strip() for a in accepted]

            mismatched = {
                k: (v, call["params"].get(k))
                for k, v in case.expected_params.items()
                if not _param_ok(v, call["params"].get(k))
            }
            acc = 1.0 if not mismatched else 0.0
            out.metric_values["param_mapping_accuracy"] = acc
            out.metric_values["tool_call_success_rate"] = 1.0 if call["ok"] else 0.0
            out.verdict = "pass" if acc else "fail"
            out.reasoning = f"实收参数={call['params']}" + (f" 不匹配={mismatched}" if mismatched else "")
            return out

        # 确定性断言
        det_hits = []
        for kw in case.expected_contains:
            det_hits.append(1.0 if kw in answer else 0.0)
        for kw in case.must_not_contain:
            det_hits.append(1.0 if kw not in answer else 0.0)
        if det_hits:
            out.metric_values["keyword_hit_rate"] = sum(det_hits) / len(det_hits)

        # Judge 指标
        out.answer_preview = answer[:300]
        if self.judges is not None:
            tc = self.judges.task_completion(case.user_input, answer, case.reference_output)
            out.metric_values["task_completion"] = 1.0 if tc.get("verdict") == "pass" else 0.0
            out.reasoning = tc.get("reasoning", "")
            inf = self.judges.instruction_following(case.user_input, answer)
            out.metric_values["instruction_following"] = 1.0 if inf.get("verdict") == "pass" else 0.0
            if inf.get("verdict") != "pass":
                out.reasoning += f" [指令遵循] {inf.get('reasoning', '')}"
            # 忠实性：需要工具返回作参照系；无工具上下文则跳过（§5.4）
            tool_context = "\n".join(
                f"{c['name']}({c['params']}) => {c.get('result_preview', '')}" for c in rm.tool_calls
            ) if rm.tool_calls else ""
            if tool_context:
                fa = self.judges.faithfulness(case.user_input, answer, tool_context)
                out.metric_values["faithfulness"] = 1.0 if fa.get("verdict") == "pass" else 0.0
                if fa.get("verdict") != "pass":
                    out.reasoning += f" [忠实性] {fa.get('reasoning', '')}"

        if out.verdict == "error":
            primary_ok = out.metric_values.get("task_completion",
                                               out.metric_values.get("tool_decision_accuracy"))
            if primary_ok is not None:
                out.verdict = "pass" if primary_ok >= 1.0 else "fail"
        return out

    # ── 多轮对话（§6.4：共享会话、逐轮执行、整体评判） ──

    def _run_multi_turn(self, case: EvalCase) -> CaseOutcome:
        from agentnexus.memory.manager import MemoryManager
        from agentnexus.memory.short_term import ShortTermMemory

        registry = build_mock_registry()
        agent = self._make_agent(registry)
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.session_id = f"eval_{case.id}"
        mgr.short_term = ShortTermMemory()
        mgr.long_term = None
        mgr.project = None
        mgr._settings = SimpleNamespace(
            offload_enabled=False, time_microcompact_interval=0, transcript_enabled=False,
        )
        mgr.compact_threshold = 10**9  # 多轮评测不触发压缩

        turns_out: list[str] = []
        turn_keyword_rates: list[float] = []
        before = _usage_snapshot(agent)
        t0 = time.monotonic()
        for turn in case.chain:
            r = agent.run(turn["user_input"], memory_manager=mgr)
            answer = r.answer or ""
            turns_out.append(f"用户: {turn['user_input']}\n助手: {answer}")
            kws = turn.get("expected_contains", [])
            if kws:
                turn_keyword_rates.append(sum(1 for k in kws if k in answer) / len(kws))
        usage = agent.llm_client.total_usage or {}
        total_in = int(usage.get("input_tokens", 0)) - before[0]
        total_out = int(usage.get("output_tokens", 0)) - before[1]

        out = CaseOutcome(case_id=case.id, dataset=case.dataset)
        out.run = RunMetrics(
            wall_ms=(time.monotonic() - t0) * 1000,
            llm_calls=len(case.chain),  # 每轮至少一次 LLM 调用；成本逐轮累计（§6.4）
            input_tokens=total_in, output_tokens=total_out,
        )
        if turn_keyword_rates:
            # 短期记忆保留率的确定性近似：各轮期望关键词命中率
            out.metric_values["stm_retention_rate"] = (
                sum(turn_keyword_rates) / len(turn_keyword_rates)
            )
        # 纠正链：最终一次目标工具调用的参数必须等于纠正后的值
        if case.expected_final_tool_params:
            ok_all = True
            for tool_name, expected in case.expected_final_tool_params.items():
                calls = [c for c in registry.call_log if c["tool"] == tool_name]
                if not calls:
                    ok_all = False
                    out.reasoning += f"[纠正链] 未调用 {tool_name}；"
                    continue
                last = calls[-1]["params"]
                for k, v in expected.items():
                    if str(last.get(k, "")).strip() != str(v).strip():
                        ok_all = False
                        out.reasoning += (f"[纠正链] {tool_name} 末次参数 {k}={last.get(k)!r}，"
                                          f"期望 {v!r}；")
            out.metric_values["correction_followed"] = 1.0 if ok_all else 0.0
        if self.judges is not None:
            verdict = self.judges.multi_turn("\n\n".join(turns_out))
            out.metric_values["multi_turn_completion"] = 1.0 if verdict.get("verdict") == "pass" else 0.0
            out.reasoning = verdict.get("reasoning", "")
            out.verdict = "pass" if verdict.get("verdict") == "pass" else "fail"
        else:
            out.verdict = "skip"
            out.reasoning = "judge 未启用"
        return out

    # ── 异常输入 ──

    # ── 规划（真实注册表）：40 个真实工具描述 + 桩执行 ──

    def _run_real_registry_case(self, case: EvalCase) -> CaseOutcome:
        registry = self._build_stubbed_real_registry()
        agent = self._make_agent(registry)
        before = _usage_snapshot(agent)
        t0 = time.monotonic()
        result = agent.run(case.user_input)
        rm = _collect_run_metrics(agent, result, (time.monotonic() - t0) * 1000,
                                  usage_before=before)
        out = CaseOutcome(case_id=case.id, dataset=case.dataset, run=rm,
                          answer_preview=(result.answer or "")[:300])
        called = rm.real_tool_names
        wanted = case.expected_any_tools or ([case.expected_tool] if case.expected_tool else [])
        correct = 1.0 if any(w in called for w in wanted) else 0.0
        out.metric_values["tool_decision_accuracy"] = correct
        out.verdict = "pass" if correct else "fail"
        out.reasoning = f"期望={wanted} 实际={called}"
        return out

    def _build_stubbed_real_registry(self):
        """真实工具注册表（40+ 工具的真实描述/参数模式）+ 桩执行体。

        工具选择压力来自真实描述拓扑；执行体全部桩掉，无副作用、无网络。
        """
        from agentnexus.evaluation.fine_grained.mock_tools import MockToolRegistry
        from agentnexus.tools import register_all_tools

        registry = MockToolRegistry()
        register_all_tools(registry, llm_client=None, enable_subagent=False,
                           subagent_confirm=lambda *a, **k: True)

        def _stub(**params):
            return {"status": "ok", "echo_params": params}

        for name, entry in list(registry._tools.items()):
            registry._tools[name] = (entry[0], _stub)
        return registry

    def _run_abnormal(self, case: EvalCase) -> CaseOutcome:
        registry = build_mock_registry()
        agent = self._make_agent(registry)
        t0 = time.monotonic()
        out = CaseOutcome(case_id=case.id, dataset=case.dataset)
        try:
            before = _usage_snapshot(agent)
            result = agent.run(case.user_input)
            answer = result.answer or ""
            out.run = _collect_run_metrics(agent, result, (time.monotonic() - t0) * 1000,
                                           usage_before=before)
        except Exception as e:
            out.run = RunMetrics(wall_ms=(time.monotonic() - t0) * 1000)
            out.verdict = "fail"
            out.metric_values["abnormal_handling"] = 0.0
            out.metric_values["crash_free_rate"] = 0.0
            out.reasoning = f"agent 抛出异常: {type(e).__name__}: {e}"
            return out

        out.metric_values["crash_free_rate"] = 1.0
        out.answer_preview = answer[:300]
        if self.judges is not None:
            r = self.judges.abnormal(case.user_input[:500], answer)
            behavior = r.get("behavior", "")
            ok = behavior in (case.expected_behaviors or ["graceful"])
            out.metric_values["abnormal_handling"] = 1.0 if ok else 0.0
            out.reasoning = f"行为分类={behavior}；{r.get('reasoning', '')}"
            out.verdict = "pass" if ok else "fail"
        else:
            out.verdict = "skip"
            out.reasoning = "judge 未启用"
        return out

    # ── Judge 校准：已知判定输入直接喂裁判，量化裁判可靠率 ──

    def _run_judge_calibration(self) -> list[CaseOutcome]:
        if self.judges is None:
            return [CaseOutcome(case_id="CAL-000", dataset="judge_calibration",
                                verdict="skip", reasoning="judge 未启用")]
        outcomes = []
        for i, entry in enumerate(JUDGE_CALIBRATION, 1):
            judge_fn = getattr(self.judges, entry["judge"])
            result = judge_fn(**entry["kwargs"])
            actual = result.get("verdict") or result.get("behavior", "")
            agree = 1.0 if actual == entry["expected"] else 0.0
            outcomes.append(CaseOutcome(
                case_id=f"CAL-{i:03d}", dataset="judge_calibration",
                verdict="pass" if agree else "fail",
                metric_values={"judge_agreement_rate": agree},
                reasoning=(f"judge={entry['judge']} 期望={entry['expected']} 实际={actual}；"
                           f"{result.get('reasoning', '')[:80]}"),
            ))
        return outcomes

    # ── 记忆模块：委托离线探针套件 ──

    def _run_memory_probes(self) -> tuple[dict[str, float], list[dict]]:
        from agentnexus.evaluation.memory_eval import DIMENSIONS, MemoryEvaluator
        report = MemoryEvaluator(enable_judge=self.judges is not None).run()
        dimensions = {d: report.dimension_rate(d) for d in DIMENSIONS
                      if any(r.dimension == d for r in report.results)}
        rows = [
            {"name": r.name, "layer": r.layer, "dimension": r.dimension,
             "passed": r.passed, "detail": r.detail}
            for r in report.results
        ]
        return dimensions, rows


# ── 感知评测的虚拟技能集 ─────────────────────────────────────────────


def _make_skill(workflow_id: str, name: str, desc: str,
                verbs: tuple[str, ...], objects: tuple[str, ...],
                examples: tuple[str, ...], negative_hints: tuple[str, ...] = ()):
    from pathlib import Path

    from agentnexus.skills.registry import SkillEntry
    from agentnexus.skills.workflow import Workflow

    wf = Workflow.model_validate({
        "id": workflow_id, "version": "1", "display_name": name,
        "description": desc,
        "prompt_profile": {"system": "react"},
        "tool_policy": {"max_risk": "low"},
        "steps": [{"type": "prompt", "id": "s1", "prompt": "Do."}],
        "success_criteria": ["Done."],
    })
    return SkillEntry(
        namespace="eval", workflow_id=workflow_id, display_name=name,
        description=desc, path=Path("/tmp/eval"), workflow=wf,
        verbs=verbs, objects=objects, examples=examples,
        negative_hints=negative_hints,
    )


def _build_eval_router():
    """3 个虚拟技能的路由器（纯关键词模式，确定性）。"""
    from agentnexus.skills.router.decide import SkillRouter

    entries = [
        _make_skill("weather_query", "天气查询", "查询城市天气、温度、出行建议",
                    ("查询", "查", "看看"), ("天气", "温度", "气温"),
                    ("北京今天天气怎么样", "上海明天会下雨吗")),
        # negative_hints：领域词出现在非领域意图（翻译/释义问句）时压分
        _make_skill("stock_analysis", "股票分析", "分析股票走势、行情和投资价值",
                    ("分析", "看看", "查"), ("股票", "股价", "行情"),
                    ("分析一下贵州茅台", "平安银行股价走势"),
                    negative_hints=("怎么说", "翻译", "英语", "什么意思")),
        _make_skill("code_review", "代码审查", "审查已有代码的质量、安全性和并发问题",
                    ("审查", "review", "检查"), ("代码", "函数", "bug"),
                    ("帮我审查这段代码", "review 这个函数")),
        # 与 code_review 词汇重叠的写作技能——测试路由器能否区分"写"和"审"
        _make_skill("code_generation", "代码生成", "根据需求编写新的代码实现",
                    ("写", "实现", "生成", "编写"), ("代码", "函数", "脚本", "程序"),
                    ("帮我写一段快速排序", "实现一个登录接口")),
    ]
    router = SkillRouter(min_score=2.0, use_embeddings=False)
    return router, entries
