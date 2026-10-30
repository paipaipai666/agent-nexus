"""Bind evaluation trials to the selected engine's public operations."""

from dataclasses import asdict, is_dataclass

from agentnexus.core.modules import ModuleContext, ModuleUnavailable, get_module_host


def require_default_prompt_engine() -> None:
    """Reject prompt-wording experiments for disabled or replacement engines."""
    host = get_module_host()
    if not host.is_enabled("agent"):
        raise ModuleUnavailable("agent", "disabled")
    if not host.is_default("agent"):
        raise ValueError("Prompt wording experiments require the default engine")
    host.require("agent")


def create_evaluation_agent(llm, tools, *, max_steps, conversation_mode=True, output=None, confirm_fn=None):
    """Construct an evaluation actor through the active module selection."""
    host = get_module_host()
    return host.require("agent", ModuleContext(settings=host.settings, llm=llm, tools=tools,
                        max_steps=max_steps, conversation_mode=conversation_mode,
                        output=output, confirm_fn=confirm_fn))


def result_answer(result) -> str:
    """Read a plain answer or an engine's structured result."""
    return result if isinstance(result, str) else (getattr(result, "answer", "") or "")


def result_steps(result) -> list[dict]:
    """Export optional public step records without assuming a concrete engine."""
    steps = []
    for step in getattr(result, "steps", ()):
        row = asdict(step) if is_dataclass(step) else dict(step) if isinstance(step, dict) else vars(step).copy()
        strategy = row.get("strategy_used")
        if hasattr(strategy, "name"):
            row["strategy_used"] = strategy.name
        steps.append(row)
    return steps
