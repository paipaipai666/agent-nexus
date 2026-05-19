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

import re

from langgraph.graph import END, START, StateGraph
from rich.console import Console
from rich.markup import escape as _e

from agentnexus.agents.coder_agent import CoderAgent
from agentnexus.agents.critic_agent import PASS_THRESHOLD, CriticAgent
from agentnexus.agents.executor_agent import ExecutorAgent
from agentnexus.agents.multi_agent.state import AgentState, validate_state
from agentnexus.agents.research_agent import ResearchAgent
from agentnexus.agents.schema import ErrorType, ExecutionResult, SourceClaim
from agentnexus.core.llm import AgentLLM
from agentnexus.observability.tracer import trace_manager
from agentnexus.prompts import get_current_date, load_prompt

console = Console()

PLANNER_PROMPT = load_prompt("planner")
ANALYST_PROMPT = load_prompt("analyst")

# ── MemoryManager bridge ──────────────────────────────────────────────
# Orchestrator nodes are pure functions; MemoryManager is not serializable.
# The CLI runner injects a MemoryManager before invoking the graph.
_orchestrator_memory = None


def set_orchestrator_memory(memory):
    """Set the MemoryManager instance for the current orchestrator run."""
    global _orchestrator_memory
    _orchestrator_memory = memory


def get_orchestrator_memory():
    """Get the MemoryManager instance set by the CLI runner."""
    return _orchestrator_memory

_MAX_RETRIES: dict[str, int] = {
    "code_error": 3,
    "info_insufficient": 3,
    "analysis_incomplete": 2,
    "replan": 2,
}
ABSOLUTE_MAX_RETRIES = 5
MAX_DURATION_SEC = 180      # hard time limit per task
MAX_TOOL_CALLS = 20         # hard tool call limit per task

# ── Budget bridge (injected by CLI runner) ─────────────────────────
_budget_tracker = None


def set_budget_tracker(tracker):
    global _budget_tracker
    _budget_tracker = tracker


def get_budget_tracker():
    return _budget_tracker


def _hard_limit_check(state: AgentState, node_name: str) -> dict | None:
    """Return a forced-end dict if a hard limit is exceeded, else None."""
    import time as _time
    started = state.get("started_at", 0.0)
    if started and _time.time() - started > MAX_DURATION_SEC:
        return {
            "analysis": f"[已终止] 任务执行超过 {MAX_DURATION_SEC}s 硬限制",
            "critique_score": 0.0,
            "critique_fail_type": "replan",
            "messages": [("system", f"Hard limit: duration exceeded at {node_name}")],
        }
    if state.get("tool_call_count", 0) > MAX_TOOL_CALLS:
        return {
            "analysis": f"[已终止] 工具调用超过 {MAX_TOOL_CALLS} 次硬限制",
            "critique_score": 0.0,
            "critique_fail_type": "replan",
            "messages": [("system", f"Hard limit: tool calls exceeded at {node_name}")],
        }
    # Budget BREAK
    budget = get_budget_tracker()
    if budget and budget.state.value == "break":
        return {
            "analysis": f"[已终止] Token 预算耗尽 ({budget.used}/{budget.total})",
            "critique_score": 0.0,
            "critique_fail_type": "replan",
            "messages": [("system", f"Budget break at {node_name}")],
        }
    return None


def _trunc(text: str, max_len: int = 2000) -> str:
    if not isinstance(text, str):
        text = str(text)
    return text[:max_len] + "..." if len(text) > max_len else text


def _state_preview(state: dict, keys: list[str]) -> dict[str, str]:
    return {k: _trunc(str(state.get(k, ""))) for k in keys}


