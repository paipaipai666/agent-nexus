"""Service container for an AgentNexus runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppServices:
    chat: object
    skill: object
    knowledge_base: object
    eval: object
    config: object
