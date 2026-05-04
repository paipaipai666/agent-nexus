"""AgentNexus Orchestrator — 新 FSM 流程

硬约束流水线:
  START → plan → [research ∥ code] → execute → analyze → critique → (retry | END)

与旧版的关键区别:
- code_node 内嵌 Schema 校验（CodeOutput 不通过 → 直接标记错误）
- execute_node 独立执行 + 验证（ExecutorAgent）
- critique_node 硬规则先行（HardRuleChecker）→ LLM 只打质量分
- should_retry 使用 RetryManager 分类策略（不再盲目 +1）
- retry_node 携带 error_type 上下文化指令
"""

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from rich.console import Console
from rich.markup import escape as _e

from agentnexus.agents.coder_agent import CoderAgent
from agentnexus.agents.critic_agent import CriticAgent
from agentnexus.agents.critic_rules import HardRuleChecker
from agentnexus.agents.executor_agent import ExecutorAgent
from agentnexus.agents.multi_agent.state import AgentState
from agentnexus.agents.research_agent import ResearchAgent
from agentnexus.agents.retry_manager import RetryManager
from agentnexus.agents.schema import (
    CriticVerdict,
    ErrorType,
    ExecutionResult,
    ResearchOutput,
    SourceClaim,
)
from agentnexus.core.llm import AgentLLM
from agentnexus.observability.tracer import trace_manager
from agentnexus.prompts import get_current_date, load_prompt

console = Console()

PLANNER_PROMPT = load_prompt("planner")
ANALYST_PROMPT = load_prompt("analyst")

MAX_RETRIES = 3
PASS_THRESHOLD = 7.0


def _trunc(text: str, max_len: int = 500) -> str:
    if not isinstance(text, str):
        text = str(text)
    return text[:max_len] + "..." if len(text) > max_len else text


def _state_preview(state: dict, keys: list[str]) -> dict[str, str]:
    return {k: _trunc(str(state.get(k, ""))) for k in keys}


def _trace_wrapper(fn, node_name: str, input_keys: list[str]):
    def wrapped(state: AgentState) -> dict:
        ctx = trace_manager.active
        span = None
        if ctx:
            span = ctx.start_span(node_name, input_data=_state_preview(state, input_keys))
        try:
            result = fn(state)
            if span and ctx:
                ctx.end_span(
                    span,
                    output_data={k: _trunc(str(v)) for k, v in result.items()},
                    metadata={"status": "ok"},
                )
            return result
        except Exception as e:
            if span and ctx:
                ctx.end_span(span, metadata={"status": "error", "error": str(e)[:200]})
            raise

    return wrapped


def _compare_output(expected: str, actual: str) -> dict:
    """Smart output comparison — returns specific mismatch reasons."""
    reasons: list[str] = []
    expected_lower = expected.lower()
    actual_lower = actual.lower()
    actual_lines = actual.strip().split("\n")

    has_data = any(ch.isdigit() for ch in actual)
    has_chart = any(kw in actual_lower for kw in ("saved", ".png", "figure", "chart", "柱状图", "图表"))
    has_company_data = any(kw in actual_lower for kw in ("公司", "company", "ai", "科技", "智能"))
    line_count = len([line for line in actual_lines if line.strip()])

    expected_has_data = any(kw in expected_lower for kw in ("数据", "data", "数字", "表格", "表", "print", "输出"))
    expected_has_chart = any(kw in expected_lower for kw in ("图", "chart", "plot", "柱状", "bar", "fig"))
    expected_has_company = any(kw in expected_lower for kw in ("公司", "company", "ai", "企业"))

    if expected_has_data and not has_data:
        reasons.append("预期应输出数值数据，但实际输出中未检测到数字")
    if expected_has_chart and not has_chart:
        reasons.append("预期应生成图表，但实际输出中未检测到图表保存记录")
    if expected_has_company and not has_company_data:
        reasons.append("预期应包含公司数据，但实际输出中未检测到公司名称")
    if line_count < 1:
        reasons.append("实际输出为空")

    matched = len(reasons) == 0

    detail = ""
    if not matched:
        detail = "; ".join(reasons)
        detail += f"\n预期要点: {expected[:200]}\n实际输出(首200字符): {actual[:200]}"

    return {
        "matched": matched,
        "reasons": reasons,
        "expected": expected[:200],
        "actual": actual[:200],
        "detail": detail,
    }