def _parse_plan(response: str, task: str) -> tuple[list[str], str, dict]:
    """Parse planner JSON response into: (plan_strings, complexity, metadata).

    Tries JSON 'steps' array first, falls back to legacy colon-separated format.
    """
    import json as _json
    stripped = response.strip()

    # Try JSON format first
    json_text = None
    match = re.search(r"```json\s*\n?(.*?)```", stripped, re.DOTALL)
    if match:
        json_text = match.group(1).strip()
    elif stripped.startswith("{"):
        json_text = stripped

    if json_text:
        try:
            data = _json.loads(json_text)
            steps = data.get("steps", [])
            if steps:
                plan = []
                for s in steps:
                    t = s.get("type", "")
                    if t == "research":
                        plan.append(f"research: {s.get('query', task)}")
                    elif t == "code":
                        plan.append(f"code: {s.get('spec', task)}")
                if not plan:
                    plan = [f"research: {task}"]
                complexity = data.get("complexity", "medium")
                metadata = {k: data[k] for k in data if k != "steps"}
                return plan, complexity, metadata
        except (_json.JSONDecodeError, TypeError):
            pass

    # Legacy format fallback
    plan = [line.strip() for line in stripped.split("\n") if ":" in line.strip()]
    if not plan:
        plan = [f"research: {task}"]
    return plan, "medium", {}


def _trace_wrapper(fn, node_name: str, input_keys: list[str]):
    def wrapped(state: AgentState) -> dict:
        # Hard limit guard
        forced = _hard_limit_check(state, node_name)
        if forced:
            return forced
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
                trace_manager._flush_span(ctx, span)
            return result
        except Exception as e:
            if span and ctx:
                ctx.end_span(span, metadata={"status": "error", "error": str(e)[:200]})
                trace_manager._flush_span(ctx, span)
            raise
    return wrapped


def _get_escalated_instruction(fail_type: str, error_history: list[dict]) -> str:
    """Generate differentiated escalating instructions based on error type and repeat count.

    Same fail_type repeating → stronger constraints. Core value from retired RetryManager._escalate().
    """
    repeats = sum(1 for e in error_history if e.get("type") == fail_type)
    detail = ""
    if error_history:
        last = error_history[-1]
        if last.get("type") == fail_type:
            detail = last.get("detail", "")

    if fail_type == "code_error":
        module = _extract_module(detail) if "ModuleNotFoundError" in detail else None
        if module and repeats >= 1:
            return (
                f"[SYSTEM] 禁止使用 {module} 库，第 {repeats + 1} 次重试："
                f"改用纯 Python 标准库实现。例如用 urllib.request 代替 requests，"
                f"用 json 代替外部解析库，用 collections.Counter 代替 nltk。"
            )
        if repeats >= 2:
            return (
                f"[SYSTEM] 代码已失败 {repeats} 次（第 {repeats + 1} 次重试）。"
                f"删除所有 import 语句中非标准库的依赖，只使用 builtins + math/collections/itertools/re。"
                f"代码必须在 1000 字符以内。"
            )
        if repeats >= 1:
            return (
                f"[SYSTEM] 上次执行失败: {detail[:200]}。请生成最小可运行版本："
                f"去掉所有非核心功能，优先输出 print() 结果而非图形。"
            )
        return f"[SYSTEM] 执行失败: {detail[:200]}。请根据错误信息修复代码。"

    if fail_type == "info_insufficient":
        if repeats >= 2:
            return (
                f"[SYSTEM] 多次检索仍无足够数据（第 {repeats + 1} 次重试）。"
                f"禁止使用任何未在搜索结果中出现的数据。信息不足时明确写'数据不可用'而非估算。"
            )
        if repeats >= 1:
            return (
                f"[SYSTEM] 检索结果不足以完成分析（第 {repeats + 1} 次重试）。"
                f"请用不同关键词重新搜索，扩大信息来源。"
            )
        return f"[SYSTEM] 检索结果不足。请扩大搜索范围或更换关键词。"

    if fail_type == "analysis_incomplete":
        if repeats >= 1:
            return (
                f"[SYSTEM] 分析仍不完整（第 {repeats + 1} 次重试）。"
                f"请只关注最重要的 2-3 个要点深入分析，不要面面俱到但浅尝辄止。"
            )
        return f"[SYSTEM] 分析不够完整。请覆盖任务的所有要求，确保每个子问题都有对应分析。"

    if fail_type == "replan":
        if repeats >= 1:
            return (
                f"[SYSTEM] 计划仍不匹配任务（第 {repeats + 1} 次重试）。"
                f"请将任务拆分为 2-3 个最小子任务，每个子任务必须有明确的产出。"
            )
        return f"[SYSTEM] 计划方向有误。请重新理解任务意图，调整计划结构。"

    return f"[SYSTEM] 请改进输出质量（第 {repeats + 1} 次尝试）。"


