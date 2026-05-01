from langgraph.graph import StateGraph, START, END
from langgraph.constants import Send
from langgraph.checkpoint.sqlite import SqliteSaver

from rich.console import Console

from agentnexus.core.llm import AgentLLM
from agentnexus.agents.multi_agent.state import AgentState
from agentnexus.agents.research_agent import ResearchAgent
from agentnexus.agents.coder_agent import CoderAgent
from agentnexus.agents.analyst_agent import AnalystAgent
from agentnexus.agents.critic_agent import CriticAgent

console = Console()


MAX_RETRIES = 3
PASS_THRESHOLD = 7.0

_research = ResearchAgent()
_coder = CoderAgent()
_analyst = AnalystAgent()
_critic = CriticAgent()
_planner_llm = AgentLLM()


def plan_node(state: AgentState) -> dict:
    prompt = f"""决定如何完成以下任务。必须输出至少一行，格式严格如下:
research: <搜索关键词>
code: <代码需求>

规则:
- 需要搜索信息时输出 research 行
- 需要运行/生成代码时输出 code 行
- 两者都需要就输出两行
- 不确定时必须输出 research 行

任务: {state['task']}

输出:"""
    response = _planner_llm.think([{"role": "user", "content": prompt}]) or ""
    plan = [line.strip() for line in response.split("\n") if ":" in line.strip()]
    if not plan:
        plan = [f"research: {state['task']}"]
    return {"plan": plan, "retry_count": state.get("retry_count", 0),
            "messages": [("planner", response)]}


def continue_to_agents(state: AgentState) -> list[Send]:
    sends = []
    has_research = False
    has_code = False
    for step in state.get("plan", []):
        if "research" in step.lower() and not has_research:
            query = step.split(":", 1)[1].strip() if ":" in step else state["task"]
            sends.append(Send("research", {"research_query": query, "task": state["task"], "plan": state["plan"]}))
            has_research = True
        elif "code" in step.lower() and not has_code:
            spec = step.split(":", 1)[1].strip() if ":" in step else "数据分析"
            sends.append(Send("code", {"code_spec": spec, "task": state["task"], "plan": state["plan"]}))
            has_code = True
    if not sends:
        sends.append(Send("analyze", {"task": state["task"], "plan": state["plan"]}))
    console.print(f"  [dim][Fan-out] -> {[s.node for s in sends]}[/dim]")
    return sends


def research_node(state: AgentState) -> dict:
    query = state.get("research_query", state["task"])
    console.print(f"  [cyan][Research][/cyan] {query[:60]}")
    result = _research.run(query)
    return {"research_result": result, "messages": [("research", result)]}


def code_node(state: AgentState) -> dict:
    spec = state.get("code_spec", "数据分析")
    console.print(f"  [yellow][Coder][/yellow] {spec[:60]}")
    result = _coder.run(spec)
    return {"code_result": result, "messages": [("coder", result)]}


def analyze_node(state: AgentState) -> dict:
    console.print("  [green][Analyst][/green] 综合分析...")
    analysis = _analyst.run(
        state.get("task", ""),
        state.get("research_result", ""),
        state.get("code_result", ""),
    )
    return {"analysis": analysis, "messages": [("analyst", analysis)]}


def critique_node(state: AgentState) -> dict:
    score, feedback = _critic.evaluate(state["task"], state.get("analysis", ""))
    status = "[green]PASS[/green]" if score >= PASS_THRESHOLD else "[red]RETRY[/red]"
    console.print(f"  [magenta][Critic][/magenta] {score:.1f}/10 {status}")
    if feedback:
        console.print(f"  [dim][Critic] {feedback[:150]}[/dim]")
    return {"critique_score": score, "critique_feedback": feedback,
            "messages": [("critic", f"{score:.1f}: {feedback}")]}


def should_retry(state: AgentState) -> str:
    score = state.get("critique_score", 0)
    if score >= PASS_THRESHOLD:
        return "approved"
    if state.get("retry_count", 0) >= MAX_RETRIES:
        console.print(f"  [red]已达最大重试次数 {MAX_RETRIES}，强制通过[/red]")
        return "approved"
    return "retry"


def retry_node(state: AgentState) -> dict:
    new_count = state.get("retry_count", 0) + 1
    console.print(f"\n  [bold yellow]>>> 第 {new_count} 次重试 <<<[/bold yellow]")
    return {"retry_count": new_count}


def build_orchestrator(checkpointer=None):
    builder = StateGraph(AgentState)

    builder.add_node("plan", plan_node)
    builder.add_node("research", research_node)
    builder.add_node("code", code_node)
    builder.add_node("analyze", analyze_node)
    builder.add_node("critique", critique_node)
    builder.add_node("retry", retry_node)

    builder.add_edge(START, "plan")
    builder.add_conditional_edges("plan", continue_to_agents, ["research", "code", "analyze"])
    builder.add_edge("research", "analyze")
    builder.add_edge("code", "analyze")
    builder.add_edge("analyze", "critique")
    builder.add_conditional_edges("critique", should_retry,
        {"approved": END, "retry": "retry"})
    builder.add_edge("retry", "plan")

    return builder.compile(checkpointer=checkpointer, interrupt_before=["code"])


orchestrator = build_orchestrator()

import sqlite3
from pathlib import Path
_db_dir = Path(__file__).resolve().parent.parent.parent / ".agentnexus_checkpoints"
_db_dir.mkdir(exist_ok=True)
_conn = sqlite3.connect(str(_db_dir / "checkpoints.db"), check_same_thread=False)
checkpointer = SqliteSaver(_conn)
checkpointer.setup()
orchestrator_persistent = build_orchestrator(checkpointer=checkpointer)