def _build_exec_report(exec_success: bool, exec_stdout: str, exec_stderr: str, exec_exception: str) -> str:
    """Deterministic execution report — LLM never touches this."""
    if exec_success:
        lines = [
            "## 代码执行报告",
            "",
            "**状态**: ✅ 执行成功",
            "",
        ]
        if exec_stdout:
            lines.append("**输出**:")
            lines.append("```")
            lines.append(exec_stdout[:1500])
            lines.append("```")
        return "\n".join(lines)

    lines = ["## 代码执行报告", "", "**状态**: ❌ 执行失败", ""]
    if exec_exception and "NO_OUTPUT" in exec_exception:
        lines.append("**原因**: 代码成功解析和运行，但未产生任何输出")
        lines.append("")
        lines.append("常见原因:")
        lines.append("- 函数已定义但未被调用（缺少 `if __name__ == '__main__':` 或顶层调用）")
        lines.append("- `print()` 语句在条件分支内且条件未满足")
        lines.append("- 代码仅包含 `import` 和 `def`，缺少执行入口")
    elif exec_exception:
        lines.append(f"**错误**: {exec_exception[:300]}")
    elif exec_stderr:
        lines.append(f"**标准错误**: {exec_stderr[:300]}")
    else:
        lines.append("**错误**: 代码未生成任何输出（可能因语法错误导致提前退出）")
    if exec_stdout:
        lines.append("")
        lines.append("**部分输出**（错误前已打印的内容）:")
        lines.append("```")
        lines.append(exec_stdout[:500])
        lines.append("```")
    return "\n".join(lines)


import threading

_research = ResearchAgent()
_coder = CoderAgent()
_executor_agent = ExecutorAgent()
_critic = CriticAgent()
_hard_checker = HardRuleChecker()
_retry_mgr = RetryManager()

_instance_local = threading.local()


def _get_planner_llm() -> AgentLLM:
    if not hasattr(_instance_local, "planner_llm"):
        _instance_local.planner_llm = AgentLLM()
    return _instance_local.planner_llm


def _get_analyst_llm() -> AgentLLM:
    if not hasattr(_instance_local, "analyst_llm"):
        _instance_local.analyst_llm = AgentLLM()
    return _instance_local.analyst_llm


def plan_node(state: AgentState) -> dict:
    ctx = trace_manager.active
    if ctx and state.get("trace_id"):
        ctx.trace_id = state["trace_id"]

    feedback = ""
    instruction = state.get("retry_instruction", "")
    research = state.get("research_result", "")
    if instruction:
        feedback = f"\n上一次尝试的问题和修复指引:\n{instruction}\n请根据指引改进计划。"
    if research and state.get("retry_count", 0) > 0:
        feedback += f"\n上一次研究结果供参考:\n{research[:1000]}"
    if state.get("coder_truncated"):
        feedback += (
            "\n⚠ 上一次代码因 token 限制被截断。"
            "请将复杂任务拆分为多个独立的 code 步骤（每行一个 code:），"
            "或生成一个极简版本（<500 字符）。"
        )

    safe_task = state["task"].replace("{", "{{").replace("}", "}}")
    prompt = PLANNER_PROMPT.format(task=safe_task + feedback, date=get_current_date())
    try:
        response = _get_planner_llm().think([{"role": "user", "content": prompt}]) or ""
        plan = [line.strip() for line in response.split("\n") if ":" in line.strip()]
        if not plan:
            plan = [f"research: {state['task']}"]
        return {
            "plan": plan,
            "messages": [("planner", response)],
        }
    except Exception as e:
        return {
            "plan": [f"research: {state['task']}"],
            "messages": [("planner", f"ERROR: {e}")],
        }