def _extract_module(error_text: str) -> str | None:
    m = re.search(r"No module named '(\w+)'", error_text)
    if m:
        return m.group(1)
    m = re.search(r"ModuleNotFoundError.*?['\"](\w+)['\"]", error_text)
    if m:
        return m.group(1)
    return None


# ── Global singletons (non-LLM) ─────────────────────────────────────────
_research = ResearchAgent()
_coder = CoderAgent()
_executor_agent = ExecutorAgent()
_critic = CriticAgent()

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

def _budget_aware_think(llm: AgentLLM, messages: list, task: str = "",
                        silent: bool = False, complexity: str = "") -> str:
    """LLM call with model routing coordinated with MemoryManager.

    Model routing: YELLOW/RED/BREAK → fast model.
    Context compression is delegated to MemoryManager (microcompact / LLM summary);
    this function only does model routing, no longer duplicates truncation.
    """
    from agentnexus.core.model_router import route_model
    budget = get_budget_tracker()
    state = budget.state.value if budget else "green"

    # Model routing — prefer planner-computed complexity
    model_id = route_model(task, state, complexity_override=complexity)
    if model_id != getattr(llm, "model", ""):
        try:
            llm.model = model_id
        except Exception:
            pass

    # Coordinate MemoryManager with budget state (compression is its job)
    memory = get_orchestrator_memory()
    if memory and budget:
        memory.set_budget_state(state)
        if state == "break":
            memory._skip_llm_compact = True

    return llm.think(messages, silent=silent) or ""


def _compress_messages(messages: list[dict]) -> list[dict]:
    """Aggressive context compression for RED budget."""
    compressed = []
    for m in messages:
        content = m.get("content", "")
        if len(content) > 1500:
            # Keep first and last part
            content = content[:800] + "\n...[预算限制，已截断]...\n" + content[-400:]
        compressed.append({"role": m.get("role", "user"), "content": content})
    return compressed


# ── plan_node ───────────────────────────────────────────────────────────
def plan_node(state: AgentState) -> dict:
    """Input: state.task. Output: state.plan, state.messages."""
    validate_state(state, "plan")
    ctx = trace_manager.active
    if ctx and state.get("trace_id"):
        ctx.trace_id = state["trace_id"]

    feedback = ""
    instruction = state.get("retry_instruction", "")
    critic_fb = state.get("critique_feedback", "")
    if instruction:
        feedback = f"\n上一次尝试的问题和修复指引:\n{instruction}\n请根据指引改进计划。"
    elif critic_fb:
        feedback = f"\n[Critic 反馈，请针对性改进]:\n{critic_fb}\n请根据反馈重新规划任务。"

    safe_task = state["task"].replace("{", "{{").replace("}", "}}")

    # Inject cross-session memory context if available
    memory = get_orchestrator_memory()
    memory_context = ""
    if memory:
        try:
            memory_context = memory.init_session(state["task"])
        except Exception as e:
            console.print(f"  [dim]记忆检索失败: {_e(str(e))}[/dim]")

    prompt = PLANNER_PROMPT.format(
        task=safe_task + feedback,
        date=get_current_date(),
        memory_context=memory_context,
    )

    console.rule("[bold cyan]▸ 规划阶段[/bold cyan]")

    try:
        with console.status("  [dim]规划中...[/dim]", spinner="dots"):
            response = _budget_aware_think(_get_planner_llm(), [{"role": "user", "content": prompt}],
                                            task=state.get("task", ""), silent=True)

        plan, plan_complexity, plan_metadata = _parse_plan(response, state["task"])

        console.print("  [bold]执行计划:[/bold]")
        for i, step in enumerate(plan, 1):
            console.print(f"    {i}. {_e(step)}")
        if plan_complexity:
            console.print(f"  [dim]复杂度: {plan_complexity}[/dim]")
        console.print()

        return {
            "plan": plan,
            "plan_complexity": plan_complexity,
            "plan_metadata": plan_metadata,
            "messages": [("planner", response)],
        }
    except Exception as e:
        console.print(f"  [red]规划失败: {_e(str(e))}[/red]")
        plan = [f"research: {state['task']}"]
        console.print("  [bold]执行计划:[/bold]")
        for i, step in enumerate(plan, 1):
            console.print(f"    {i}. {_e(step)}")
        console.print()
        return {
            "plan": plan,
            "plan_complexity": "medium",
            "plan_metadata": {},
            "messages": [("planner", f"ERROR: {e}")],
        }


