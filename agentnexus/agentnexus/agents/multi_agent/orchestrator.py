"""AgentNexus Orchestrator — 重构版 FSM

简化流水线:
  START → plan → research? → code → [HITL] → execute → {success: analyst → END
                                                          failure: code (re-route)}
 
关键变化:
- 串行执行: research 先跑，code 后跑（code 可接收 research 结果）
- 失败直接回 code，不再绕 critique→retry→plan
- Analyst 只在最后（执行成功或达上限）
- code 失败时可能先跑 research（查新 API/SDK 文档）
"""

from langgraph.graph import END, START, StateGraph
from rich.console import Console
from rich.markup import escape as _e

from agentnexus.agents.coder_agent import CoderAgent
from agentnexus.agents.executor_agent import ExecutorAgent
from agentnexus.agents.multi_agent.state import AgentState
from agentnexus.agents.research_agent import ResearchAgent
from agentnexus.agents.schema import ErrorType
from agentnexus.core.llm import AgentLLM
from agentnexus.observability.tracer import trace_manager
from agentnexus.prompts import get_current_date, load_prompt

console = Console()

PLANNER_PROMPT = load_prompt("planner")
ANALYST_PROMPT = load_prompt("analyst")

MAX_RETRIES = 3


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


# ── Global singletons (non-LLM) ─────────────────────────────────────────
_research = ResearchAgent()
_coder = CoderAgent()
_executor_agent = ExecutorAgent()

import threading
_instance_local = threading.local()

def _get_planner_llm() -> AgentLLM:
    if not hasattr(_instance_local, "planner_llm"):
        _instance_local.planner_llm = AgentLLM()
    return _instance_local.planner_llm

def _get_analyst_llm() -> AgentLLM:
    if not hasattr(_instance_local, "analyst_llm"):
        _instance_local.analyst_llm = AgentLLM()
    return _instance_local.analyst_llm


# ── plan_node ───────────────────────────────────────────────────────────
def plan_node(state: AgentState) -> dict:
    ctx = trace_manager.active
    if ctx and state.get("trace_id"):
        ctx.trace_id = state["trace_id"]

    feedback = ""
    instruction = state.get("retry_instruction", "")
    if instruction:
        feedback = f"\n上一次尝试的问题和修复指引:\n{instruction}\n请根据指引改进计划。"

    safe_task = state["task"].replace("{", "{{").replace("}", "}}")
    prompt = PLANNER_PROMPT.format(task=safe_task + feedback, date=get_current_date())

    console.rule("[bold cyan]▸ 规划阶段[/bold cyan]")

    try:
        with console.status("  [dim]规划中...[/dim]", spinner="dots"):
            response = _get_planner_llm().think([{"role": "user", "content": prompt}], silent=True) or ""

        plan = [line.strip() for line in response.split("\n") if ":" in line.strip()]
        if not plan:
            plan = [f"research: {state['task']}"]

        console.print("  [bold]执行计划:[/bold]")
        for i, step in enumerate(plan, 1):
            console.print(f"    {i}. {_e(step[:100])}")
        console.print()

        return {
            "plan": plan,
            "messages": [("planner", response)],
        }
    except Exception as e:
        console.print(f"  [red]规划失败: {_e(str(e))}[/red]")
        plan = [f"research: {state['task']}"]
        console.print("  [bold]执行计划:[/bold]")
        for i, step in enumerate(plan, 1):
            console.print(f"    {i}. {_e(step[:100])}")
        console.print()
        return {
            "plan": plan,
            "messages": [("planner", f"ERROR: {e}")],
        }


import ast as _ast_mod
import re as _re_mod

_HAS_MAIN_RE = _re_mod.compile(r'^if\s+__name__\s*==\s*["\']__main__["\']', _re_mod.MULTILINE)


def _ensure_main_block(code: str) -> str:
    """If code has no `if __name__ == '__main__':` block, auto-append module-level calls.

    Uses AST to find all top-level zero-arg functions and adds direct calls.
    """
    if _HAS_MAIN_RE.search(code):
        return code

    console.print("  [yellow][Coder] 代码缺少 __main__ 块，自动追加[/yellow]")
    try:
        tree = _ast_mod.parse(code)
        funcs = [
            node.name for node in _ast_mod.walk(tree)
            if isinstance(node, _ast_mod.FunctionDef)
            and not node.name.startswith('_')
            and not node.args.args
        ]
        if not funcs:
            funcs = [
                node.name for node in _ast_mod.walk(tree)
                if isinstance(node, _ast_mod.FunctionDef)
                and not node.name.startswith('_')
            ]
        if funcs:
            main_block = '\n\n# Auto-appended entry point\n'
            for name in funcs[:10]:
                main_block += f'print(f"\\n=== {name} ====")\n'
                main_block += f'{name}()\n'
            return code + main_block
    except SyntaxError:
        pass

    return code + '\n\nprint("Auto-executed")\n'


