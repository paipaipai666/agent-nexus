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


_research = ResearchAgent()
_coder = CoderAgent()
_executor_agent = ExecutorAgent()
_critic = CriticAgent()
_hard_checker = HardRuleChecker()
_retry_mgr = RetryManager()
_analyst_llm = AgentLLM()
_planner_llm = AgentLLM()


def plan_node(state: AgentState) -> dict:
    ctx = trace_manager.active
    if ctx and state.get("trace_id"):
        ctx.trace_id = state["trace_id"]

    feedback = ""
    instruction = state.get("retry_instruction", "")
    if instruction:
        feedback = f"\n上一次尝试的问题和修复指引:\n{instruction}\n请根据指引改进计划。"

    prompt = PLANNER_PROMPT.format(task=state["task"] + feedback, date=get_current_date())
    try:
        response = _planner_llm.think([{"role": "user", "content": prompt}]) or ""
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


def continue_to_agents(state: AgentState) -> list[Send]:
    sends = []
    has_research = False
    has_code = False
    for step in state.get("plan", []):
        if "research" in step.lower() and not has_research:
            query = step.split(":", 1)[1].strip() if ":" in step else state["task"]
            if not query:
                query = state["task"]
            sends.append(Send("research", {"research_query": query, "task": state["task"], "plan": state["plan"]}))
            has_research = True
        elif "code" in step.lower() and not has_code:
            spec = step.split(":", 1)[1].strip() if ":" in step else "数据分析"
            if not spec:
                spec = state["task"]
            sends.append(Send("code", {"code_spec": spec, "task": state["task"], "plan": state["plan"]}))
            has_code = True
    if not sends:
        sends.append(Send("analyze", {"task": state["task"], "plan": state["plan"]}))
    console.print(f"  [dim][Fan-out] -> {[s.node for s in sends]}[/dim]")
    return sends


def research_node(state: AgentState) -> dict:
    query = state.get("research_query", state["task"])
    console.print(f"  [cyan][Research][/cyan] {query[:60]}")
    try:
        result = _research.search(query)
    except Exception as e:
        result = ResearchOutput(summary=f"研究出错: {e}", claims=[], gaps=str(e))
        console.print(f"  [red][Research ERROR][/red] {e}")

    claims_dict = [c.model_dump() for c in result.claims]
    return {
        "research_result": result.summary,
        "research_claims": claims_dict,
        "messages": [("research", result.summary)],
    }


def code_node(state: AgentState) -> dict:
    spec = state.get("code_spec", "数据分析")
    console.print(f"  [yellow][Coder][/yellow] {spec[:60]}")

    output = _coder.generate(spec)

    if not output.code or not output.code.strip():
        error_type = _coder.detect_error_type() or ErrorType.MISSING_CODE
        console.print(f"  [red][Coder] Schema 校验失败: {error_type.value}[/red]")
        return {
            "code_result": f"[{error_type.value}] {output.reasoning[:300]}",
            "messages": [("coder", f"ERROR: {error_type.value}")],
        }

    console.print("  [dim][Coder] Schema 校验通过，准备执行...[/dim]")
    return {
        "code_result": output.reasoning,
        "expected_output": output.expected_output,
        "messages": [("coder", output.code)],
    }


def execute_node(state: AgentState) -> dict:
    code = ""
    for msg in state.get("messages", []):
        if isinstance(msg, tuple) and msg[0] == "coder" and not msg[1].startswith("ERROR"):
            code = msg[1]
            break

    if not code:
        return {
            "exec_success": True,
            "exec_stdout": "",
            "exec_stderr": "",
            "exec_exception": "",
            "messages": [("executor", "no code to execute")],
        }

    console.print(f"  [blue][Executor][/blue] 执行代码 ({len(code)} 字符)...")
    result = _executor_agent.execute(code)
    validated = _executor_agent.validate(result)

    if validated:
        console.print(f"  [red][Executor] {validated.value}[/red]")
        if result.exception:
            console.print(f"  [red]  {result.exception[:150]}[/red]")
    else:
        console.print("  [green][Executor] 执行成功[/green]")
        if result.stdout:
            console.print(f"  [dim]  stdout: {result.stdout[:200]}[/dim]")

    expected = state.get("expected_output", "")
    diff = {}
    if result.success and expected and result.stdout:
        actual = result.stdout.strip()
        expected_stripped = expected.strip()
        matched = expected_stripped in actual or actual in expected_stripped
        diff = {
            "matched": matched,
            "expected": expected_stripped[:200],
            "actual": actual[:200],
            "detail": "" if matched else f"预期:\n{expected_stripped[:200]}\n实际:\n{actual[:200]}",
        }
        if not matched:
            console.print("  [yellow][Executor] 输出与预期不符[/yellow]")

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

    is_all_failed = (
        (not research or research.startswith("研究出错") or research.startswith("检索过程出错"))
        and (not code_reasoning or code_reasoning.startswith("ERROR") or code_reasoning.startswith("["))
        and (not exec_out or exec_out.startswith("ERROR"))
    )

    if is_all_failed:
        console.print("  [yellow][Analyst][/yellow] 所有上游输入均失败，跳过分析")
        return {
            "analysis": "任务执行失败：研究和代码生成均未产出有效结果。",
            "messages": [("analyst", "all upstream failed")],
        }

    console.print("  [green][Analyst][/green] 综合分析...")
    try:
        prompt = ANALYST_PROMPT.format(
            task=state.get("task", ""),
            research=research[:2000],
            code=code_reasoning[:2000],
            exec_output=exec_out[:1000],
            exec_error=exec_err[:500],
            date=get_current_date(),
        )
        analysis = _analyst_llm.think([{"role": "user", "content": prompt}]) or ""
    except Exception as e:
        analysis = f"分析出错: {e}"
        console.print(f"  [red][Analyst ERROR][/red] {e}")
    return {"analysis": analysis, "messages": [("analyst", analysis)]}


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

    sources = [SourceClaim(**c) for c in state.get("research_claims", [])]

    code_output = ""
    for msg in state.get("messages", []):
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
    console.print(f"  [magenta][Critic][/magenta] {score:.1f}/10 {status}")
    if feedback:
        console.print(f"  [dim][Critic] {feedback[:150]}[/dim]")

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
    _retry_mgr.record_error(etype)

    instruction = ""
    if _retry_mgr.should_retry(etype, new_count):
        instruction = _retry_mgr.build_retry_instruction(etype, state.get("exec_exception", ""))

    console.print(f"  [dim]错误类型: {etype.value} → 策略: {_retry_mgr.get_strategy(etype)['strategy']}[/dim]")

    msgs = state.get("messages", [])
    if len(msgs) > 20:
        msgs = msgs[-12:]

    return {
        "retry_count": new_count,
        "error_type": etype.value,
        "retry_instruction": instruction,
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