import ast as _ast_mod

_HAS_MAIN_RE = re.compile(r'^if\s+__name__\s*==\s*["\']__main__["\']', re.MULTILINE)


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
    has_r = any(s.lower().startswith("research:") for s in plan)
    has_c = any(s.lower().startswith("code:") for s in plan)
    if has_r:
        return "research"
    if has_c:
        return "code"
    return "analyst"


# ── research_node ───────────────────────────────────────────────────────
def research_node(state: AgentState) -> dict:
    """Input: state.task, state.plan. Output: state.research_result, state.research_claims, state.messages."""
    validate_state(state, "research")
    plan = state.get("plan", [])
    query = state["task"]
    for step in plan:
        if step.lower().startswith("research:"):
            query = step.split(":", 1)[1].strip() if ":" in step else query
            break

    fail_type = state.get("critique_fail_type", "")
    critic_fb = state.get("critique_feedback", "")
    if fail_type == "info_insufficient" and critic_fb:
        # Re-search with a clean query derived from task, not raw critic feedback
        # (critic feedback is 200+ chars of prose — useless as a search keyword)
        query = f"{query} 补充检索"

    console.rule("[bold cyan]▸ 检索阶段[/bold cyan]")
    console.print(f"  [bold]查询:[/bold] {_e(query)}")

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
        console.print(f"  [dim]{_e(result.summary)}[/dim]")
        if result.claims:
            console.print(f"  [bold]来源 ({len(result.claims)} 条):[/bold]")
            for c in result.claims[:8]:
                url_str = f" [link={c.url}]{c.url}[/link]" if c.url else ""
                conf_str = f"[dim]({c.confidence:.0%})[/dim] "
                console.print(f"    {conf_str}{_e(c.claim[:120])}{url_str}")
            if len(result.claims) > 8:
                console.print(f"    [dim]... 还有 {len(result.claims) - 8} 条[/dim]")
    else:
        console.print(f"  [red]✗ {_e(result.summary)}[/red]")
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
    has_c = any(s.lower().startswith("code:") for s in plan)
    if has_c:
        return "code"
    return "analyst"


# ── code_node ───────────────────────────────────────────────────────────
def _extract_code_spec(state: AgentState) -> str:
    plan = state.get("plan", [])
    for step in plan:
        if step.lower().startswith("code:"):
            return step.split(":", 1)[1].strip() if ":" in step else state["task"]
    return state["task"]