_CODE_ONLY_ERRORS = frozenset({
    ErrorType.MISSING_CODE,
    ErrorType.RUNTIME_ERROR,
    ErrorType.NO_OUTPUT,
    ErrorType.TRUNCATION,
    ErrorType.SCHEMA_VIOLATION,
    ErrorType.LOGIC_ERROR,
})


def _should_skip_research(state: AgentState) -> bool:
    if state.get("retry_count", 0) < 1:
        return False
    etype = state.get("error_type", "")
    try:
        return ErrorType(etype) in _CODE_ONLY_ERRORS
    except ValueError:
        return False


def continue_to_agents(state: AgentState) -> list[Send]:
    sends = []
    has_research = False
    has_code = False
    code_specs = []

    skip_research = _should_skip_research(state)

    for step in state.get("plan", []):
        if "research" in step.lower() and not has_research:
            if skip_research:
                console.print("  [dim][Fan-out] 代码类错误，跳过 research 重跑[/dim]")
                continue
            query = step.split(":", 1)[1].strip() if ":" in step else state["task"]
            if not query:
                query = state["task"]
            sends.append(Send("research", {"research_query": query, "task": state["task"], "plan": state["plan"]}))
            has_research = True
        elif "code" in step.lower() and not has_code:
            spec = step.split(":", 1)[1].strip() if ":" in step else "数据分析"
            if not spec:
                spec = state["task"]
            code_specs.append(spec)
            has_code = True
    if code_specs:
        sends.append(Send("code", {
            "code_spec": code_specs[0], "code_specs": code_specs,
            "task": state["task"], "plan": state["plan"],
        }))
    if not sends:
        sends.append(Send("analyze", {"task": state["task"], "plan": state["plan"]}))
    console.print(f"  [dim][Fan-out] -> {[s.node for s in sends]}[/dim]")
    return sends


def research_node(state: AgentState) -> dict:
    query = state.get("research_query", state["task"])
    console.print(f"  [cyan][Research][/cyan] {_e(query[:60])}")
    try:
        result = _research.search(query)
    except Exception as e:
        result = ResearchOutput(summary=f"研究出错: {e}", claims=[], gaps=str(e))
        console.print(f"  [red][Research ERROR][/red] {_e(str(e))}")

    claims_dict = [c.model_dump() for c in result.claims]
    is_error = result.summary.startswith("研究出错") or result.summary.startswith("检索过程出错")
    return {
        "research_result": result.summary,
        "research_status": "error" if is_error else "ok",
        "research_claims": claims_dict,
        "messages": [("research", result.summary)],
    }


def _enrich_specs(specs: list[str], state: AgentState) -> list[str]:
    if state.get("retry_count", 0) < 1:
        return specs
    prev_code = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, tuple) and msg[0] == "coder" and not msg[1].startswith("ERROR"):
            prev_code = msg[1]
            break
    exec_err = state.get("exec_exception", "")
    if not prev_code and not exec_err:
        return specs
    context = "\n\n[上文代码修改指引]\n"
    if prev_code:
        context += f"上一次生成的代码如下，请在其基础上修改而非从零重写:\n```python\n{prev_code[:2000]}\n```\n"
    if exec_err:
        context += f"执行错误: {exec_err[:500]}\n"
    if state.get("error_type"):
        context += f"错误类型: {state['error_type']}\n"
    return [s + context for s in specs]