# ── Router: plan → research / code / both ──────────────────────────────
def route_after_plan(state: AgentState) -> str:
    plan = state.get("plan", [])
    has_r = any("research" in s.lower() for s in plan)
    has_c = any("code" in s.lower() for s in plan)
    if has_r:
        return "research"
    if has_c:
        return "code"
    return "analyst"


# ── research_node ───────────────────────────────────────────────────────
def research_node(state: AgentState) -> dict:
    plan = state.get("plan", [])
    query = state["task"]
    for step in plan:
        if "research" in step.lower():
            query = step.split(":", 1)[1].strip() if ":" in step else query
            break

    console.rule("[bold cyan]▸ 检索阶段[/bold cyan]")
    console.print(f"  [bold]查询:[/bold] {_e(query[:80])}")

    with console.status("  [dim]检索中...[/dim]", spinner="dots"):
        try:
            result = _research.search(query)
        except Exception as e:
            result = None
            console.print(f"  [red][Research ERROR][/red] {_e(str(e))}")

    if result is None:
        console.print("  [red]✗ 检索失败[/red]\n")
        return {
            "research_result": "研究出错",
            "research_status": "error",
            "research_claims": [],
            "messages": [("research", "error")],
        }

    claims_dict = [c.model_dump() for c in result.claims]
    is_error = result.summary.startswith("研究出错") or result.summary.startswith("检索过程出错")

    if not is_error:
        console.print("  [bold green]✓ 检索完成[/bold green]")
        console.print(f"  [dim]{_e(result.summary[:300])}[/dim]")
        if result.claims:
            console.print(f"  [dim]来源: {len(result.claims)} 条引用[/dim]")
    else:
        console.print(f"  [red]✗ {_e(result.summary[:100])}[/red]")
    console.print()

    return {
        "research_result": result.summary,
        "research_status": "error" if is_error else "ok",
        "research_claims": claims_dict,
        "messages": [("research", result.summary)],
    }


# ── Router: research done → code or analyst ─────────────────────────────
def route_after_research(state: AgentState) -> str:
    plan = state.get("plan", [])
    has_c = any("code" in s.lower() for s in plan)
    if has_c:
        return "code"
    return "analyst"


# ── code_node ───────────────────────────────────────────────────────────
def _extract_code_spec(state: AgentState) -> str:
    plan = state.get("plan", [])
    for step in plan:
        if "code" in step.lower():
            return step.split(":", 1)[1].strip() if ":" in step else state["task"]
    return state["task"]


def code_node(state: AgentState) -> dict:
    spec = _extract_code_spec(state)

    # 注入 research 结果作为上下文
    research_text = state.get("research_result", "")
    if research_text and "研究出错" not in research_text:
        spec = f"{spec}\n\n[研究结果供参考]:\n{research_text[:1500]}"

    # 重试时注入上次代码和错误
    retry_count = state.get("retry_count", 0)
    if retry_count > 0:
        prev_code = ""
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, tuple) and msg[0] == "coder" and not msg[1].startswith("ERROR"):
                prev_code = msg[1]
                break
        exec_err = state.get("exec_exception", "")
        if prev_code:
            spec += f"\n\n[上文代码修改指引]\n上一次生成的代码（⚠️ 此代码存在问题，请勿原样复制）:\n```python\n{prev_code[:2000]}\n```"
        if exec_err:
            spec += f"\n执行错误（需修复）: {exec_err[:500]}"
        if "NO_OUTPUT" in exec_err:
            spec += (
                "\n\n⚠️ 上述代码的问题：只定义了函数但没有在模块级调用。修复方法：在代码末尾添加\n"
                "`if __name__ == '__main__':` 块，在其中调用所有顶层函数并 print 结果。"
            )

    if retry_count == 0:
        console.rule("[bold cyan]▸ 编码阶段[/bold cyan]")
    else:
        console.rule(f"[bold yellow]▸ 编码阶段 (第 {retry_count} 次重试)[/bold yellow]")

    console.print(f"  [bold]需求:[/bold] {_e(spec[:120])}")
    if research_text and "研究出错" not in research_text:
        console.print(f"  [dim]已注入研究结果 ({len(research_text)} 字符)[/dim]")

    with console.status("  [dim]生成代码中...[/dim]", spinner="dots"):
        output = _coder.generate(spec)
    last_truncated = _coder._llm.last_truncated

    if output.code and output.code.strip():
        output.code = _ensure_main_block(output.code)
        if last_truncated:
            console.print("  [yellow]⚠ 代码已生成但 LLM 输出被截断，代码可能不完整[/yellow]")
        else:
            console.print(f"  [bold green]✓ Schema 校验通过[/bold green] ({len(output.code)} 字符)")

        preview = output.code[:200].replace('\n', '\n  ')
        console.print(f"  [dim]预览:[/dim]")
        console.print(f"  [dim]{_e(preview)}{'...' if len(output.code) > 200 else ''}[/dim]")
        console.print()

        # 仅首次确认，重试自动继续
        if retry_count == 0:
            import sys as _sys
            if _sys.stdin.isatty():
                console.print("[yellow]即将执行代码，是否继续？[/yellow] [dim](y/n)[/dim]")
                try:
                    resp = input().strip().lower()
                except (EOFError, OSError):
                    resp = 'y'
                if resp != 'y':
                    console.print("[dim]任务已取消[/dim]")
                    return {
                        "code_result": output.reasoning,
                        "code_status": "cancelled",
                        "expected_output": output.expected_output,
                        "coder_truncated": last_truncated,
                        "messages": [("coder", output.code)],
                    }
        else:
            console.print("  [dim]重试自动继续 → 执行[/dim]\n")

        return {
            "code_result": output.reasoning,
            "code_status": "ok",
            "expected_output": output.expected_output,
            "coder_truncated": last_truncated,
            "messages": [("coder", output.code)],
        }

    error_type = _coder.detect_error_type() or ErrorType.MISSING_CODE
    console.print(f"  [red]✗ Schema 校验失败: {error_type.value}[/red]\n")
    return {
        "code_result": f"[{error_type.value}] {output.reasoning[:300]}",
        "code_status": "error",
        "expected_output": "",
        "coder_truncated": last_truncated,
        "messages": [("coder", f"ERROR: {error_type.value}")],
    }


