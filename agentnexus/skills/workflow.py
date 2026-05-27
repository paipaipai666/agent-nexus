"""Workflow configuration models for AgentNexus skills."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

StepType = Literal["prompt", "tool_call", "retrieve", "checkpoint", "finalize"]
SkillResourceType = Literal["script", "reference", "asset"]


class PromptProfile(BaseModel):
    system: str | None = None
    fragments: list[str] = Field(default_factory=list)
    variables: dict[str, Any] = Field(default_factory=dict)


class ToolPolicy(BaseModel):
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    max_risk: str = "high"
    allow_subagents: bool = False

    @field_validator("max_risk")
    @classmethod
    def validate_risk(cls, value: str) -> str:
        normalized = value.lower().strip()
        if normalized not in {"low", "medium", "high"}:
            raise ValueError(f"unsupported risk level: {value}")
        return normalized


class MemoryPolicy(BaseModel):
    inject_long_term: bool = True
    allow_save: bool = True


class RetrievalPolicy(BaseModel):
    namespace: str = "default"
    view: str = "section"
    top_k: int = Field(default=5, ge=1, le=50)
    filters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("view")
    @classmethod
    def validate_view(cls, value: str) -> str:
        normalized = value.lower().strip()
        if normalized not in {"section", "chunk"}:
            raise ValueError(f"unsupported retrieval view: {value}")
        return normalized


class WorkflowStep(BaseModel):
    type: StepType
    id: str | None = None
    prompt: str | None = None
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class SkillResource(BaseModel):
    type: SkillResourceType
    path: str
    absolute_path: str = ""
    name: str
    size_bytes: int = 0


class Workflow(BaseModel):
    id: str
    version: str
    display_name: str
    prompt_profile: PromptProfile
    tool_policy: ToolPolicy
    steps: list[WorkflowStep]
    success_criteria: list[str]
    description: str | None = None
    entry_mode: str = "chat"
    memory_policy: MemoryPolicy = Field(default_factory=MemoryPolicy)
    retrieval_policy: RetrievalPolicy = Field(default_factory=RetrievalPolicy)
    fallbacks: list[str] = Field(default_factory=list)
    resources: list[SkillResource] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    verbs: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)


    def to_session_profile(self) -> "SessionProfile":
        return SessionProfile(
            workflow_id=self.id,
            display_name=self.display_name,
            description=self.description or "",
            prompt_profile=self.prompt_profile,
            tool_policy=self.tool_policy,
            memory_policy=self.memory_policy,
            retrieval_policy=self.retrieval_policy,
            steps=self.steps,
            success_criteria=self.success_criteria,
            resources=self.resources,
        )


class SessionProfile(BaseModel):
    workflow_id: str
    display_name: str = ""
    description: str = ""
    prompt_profile: PromptProfile
    tool_policy: ToolPolicy
    memory_policy: MemoryPolicy
    retrieval_policy: RetrievalPolicy
    steps: list[WorkflowStep]
    success_criteria: list[str] = Field(default_factory=list)
    resources: list[SkillResource] = Field(default_factory=list)


class WorkflowLoader:
    def load(self, path: str | Path) -> Workflow:
        workflow_path = Path(path)
        try:
            with open(workflow_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return Workflow.model_validate(data)
        except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
            raise ValueError(f"Invalid workflow manifest {workflow_path}: {exc}") from exc


def load_workflow(path: str | Path) -> Workflow:
    return WorkflowLoader().load(path)