def code_node(state: AgentState) -> dict:
    specs = state.get("code_specs", [state.get("code_spec", "数据分析")])
    all_code = []
    all_reasoning = []
    all_expected = []
    last_truncated = False

    for spec in _enrich_specs(specs, state):
        console.print(f"  [yellow][Coder][/yellow] {_e(spec[:120])}")
        output = _coder.generate(spec)
        last_truncated = _coder._llm.last_truncated

        if output.code and output.code.strip():
            if last_truncated:
                console.print("  [yellow][Coder] 代码已生成但 LLM 输出被截断，代码可能不完整[/yellow]")
                all_reasoning.append(f"[TRUNCATED] {output.reasoning}")
            else:
                console.print("  [dim][Coder] Schema 校验通过[/dim]")
                all_reasoning.append(output.reasoning)
            console.print(f"  [dim][Coder] 代码预览: {_e(output.code[:200])}...[/dim]" if len(output.code) > 200 else f"  [dim][Coder] 代码预览: {_e(output.code)}[/dim]")
            all_code.append(output.code)
            all_expected.append(output.expected_output)
        elif last_truncated:
            console.print("  [red][Coder] LLM 输出被截断，无法提取有效代码[/red]")
            all_reasoning.append(f"[TRUNCATION] {output.reasoning[:200]}")
            all_code.append("ERROR: TRUNCATION")
            all_expected.append("")
        else:
            error_type = _coder.detect_error_type() or ErrorType.MISSING_CODE
            console.print(f"  [red][Coder] Schema 校验失败: {error_type.value}[/red]")
            all_reasoning.append(f"[{error_type.value}] {output.reasoning[:300]}")
            all_code.append(f"ERROR: {error_type.value}")
            all_expected.append("")

    combined_code = "\n\n# --- next step ---\n\n".join(all_code) if all_code else ""
    combined_reasoning = "; ".join(all_reasoning)
    combined_expected = "; ".join(e for e in all_expected if e)

    return {
        "code_result": combined_reasoning,
        "code_status": _code_status(all_code, last_truncated),
        "expected_output": combined_expected,
        "coder_truncated": last_truncated,
        "messages": [("coder", combined_code)],
    }


def _code_status(all_code: list[str], truncated: bool) -> str:
    if not all_code:
        return "error"
    if all(item.startswith("ERROR") for item in all_code):
        return "error"
    if truncated:
        return "truncated"
    return "ok"


def execute_node(state: AgentState) -> dict:
    code = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, tuple) and msg[0] == "coder" and not msg[1].startswith("ERROR"):
            code = msg[1]
            break

    if not code:
        return {
            "exec_success": False,
            "exec_stdout": "",
            "exec_stderr": "",
            "exec_exception": "No code to execute — coder produced empty or malformed output",
            "messages": [("executor", "no code to execute")],
        }

    console.print(f"  [blue][Executor][/blue] 执行代码 ({len(code)} 字符)...")
    result = _executor_agent.execute(code)
    validated = _executor_agent.validate(result)

    if validated:
        console.print(f"  [red][Executor] {_e(validated.value)}[/red]")
        if result.exception:
            console.print(f"  [red]  {_e(result.exception[:150])}[/red]")
    else:
        console.print("  [green][Executor] 执行成功[/green]")
        if result.stdout:
            console.print(f"  [dim]  stdout: {_e(result.stdout[:200])}[/dim]")

    expected = state.get("expected_output", "")
    diff: dict[str, object] = {}
    if result.success and expected and result.stdout:
        diff = _compare_output(expected, result.stdout)
        if not diff["matched"]:
            reasons = diff.get("reasons", [])
            console.print("  [yellow][Executor] 输出与预期不符:[/yellow]")
            for r in reasons:
                console.print(f"  [yellow]    - {_e(str(r))}[/yellow]")

    return {
        "exec_success": result.success,
        "exec_stdout": result.stdout,
        "exec_stderr": result.stderr,
        "exec_exception": result.exception,
        "exec_diff": diff,
        "messages": [("executor", result.stdout[:1000] if result.stdout else result.exception[:500])],
    }