# ── execute_node ────────────────────────────────────────────────────────
def execute_node(state: AgentState) -> dict:
    code = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, tuple) and msg[0] == "coder" and not msg[1].startswith("ERROR"):
            code = msg[1]
            break

    console.rule("[bold cyan]▸ 执行阶段[/bold cyan]")

    if not code:
        console.print("  [red]✗ 无代码可执行[/red]\n")
        return {
            "exec_success": False,
            "exec_stdout": "",
            "exec_stderr": "",
            "exec_exception": "No code to execute — coder produced empty or malformed output",
            "retry_count": state.get("retry_count", 0) + 1,
            "messages": [("executor", "no code to execute")],
        }

    console.print(f"  [bold]代码长度:[/bold] {len(code)} 字符")

    with console.status("  [dim]执行中...[/dim]", spinner="dots"):
        result = _executor_agent.execute(code)
        validated = _executor_agent.validate(result)

    if result.success:
        console.print(f"  [bold green]✓ 执行成功[/bold green]")
        if result.stdout:
            lines = result.stdout.strip().split("\n")
            display = "\n".join(f"    {line}" for line in lines[:15])
            console.print(f"  [dim]输出:[/dim]\n  [dim]{_e(display[:400])}[/dim]")
            if len(lines) > 15:
                console.print(f"  [dim]... 共 {len(lines)} 行输出[/dim]")
        if result.stderr:
            console.print(f"  [yellow]stderr: {_e(result.stderr[:200])}[/yellow]")
    else:
        if validated:
            console.print(f"  [red]✗ {_e(validated.value)}[/red]")
        else:
            console.print(f"  [red]✗ 执行失败[/red]")
        if result.exception:
            console.print(f"  [red]  {_e(result.exception[:200])}[/red]")
        if result.stdout:
            console.print(f"  [dim]stdout: {_e(result.stdout[:200])}[/dim]")
        if result.stderr:
            console.print(f"  [dim]stderr: {_e(result.stderr[:200])}[/dim]")
    console.print()

    return {
        "exec_success": result.success,
        "exec_stdout": result.stdout,
        "exec_stderr": result.stderr,
        "exec_exception": result.exception,
        "retry_count": state.get("retry_count", 0) + (0 if result.success else 1),
        "messages": [("executor", result.stdout[:1000] if result.stdout else result.exception[:500])],
    }


# ── Router: execute → analyst or retry ──────────────────────────────────
_CODE_ONLY_ERRORS = frozenset({
    ErrorType.MISSING_CODE, ErrorType.RUNTIME_ERROR, ErrorType.NO_OUTPUT,
    ErrorType.TRUNCATION, ErrorType.SCHEMA_VIOLATION, ErrorType.LOGIC_ERROR,
})


def route_after_execute(state: AgentState) -> str:
    if state.get("exec_success", False):
        return "analyst"

    retry_count = state.get("retry_count", 0)
    if retry_count > MAX_RETRIES:
        console.rule("[bold red]▸ 重试耗尽[/bold red]")
        console.print(f"  [red]已达最大重试次数 {MAX_RETRIES}，强制通过[/red]\n")
        return "analyst"

    exc = state.get("exec_exception", "")
    need_research = "ModuleNotFoundError" in exc or (retry_count >= 2 and "NO_OUTPUT" in exc)

    if need_research:
        console.print(f"  [dim]重试前先 research 更新 API 文档[/dim]")
        return "research"
    return "code"