def code_node(state: AgentState) -> dict:
    """Input: state.task, state.plan, state.retry_count, state.messages. Output: state.code_result, state.messages, state.coder_truncated."""
    validate_state(state, "code")
    spec = _extract_code_spec(state)

    # 注入 research 结果作为上下文
    research_text = state.get("research_result", "")
    if research_text and "研究出错" not in research_text:
        spec = f"{spec}\n\n[研究结果供参考]:\n{research_text[:5000]}"

    # 重试时注入上次代码 + 升级指令
    retry_count = state.get("retry_count", 0)
    if retry_count > 0:
        prev_code = ""
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, tuple) and msg[0] == "coder" and not msg[1].startswith("ERROR"):
                prev_code = msg[1]
                break
        if prev_code:
            spec += f"\n\n[上文代码修改指引]\n上一次生成的代码（⚠️ 此代码存在问题，请勿原样复制）:\n```python\n{prev_code}\n```"

        exec_err = state.get("exec_exception", "")
        fail_type = state.get("critique_fail_type", "")
        error_history = state.get("error_history", [])

        # Use escalation: same error repeated → stronger constraints
        if fail_type:
            instruction = _get_escalated_instruction(fail_type, error_history)
            spec += f"\n\n{instruction}"
        if exec_err:
            spec += f"\n上次错误详情: {exec_err}"
        if "NO_OUTPUT" in exec_err:
            spec += (
                "\n⚠️ 上述代码的问题：只定义了函数但没有在模块级调用。修复方法：在代码末尾添加\n"
                "`if __name__ == '__main__':` 块，在其中调用所有顶层函数并 print 结果。"
            )

    if retry_count == 0:
        console.rule("[bold cyan]▸ 编码阶段[/bold cyan]")
    else:
        console.rule(f"[bold yellow]▸ 编码阶段 (第 {retry_count} 次重试)[/bold yellow]")

    console.print(f"  [bold]需求:[/bold] {_e(spec)}")
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

        preview = output.code.replace('\n', '\n  ')
        console.print(f"  [dim]预览:[/dim]")
        console.print(f"  [dim]{_e(preview)}[/dim]")
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
                        "coder_truncated": last_truncated,
                        "messages": [("coder", output.code)],
                    }
        else:
            console.print("  [dim]重试自动继续 → 执行[/dim]\n")

        return {
            "code_result": output.reasoning,
            "code_status": "ok",
            "coder_truncated": last_truncated,
            "messages": [("coder", output.code)],
        }

    error_type = _coder.detect_error_type() or ErrorType.MISSING_CODE
    console.print(f"  [red]✗ Schema 校验失败: {error_type.value}[/red]\n")
    return {
        "code_result": f"[{error_type.value}] {output.reasoning}",
        "code_status": "error",
        "coder_truncated": last_truncated,
        "messages": [("coder", f"ERROR: {error_type.value}")],
    }


