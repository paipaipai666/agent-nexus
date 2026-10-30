"""Default provider factories; importing this file loads no implementations."""

from __future__ import annotations

from agentnexus.core.modules import ModuleContext


def agent(context: ModuleContext):
    """Construct the default engine only when it is selected."""
    from agentnexus.agents.re_act_agent import ReActAgent
    from agentnexus.core.llm import AgentLLM
    from agentnexus.tools.registry import ToolRegistry

    llm = context.llm_factory() if context.llm_factory is not None else context.llm
    return ReActAgent(
        llm if llm is not None else AgentLLM(),
        context.tools if context.tools is not None else ToolRegistry(),
        conversation_mode=context.conversation_mode,
        max_steps=context.max_steps,
        token_budget=context.token_budget,
        agent_id=context.agent_id,
        output=context.output,
        confirm_fn=context.confirm_fn,
    )


def short_term(context: ModuleContext):
    """Construct one session's retained conversation implementation."""
    from agentnexus.memory.short_term import ShortTermMemory

    return ShortTermMemory()


def long_term(context: ModuleContext):
    """Construct the runtime's selected long-term memory store."""
    from agentnexus.memory.long_term import LongTermMemory

    return LongTermMemory(settings=context.settings)


def wiki(context: ModuleContext):
    """Construct the runtime's selected Wiki service."""
    from agentnexus.wiki.store import WikiStore
    from agentnexus.wiki.wiki_service import WikiService

    store = WikiStore(db_path=context.settings.rag_catalog_db_path)
    return WikiService(store=store, owns_store=True, settings=context.settings)


def evaluation(context: ModuleContext):
    """Construct the runtime's selected evaluation service."""
    import importlib

    importlib.import_module("agentnexus.evaluation")
    importlib.import_module("agentnexus.eval")
    from agentnexus.services.eval import EvalService

    return EvalService(settings=context.settings)


def observability(context: ModuleContext):
    """Construct the runtime's selected diagnostic backend."""
    from agentnexus.observability.backend import ObservabilityBackend

    return ObservabilityBackend(context.settings)