def analyze_node(state: AgentState) -> dict:
    research = state.get("research_result", "无")
    code_reasoning = state.get("code_result", "无")
    exec_out = state.get("exec_stdout", "")
    exec_err = state.get("exec_exception", "")

    source_code = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, tuple) and msg[0] == "coder" and not msg[1].startswith("ERROR"):
            source_code = msg[1]
            break

    is_all_failed = (
        state.get("research_status") == "error"
        and state.get("code_status") == "error"
        and (not exec_out or exec_out.startswith("ERROR"))
    )

    if is_all_failed:
        console.print("  [yellow][Analyst] 所有上游输入均失败，跳过分析[/yellow]")
        return {
            "analysis": "任务执行失败：研究和代码生成均未产出有效结果。",
            "messages": [("analyst", "all upstream failed")],
        }

    exec_report = _build_exec_report(
        state.get("exec_success", False), exec_out, state.get("exec_stderr", ""), exec_err
    )

    no_output_to_analyze = not state.get("exec_success", True) and not exec_out
    if no_output_to_analyze:
        console.print("  [yellow][Analyst][/yellow] 执行失败且无输出，跳过 LLM 分析")
        return {
            "analysis": exec_report,
            "messages": [("analyst", "execution failed, no output to analyze")],
        }

    console.print("  [green][Analyst][/green] 综合分析...")
    try:
        prompt = ANALYST_PROMPT.format(
            task=state.get("task", ""),
            research=research[:2000],
            code=code_reasoning[:2000],
            source_code=source_code[:3000],
            exec_output=exec_out[:1000],
            exec_error=exec_err[:500],
            exec_status="成功" if state.get("exec_success", True) else "失败",
            exec_report=exec_report,
            date=get_current_date(),
        )
        analysis = _get_analyst_llm().think([{"role": "user", "content": prompt}]) or ""
    except Exception as e:
        analysis = f"分析出错: {e}"
        console.print(f"  [red][Analyst ERROR][/red] {_e(str(e))}")
    analysis = _sanitize_analysis(analysis, state.get("exec_success", False))
    analysis = exec_report + "\n\n" + analysis
    return {"analysis": analysis, "messages": [("analyst", analysis)]}


def _sanitize_analysis(text: str, exec_success: bool) -> str:
    import re
    if exec_success:
        text = re.sub(r'\*\*状态\*\*[：:]\s*❌\s*执行失败', '**状态**: ✅ 执行成功', text)
        text = re.sub(r'状态[：:]\s*❌\s*执行失败', '状态: ✅ 执行成功', text)
    else:
        text = re.sub(r'\*\*状态\*\*[：:]\s*✅\s*执行成功', '**状态**: ❌ 执行失败', text)
        text = re.sub(r'状态[：:]\s*✅\s*执行成功', '状态: ❌ 执行失败', text)
    return text


def critique_node(state: AgentState) -> dict:
    exec_result = None
    if state.get("exec_exception") or state.get("exec_stdout") or state.get("exec_stderr"):
        exec_result = ExecutionResult(
            success=state.get("exec_success", True),
            stdout=state.get("exec_stdout", ""),
            stderr=state.get("exec_stderr", ""),
            exception=state.get("exec_exception", ""),
            exit_code=0 if state.get("exec_success", True) else 1,
        )

    sources = [SourceClaim.model_validate(c) for c in state.get("research_claims", [])]

    code_output = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, tuple) and msg[0] == "coder":
            code_output = msg[1]
            break

    plan = state.get("plan", [])
    requires_code = any("code" in step.lower() for step in plan)
    requires_research = any("research" in step.lower() for step in plan)

    score, feedback, hard_verdict = _critic.evaluate(
        task=state["task"],
        answer=state.get("analysis", ""),
        output_code=code_output if code_output and not code_output.startswith("ERROR") else None,
        sources=sources if sources else None,
        exec_result=exec_result,
        task_requires_code=requires_code,
        task_requires_research=requires_research,
    )

    status = "[green]PASS[/green]" if score >= PASS_THRESHOLD else "[red]RETRY[/red]"
    console.print(f"  [magenta][Critic][/magenta] {score:.1f}/10 {_e(status)}")
    if feedback:
        console.print(f"  [red][Critic] {_e(feedback[:150])}[/red]")

    hard_dict = hard_verdict.model_dump() if hard_verdict else None

    return {
        "critique_score": score,
        "critique_feedback": feedback,
        "hard_verdict": hard_dict,
        "messages": [("critic", f"{score:.1f}: {feedback}")],
    }


def should_retry(state: AgentState) -> str:
    score = state.get("critique_score", 0)
    retry_count = state.get("retry_count", 0)

    if score >= PASS_THRESHOLD:
        return "approved"

    if retry_count >= MAX_RETRIES:
        console.print(f"  [red]已达最大重试次数 {MAX_RETRIES}，强制通过[/red]")
        return "approved"

    return "retry"