# ── execute_node ────────────────────────────────────────────────────────
def execute_node(state: AgentState) -> dict:
    """Input: state.messages (coder output). Output: state.exec_success, state.exec_stdout, state.exec_stderr, state.exec_exception, state.retry_count, state.messages."""
    validate_state(state, "execute")
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
            display = "\n".join(f"    {line}" for line in lines)
            console.print(f"  [dim]输出:[/dim]\n  [dim]{_e(display)}[/dim]")
            if len(lines) > 15:
                console.print(f"  [dim]... 共 {len(lines)} 行输出[/dim]")
        if result.stderr:
            console.print(f"  [yellow]stderr: {_e(result.stderr)}[/yellow]")
    else:
        if validated:
            console.print(f"  [red]✗ {_e(validated.value)}[/red]")
        else:
            console.print(f"  [red]✗ 执行失败[/red]")
        if result.exception:
            console.print(f"  [red]  {_e(result.exception)}[/red]")
        if result.stdout:
            console.print(f"  [dim]stdout: {_e(result.stdout)}[/dim]")
        if result.stderr:
            console.print(f"  [dim]stderr: {_e(result.stderr)}[/dim]")
    console.print()

    return {
        "exec_success": result.success,
        "exec_stdout": result.stdout,
        "exec_stderr": result.stderr,
        "exec_exception": result.exception,
        "retry_count": state.get("retry_count", 0) + (0 if result.success else 1),
        "tool_call_count": state.get("tool_call_count", 0) + 1,
        "messages": [("executor", result.stdout or result.exception or "")],
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
    fail_type = state.get("critique_fail_type", "code_error")
    max_for_type = _MAX_RETRIES.get(fail_type, 3)
    total_retries = len(state.get("error_history", []))

    if retry_count >= max_for_type or total_retries >= ABSOLUTE_MAX_RETRIES:
        console.rule("[bold red]▸ 重试耗尽[/bold red]")
        console.print(f"  [red]已达最大重试次数（类型={fail_type} 上限={max_for_type}，总计={total_retries}），强制通过[/red]\n")
        return "analyst"

    exc = state.get("exec_exception", "")
    need_research = "ModuleNotFoundError" in exc or (retry_count >= 2 and "NO_OUTPUT" in exc)

    if need_research:
        console.print(f"  [dim]重试前先 research 更新 API 文档[/dim]")
        return "research"
    return "code"


# ── analyst helpers (extracted from analyst_node — Harness separation of concerns) ─

def _build_exec_report(exec_success: bool, is_pure_research: bool,
                       exec_out: str, exec_err: str, retry_count: int) -> str:
    """Build deterministic execution report — no LLM involved."""
    if is_pure_research:
        console.print("  [dim]基于研究结果合成答案...[/dim]")
        return "（本次无代码执行，基于研究结果直接生成答案）"
    if exec_success:
        report = "**状态**: ✅ 执行成功"
        if retry_count > 0:
            report += f"\n**重试次数**: {retry_count}"
        if exec_out:
            report += f"\n**输出**:\n```\n{exec_out}\n```"
        console.print("  [bold green]✓ 代码执行成功[/bold green]")
        return report
    report = "**状态**: ❌ 执行失败"
    if exec_err:
        report += f"\n**原因**: 代码运行但未产生任何输出" if "NO_OUTPUT" in exec_err else f"\n**错误**: {exec_err}"
    else:
        report += "\n**错误**: 代码未生成任何输出"
    console.print("  [red]✗ 代码执行失败[/red]")
    return report


def _build_degraded_answer(exec_report: str, exec_out: str) -> str:
    """Deterministic fallback when LLM synthesis fails."""
    parts = ["## 执行报告\n", exec_report]
    if exec_out:
        parts.append(f"\n### 执行输出\n```\n{exec_out}\n```")
    parts.append("\n⚠️ LLM 分析暂时不可用，以上为系统自动生成的执行报告。")
    return "\n".join(parts)


def _synthesize_answer(state: dict, exec_report: str, is_pure_research: bool,
                       exec_success: bool, exec_out: str, exec_err: str) -> str:
    """Call analyst LLM — with deterministic degraded fallback on failure."""
    try:
        task = state.get("task", "")
        fail_type = state.get("critique_fail_type", "")
        critic_fb = state.get("critique_feedback", "")
        task_with_feedback = task
        if fail_type == "analysis_incomplete" and critic_fb:
            task_with_feedback = f"{task}\n\n[上一轮 Critic 反馈，请针对性改进]:\n{critic_fb}"
        prompt = ANALYST_PROMPT.format(
            task=task_with_feedback,
            research=state.get("research_result", "无"),
            code=state.get("code_result", "无"),
            exec_output_raw=exec_out or "（无输出）",
            exec_report=exec_report,
            date=get_current_date(),
        )
        ana_llm = _get_analyst_llm()
        plan_complexity = state.get("plan_complexity", "")
        analysis = _budget_aware_think(ana_llm, [{"role": "user", "content": prompt}],
                                       task=state.get("task", ""), silent=True,
                                       complexity=plan_complexity)
        if ana_llm.last_truncated:
            console.print("  [yellow]⚠ Analyst 输出被 LLM 截断（max_tokens），最终答案可能不完整[/yellow]")
            analysis += (
                "\n\n⚠️ [系统检测] LLM 输出在生成过程中被截断（达到 max_tokens 上限）。"
                "以上答案中的分析内容可能不完整，请检查。"
            )
        return analysis
    except Exception as e:
        console.print(f"  [red]✗ LLM 分析失败: {_e(str(e))}[/red]")
        return _build_degraded_answer(exec_report, exec_out)


def _check_data_fabrication(analysis: str, exec_out: str) -> str:
    """Detect if analyst fabricated data rows not present in actual exec output."""
    if not exec_out or not exec_out.strip():
        return analysis
    _data_row_re = re.compile(r'\d+\s+\S+')
    _real_rows = [ln for ln in exec_out.strip().split('\n') if _data_row_re.search(ln)]
    _real_count = len(_real_rows)
    if _real_count == 0:
        return analysis
    _table_blocks = re.findall(
        r'(?:#|Reference|样本|测试数据|\d+\s+\S).*?(?:\n(?:#|Reference|样本|测试数据|\d+\s+\S).*?){2,}',
        analysis, re.DOTALL,
    )
    _analysis_rows = 0
    for _block in _table_blocks:
        _analysis_rows += len([ln for ln in _block.split('\n') if _data_row_re.search(ln)])
    if _analysis_rows > _real_count:
        _warn = (
            f"\n\n⚠️ [系统检测] 以上分析中展示的数据行数({_analysis_rows}行)多于实际执行输出"
            f"({_real_count}行)。分析中的数据可能包含编造内容，实际执行只产生了 {_real_count} 行数据。"
        )
        console.print(f"  [yellow]⚠ 检测到疑似数据编造: 分析中 {_analysis_rows} 行 vs 实际 {_real_count} 行[/yellow]")
        return analysis + _warn
    return analysis


def _fix_status_display(analysis: str, exec_success: bool, is_pure_research: bool) -> str:
    """Correct any LLM-generated status text to match the actual exec result."""
    if exec_success:
        return re.sub(r'\*\*状态\*\*[：:]\s*❌\s*执行失败', '**状态**: ✅ 执行成功', analysis)
    if not is_pure_research:
        return re.sub(r'\*\*状态\*\*[：:]\s*✅\s*执行成功', '**状态**: ❌ 执行失败', analysis)
    return analysis


def _append_source_code(analysis: str, source_code: str) -> str:
    """Mechanical append — guarantees source code never truncated by LLM."""
    if source_code and source_code.strip():
        return analysis + (
            "\n\n---\n\n"
            "## 完整源代码（系统附加，可运行）\n\n"
            "```python\n"
            f"{source_code}\n"
            "```\n"
        )
    return analysis


def _evaluate_and_report(state: dict, analysis: str, source_code: str,
                         plan: list[str], has_code_step: bool, retry_count: int
                         ) -> dict:
    """Critic evaluation + error routing — extracted from analyst_node tail."""
    raw_claims = state.get("research_claims", [])
    sources = [SourceClaim(**c) for c in raw_claims] if raw_claims else []
    exec_for_critic = ExecutionResult(
        success=state.get("exec_success", False),
        stdout=state.get("exec_stdout", ""),
        stderr=state.get("exec_stderr", ""),
        exception=state.get("exec_exception", ""),
        exit_code=0 if state.get("exec_success", False) else 1,
    ) if source_code else None

    try:
        critic_score, critic_feedback, hard_fail, fail_type = _critic.evaluate(
            task=state.get("task", ""), answer=analysis,
            output_code=source_code or None, sources=sources,
            exec_result=exec_for_critic,
            task_requires_code=has_code_step,
            task_requires_research=any(s.lower().startswith("research:") for s in plan),
        )
        if hard_fail:
            console.print(f"  [red]✗ Critic 硬规则: {_e(hard_fail.fail_reason)}[/red]")
            fail_type = "replan"
        else:
            passed = critic_score >= PASS_THRESHOLD
            symbol = "✓" if passed else "✗"
            suffix = " ✓ 通过" if passed else ""
            console.print(f"  [bold]{symbol} Critic 评分: {critic_score:.1f}/10{suffix}[/bold]")
            if critic_feedback:
                console.print(f"  [dim]{_e(critic_feedback)}[/dim]")
            if not passed:
                console.print(f"  [dim]失败类型: {fail_type}[/dim]")
        console.print()
    except Exception:
        critic_score, critic_feedback, hard_fail, fail_type = 5.0, "", None, "replan"

    hard_verdict = hard_fail.model_dump() if hard_fail else None
    retry_instruction = ""
    next_retry_count = retry_count
    error_entry = None

    if hard_fail:
        retry_instruction = f"Critic 硬规则失败: {hard_fail.fail_reason}。请重新规划任务。"
        next_retry_count += 1
        error_entry = {"type": fail_type, "detail": hard_fail.fail_reason}
    elif critic_score < 7.0:
        retry_instruction = f"Critic 评分 {critic_score:.1f}/10 低于阈值 7.0。反馈: {critic_feedback}。请改进输出质量。"
        next_retry_count += 1
        error_entry = {"type": fail_type, "detail": critic_feedback[:500]}

    return {
        "critic_score": critic_score,
        "critic_feedback": critic_feedback,
        "hard_fail": hard_fail,
        "fail_type": fail_type,
        "hard_verdict": hard_verdict,
        "retry_instruction": retry_instruction,
        "next_retry_count": next_retry_count,
        "error_entry": error_entry,
    }


# ── analyst_node (thin orchestrator — delegates to helpers above) ────────
def analyst_node(state: AgentState) -> dict:
    """Input: state.task, state.plan, state.messages. Output: state.analysis, state.critique_score, state.critique_feedback, state.retry_count, state.messages, state.error_history."""
    validate_state(state, "analyst")
    console.rule("[bold cyan]▸ 综合分析阶段[/bold cyan]")

    exec_out = state.get("exec_stdout", "")
    exec_err = state.get("exec_exception", "")
    exec_success = state.get("exec_success", False)
    retry_count = state.get("retry_count", 0)

    source_code = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, tuple) and msg[0] == "coder" and not msg[1].startswith("ERROR"):
            source_code = msg[1]
            break

    plan = state.get("plan", [])
    has_code_step = any(s.lower().startswith("code:") for s in plan)
    is_pure_research = not source_code and not exec_out and not exec_err

    # 1. Build execution report (deterministic)
    exec_report = _build_exec_report(exec_success, is_pure_research, exec_out, exec_err, retry_count)

    # 2. LLM synthesis (with deterministic fallback)
    with console.status("  [dim]LLM 分析中...[/dim]", spinner="dots"):
        analysis = _synthesize_answer(state, exec_report, is_pure_research, exec_success, exec_out, exec_err)

    # Show current round's answer (regardless of Critic verdict — user sees all rounds)
    from rich.panel import Panel as _Panel
    console.print(_Panel(analysis, title=f"[dim]本回合答案预览 (共 {len(analysis)} 字符)[/dim]", border_style="dim"))

    # 3. Data fabrication check
    analysis = _check_data_fabrication(analysis, exec_out)

    # 4. Status display correction
    analysis = _fix_status_display(analysis, exec_success, is_pure_research)

    # 5. Mechanical source code append
    analysis = _append_source_code(analysis, source_code)

    # 6. Critic evaluation
    result = _evaluate_and_report(state, analysis, source_code, plan, has_code_step, retry_count)

    console.print("  [bold green]✓ 分析完成[/bold green]\n")
    final = {
        "analysis": analysis, "messages": [("analyst", analysis)],
        "critique_score": result["critic_score"],
        "critique_feedback": result["critic_feedback"],
        "critique_fail_type": result["fail_type"],
        "hard_verdict": result["hard_verdict"],
        "retry_instruction": result["retry_instruction"],
        "retry_count": result["next_retry_count"],
    }
    if result["error_entry"]:
        final["error_history"] = [result["error_entry"]]
    return final


