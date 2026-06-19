"""Service container for an AgentNexus runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentnexus.services.config_service import ConfigService
    from agentnexus.services.eval_service import EvalService
    from agentnexus.services.knowledge_base_service import KnowledgeBaseService

    from agentnexus.services.chat import ChatService
    from agentnexus.skills import SkillRegistry


@dataclass(frozen=True)
class AppServices:
    chat: "ChatService"
    skill: "SkillRegistry"
    knowledge_base: "KnowledgeBaseService"
    eval: "EvalService"
    config: "ConfigService"