def retry_node(state: AgentState) -> dict:
    new_count = state.get("retry_count", 0) + 1
    console.print(f"\n  [bold yellow]>>> 第 {new_count} 次重试 <<<[/bold yellow]")

    exec_result = None
    if state.get("exec_exception") or state.get("exec_stdout") or state.get("exec_stderr"):
        exec_result = ExecutionResult(
            success=state.get("exec_success", True),
            stdout=state.get("exec_stdout", ""),
            stderr=state.get("exec_stderr", ""),
            exception=state.get("exec_exception", ""),
        )

    hard_verdict = state.get("hard_verdict")
    if hard_verdict:
        critic_verdict = CriticVerdict(**hard_verdict)
    else:
        critic_verdict = CriticVerdict(
            passed=False,
            score=state.get("critique_score", 0),
            feedback=state.get("critique_feedback", ""),
        )

    has_code = bool(state.get("code_result") and "ERROR" not in str(state.get("code_result", "")))
    has_sources = bool(state.get("research_claims"))

    etype = _retry_mgr.classify_error(critic_verdict, exec_result, has_code, has_sources)

    # coder_truncated 优先级最高 — 截断可能是其他错误(如 RUNTIME_ERROR)的根源
    if state.get("coder_truncated"):
        etype = ErrorType.TRUNCATION

    _retry_mgr.record_error(etype)

    instruction = ""
    if _retry_mgr.should_retry(etype, new_count):
        instruction = _retry_mgr.build_retry_instruction(etype, state.get("exec_exception", ""))

    console.print(f"  [dim]错误类型: {etype.value} → 策略: {_retry_mgr.get_strategy(etype)['strategy']}[/dim]")

    msgs = state.get("messages", [])
    if len(msgs) > 20:
        # 保留最早的 4 条（plan/research 上下文） + 最近的 8 条
        msgs = msgs[:4] + msgs[-8:]

    return {
        "retry_count": new_count,
        "error_type": etype.value,
        "retry_instruction": instruction,
        "analysis": "",
        "messages": msgs,
    }


def build_orchestrator(checkpointer=None):
    builder = StateGraph(AgentState)

    builder.add_node("plan", _trace_wrapper(plan_node, "plan_node", ["task", "retry_count"]))
    builder.add_node("research", _trace_wrapper(research_node, "research_node", ["research_query", "task"]))
    builder.add_node("code", _trace_wrapper(code_node, "code_node", ["code_spec", "task"]))
    builder.add_node("execute", _trace_wrapper(execute_node, "execute_node", ["task"]))
    builder.add_node("analyze", _trace_wrapper(analyze_node, "analyze_node", ["task"]))
    builder.add_node("critique", _trace_wrapper(critique_node, "critique_node", ["task"]))
    builder.add_node("retry", _trace_wrapper(retry_node, "retry_node", ["retry_count", "error_type"]))

    builder.add_edge(START, "plan")
    builder.add_conditional_edges("plan", continue_to_agents, ["research", "code", "analyze"])
    builder.add_edge("research", "analyze")
    builder.add_edge("code", "execute")
    builder.add_edge("execute", "analyze")
    builder.add_edge("analyze", "critique")
    builder.add_conditional_edges("critique", should_retry, {"approved": END, "retry": "retry"})
    builder.add_edge("retry", "plan")

    return builder.compile(checkpointer=checkpointer, interrupt_before=["code"])


orchestrator = build_orchestrator()

import sqlite3  # noqa: E402
from pathlib import Path  # noqa: E402

from agentnexus.core.config import get_settings  # noqa: E402

_db_dir = Path(get_settings().chroma_persist_dir).parent / "checkpoints"
_db_dir.mkdir(exist_ok=True)
_conn = sqlite3.connect(str(_db_dir / "checkpoints.db"), check_same_thread=False)
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402

checkpointer = SqliteSaver(_conn)
checkpointer.setup()
orchestrator_persistent = build_orchestrator(checkpointer=checkpointer)