# ── Router: analyst → END or targeted retry ────────────────────────────
def route_after_analyst(state: AgentState) -> str:
    score = state.get("critique_score", 5.0)
    retry_count = state.get("retry_count", 0)
    fail_type = state.get("critique_fail_type", "replan")
    max_for_type = _MAX_RETRIES.get(fail_type, 3)
    total_retries = len(state.get("error_history", []))

    if retry_count >= max_for_type or total_retries >= ABSOLUTE_MAX_RETRIES:
        console.print(
            f"  [red]已达最大重试次数（类型={fail_type} 上限={max_for_type}，总计={total_retries}），强制结束[/red]\n"
        )
        return "__end__"

    if score >= 7.0:
        return "__end__"

    route_map = {
        "code_error":          "code",
        "info_insufficient":   "research",
        "analysis_incomplete": "analyst",
        "replan":              "plan",
    }
    target = route_map.get(fail_type, "plan")

    # 如果任务不涉及代码，code_error 应退化为 research 而非强行 code
    plan = state.get("plan", [])
    has_code_step = any(s.lower().startswith("code:") for s in plan)
    if fail_type == "code_error" and not has_code_step:
        target = "research"

    target_labels = {
        "code_error": "代码修复",
        "info_insufficient": "补充检索",
        "analysis_incomplete": "重新分析",
        "replan": "重规划",
    }
    label = target_labels.get(fail_type, fail_type)
    console.print(f"  [yellow]Critic 评分 {score:.1f}/10 < 7.0 ({label}) → {target}[/yellow]\n")
    return target


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
    builder.add_conditional_edges("analyst", route_after_analyst, {
        "plan": "plan",
        "code": "code",
        "research": "research",
        "analyst": "analyst",
        "__end__": END,
    })

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
