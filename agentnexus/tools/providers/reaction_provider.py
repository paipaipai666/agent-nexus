"""Reaction tool provider — optional emoji reaction to the user's question.

Registered only when settings.enable_user_reaction is True (default False).
The tool is entertainment: the agent decides on its own whether to react, and
silence (not calling) is the expected default for most questions.
"""

from __future__ import annotations

from agentnexus.tools.providers.base import ProviderSpec, ToolProviderContext
from agentnexus.tools.registry import ToolRegistry


class ReactionToolProvider:
    def metadata(self) -> ProviderSpec:
        return ProviderSpec(
            "reaction",
            description="Optional emoji reaction to the user's question (entertainment).",
        )

    def register(self, executor: ToolRegistry, context: ToolProviderContext) -> None:
        from agentnexus.core.config import get_settings
        from agentnexus.tools.user_reaction import REACTION_EMOJI, express_reaction

        # Off by default (like enable_contextual_retrieval): opt-in entertainment.
        # The want() whitelist only narrows further; it must never force-enable.
        try:
            enabled = get_settings().enable_user_reaction
        except Exception:
            enabled = False
        if not enabled:
            return

        before = set(executor.list_tools())
        if context.want("express_reaction"):
            executor.register_tool(
                "express_reaction",
                "对用户的提问表达一个表情反应（可选娱乐功能）。读完问题后真的有感觉才用："
                "眼前一亮/非常好奇/想吐槽/被逗乐/被问懵。"
                "普通正经问题直接正常干活，不反应就是默认行为。"
                "调用时无需在 Thought 中解释理由。",
                express_reaction,
                param_schema={
                    "type": "object",
                    "properties": {
                        "reaction": {
                            "type": "string",
                            "enum": sorted(REACTION_EMOJI),
                            "description": "反应类型",
                        },
                        "comment": {
                            "type": "string",
                            "default": "",
                            "description": "可选的一句短吐槽，显示在表情旁边",
                        },
                    },
                    "required": ["reaction"],
                },
                risk_level="low",
                rate_limit_per_min=10,
                concurrency_safe=True,
            )
        context.mark_registered(executor, before)