# ── analyst_node ────────────────────────────────────────────────────────
def analyst_node(state: AgentState) -> dict:
    console.rule("[bold cyan]▸ 综合分析阶段[/bold cyan]")

    research = state.get("research_result", "无")
    exec_out = state.get("exec_stdout", "")
    exec_err = state.get("exec_exception", "")
    exec_success = state.get("exec_success", False)
    retry_count = state.get("retry_count", 0)

    source_code = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, tuple) and msg[0] == "coder" and not msg[1].startswith("ERROR"):
            source_code = msg[1]
            break

    # 确定性执行报告
    if exec_success:
        lines = ["## 代码执行报告", "", "**状态**: ✅ 执行成功"]
        if retry_count > 0:
            lines.append(f"**重试次数**: {retry_count}")
        if exec_out:
            lines.extend(["", "**输出**:", "```", exec_out[:1500], "```"])
        exec_report = "\n".join(lines)
        console.print("  [bold green]✓ 代码执行成功[/bold green]")
    else:
        lines = ["## 代码执行报告", "", "**状态**: ❌ 执行失败"]
        if retry_count > 0:
            lines.append(f"**重试次数**: {retry_count}")
        if exec_err:
            if "NO_OUTPUT" in exec_err:
                lines.append("**原因**: 代码运行但未产生任何输出（缺少 `if __name__ == '__main__':` 入口）")
            else:
                lines.append(f"**错误**: {exec_err[:300]}")
        else:
            lines.append("**错误**: 代码未生成任何输出")
        exec_report = "\n".join(lines)
        console.print("  [red]✗ 代码执行失败[/red]")

    # 无输出则跳过 LLM 分析
    if not exec_success and not exec_out:
        console.print("  [yellow]执行失败且无输出，跳过 LLM 分析[/yellow]\n")
        return {
            "analysis": exec_report,
            "messages": [("analyst", "execution failed, no output")],
        }

    with console.status("  [dim]LLM 分析中...[/dim]", spinner="dots"):
        try:
            prompt = ANALYST_PROMPT.format(
                task=state.get("task", ""),
                research=research[:2000],
                code=state.get("code_result", "无")[:2000],
                source_code=source_code[:3000],
                exec_output=exec_out[:1000],
                exec_error=exec_err[:500],
                exec_status="成功" if exec_success else "失败",
                exec_report=exec_report,
                date=get_current_date(),
            )
            analysis = _get_analyst_llm().think([{"role": "user", "content": prompt}], silent=True) or ""
        except Exception as e:
            analysis = f"分析出错: {e}"
            console.print(f"  [red]✗ 分析失败: {_e(str(e))}[/red]")

    import re as _re
    if exec_success:
        analysis = _re.sub(r'\*\*状态\*\*[：:]\s*❌\s*执行失败', '**状态**: ✅ 执行成功', analysis)
    else:
        analysis = _re.sub(r'\*\*状态\*\*[：:]\s*✅\s*执行成功', '**状态**: ❌ 执行失败', analysis)

    analysis = exec_report + "\n\n" + analysis
    console.print("  [bold green]✓ 分析完成[/bold green]\n")
    return {"analysis": analysis, "messages": [("analyst", analysis)]}


# ── Build graph ─────────────────────────────────────────────────────────
def build_orchestrator(checkpointer=None):
    builder = StateGraph(AgentState)

    builder.add_node("plan", _trace_wrapper(plan_node, "plan_node", ["task", "retry_count"]))
    builder.add_node("research", _trace_wrapper(research_node, "research_node", ["research_query", "task"]))
    builder.add_node("code", _trace_wrapper(code_node, "code_node", ["code_spec", "task"]))
    builder.add_node("execute", _trace_wrapper(execute_node, "execute_node", ["task"]))
    builder.add_node("analyst", _trace_wrapper(analyst_node, "analyst_node", ["task"]))

    builder.add_edge(START, "plan")
    builder.add_conditional_edges("plan", route_after_plan, {
        "research": "research",
        "code": "code",
        "analyst": "analyst",
    })
    builder.add_conditional_edges("research", route_after_research, {
        "code": "code",
        "analyst": "analyst",
    })
    builder.add_edge("code", "execute")
    builder.add_conditional_edges("execute", route_after_execute, {
        "analyst": "analyst",
        "code": "code",
        "research": "research",
    })
    builder.add_edge("analyst", END)

    return builder.compile(checkpointer=checkpointer)


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
