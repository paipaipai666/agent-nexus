"""UI-neutral skill service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentnexus.skills import SkillEntry, SkillRegistry, WorkflowRunResult, WorkflowRuntime, validate_session_profile
from agentnexus.skills.router import SkillRoute, SkillRouter


@dataclass(frozen=True)
class SkillStatus:
    current: str = "default/default"
    status: str = "idle"
    available: int = 0
    errors: tuple[str, ...] = ()
    last_run_id: str = ""
    last_run_status: str = ""
    step_count: int = 0
    ok_steps: int = 0
    error_steps: int = 0
    skipped_steps: int = 0
    scripts: int = 0
    references: int = 0
    assets: int = 0
    auto_route_enabled: bool = True
    auto_route_reason: str = ""
    auto_route_score: float = 0.0
    auto_route_source: str = ""
    available_skills: tuple[tuple[str, str, str], ...] = ()


class SkillService:
    """Discover, validate, and apply session skills."""

    def __init__(
        self,
        registry: SkillRegistry,
        agent: Any = None,
        *,
        auto_route: bool = True,
        auto_route_llm_fallback: bool = True,
        llm_client: Any = None,
        router: SkillRouter | None = None,
    ):
        self.registry = registry
        self.agent = agent
        self.runtime = WorkflowRuntime()
        self.router = router or SkillRouter()
        self.auto_route_enabled = auto_route
        self.auto_route_llm_fallback = auto_route_llm_fallback
        self.llm_client = llm_client
        self.current: SkillEntry | None = None
        self.status = "idle"
        self.last_run = None
        self.last_route: SkillRoute | None = None
        self.selection_source = "none"
        self._rebuild_router_index()

    def refresh(self) -> list[SkillEntry]:
        entries = self.registry.discover()
        self._rebuild_router_index()
        self.status = "error" if self.registry.errors else ("selected" if self.current else "idle")
        return entries

    def list(self) -> list[SkillEntry]:
        return self.registry.list()

    def validate(self, target: str | None = None) -> list[str]:
        errors = self.registry.validate(target)
        self.status = "error" if errors else ("selected" if self.current else "idle")
        return errors

    def use(self, target: str) -> SkillEntry:
        try:
            entry = self.registry.get(target)
        except ValueError:
            self.status = "error"
            raise
        if entry is None:
            self.status = "error"
            raise ValueError(f"Skill not found: {target}")
        profile = entry.workflow.to_session_profile()
        try:
            validate_session_profile(profile)
        except Exception:
            self.status = "error"
            raise
        if self.agent is not None and hasattr(self.agent, "set_session_profile"):
            try:
                self.agent.set_session_profile(profile)
            except Exception:
                self.status = "error"
                raise
        self.current = entry
        self.status = "selected"
        self.last_route = None
        self.selection_source = "manual"
        return entry

    def use_default(self, target: str | None) -> SkillEntry | None:
        if not target:
            return None
        try:
            entry = self.use(target)
            self.selection_source = "default"
            return entry
        except Exception as exc:
            self.status = "error"
            self.registry.errors.append(f"default_skill {target}: {exc}")
            return None

    def reset(self) -> None:
        if self.agent is not None and hasattr(self.agent, "set_session_profile"):
            self.agent.set_session_profile(None)
        self.current = None
        self.last_run = None
        self.last_route = None
        self.selection_source = "none"
        self.status = "idle"

    def maybe_auto_select(self, text: str) -> SkillRoute | None:
        if not self.auto_route_enabled or self.current is not None:
            return None
        entries = [entry for entry in self.registry.list() if entry.source_kind == "skill"]
        llm_client = self.llm_client if self.auto_route_llm_fallback else None
        route = self.router.route_with_llm(text, entries, llm_client=llm_client)
        if route is None:
            return None
        try:
            self.use(route.entry.qualified_id)
        except Exception as exc:
            self.status = "error"
            self.registry.errors.append(f"auto_skill {route.entry.qualified_id}: {exc}")
            return None
        self.last_route = route
        self.selection_source = "auto"
        return route

    def _rebuild_router_index(self) -> None:
        entries = [entry for entry in self.registry.list() if entry.source_kind == "skill"]
        self.router.rebuild(entries)

    def prepare_message(
        self,
        text: str,
        *,
        tool_executor: Any = None,
        memory_manager: Any = None,
        auto_select: bool = True,
    ) -> WorkflowRunResult:
        if auto_select:
            self.maybe_auto_select(text)
        if self.current is None:
            return WorkflowRunResult(question=text, workflow_context="", events=[])
        profile = self.current.workflow.to_session_profile()
        result = self.runtime.prepare(
            text,
            profile,
            tool_executor=tool_executor,
            memory_manager=memory_manager,
        )
        self.last_run = result.state
        return result

    def snapshot(self) -> SkillStatus:
        current = self.current.qualified_id if self.current else "default/default"
        last_run = self.last_run
        resources = getattr(getattr(self.current, "workflow", None), "resources", []) or []
        return SkillStatus(
            current=current,
            status=self.status,
            available=len(self.registry.list()),
            errors=tuple(self.registry.errors),
            last_run_id=getattr(last_run, "run_id", "") or "",
            last_run_status=getattr(last_run, "status", "") or "",
            step_count=len(getattr(last_run, "steps", []) or []),
            ok_steps=getattr(last_run, "ok_count", 0) or 0,
            error_steps=getattr(last_run, "error_count", 0) or 0,
            skipped_steps=getattr(last_run, "skipped_count", 0) or 0,
            scripts=sum(1 for resource in resources if resource.type == "script"),
            references=sum(1 for resource in resources if resource.type == "reference"),
            assets=sum(1 for resource in resources if resource.type == "asset"),
            auto_route_enabled=self.auto_route_enabled,
            auto_route_reason=getattr(self.last_route, "reason", "") or "",
            auto_route_score=getattr(self.last_route, "score", 0.0) or 0.0,
            auto_route_source=getattr(self.last_route, "source", "") or "",
            available_skills=tuple(
                (entry.qualified_id, entry.display_name, entry.description)
                for entry in self.registry.list()
                if entry.source_kind == "skill"
            ),
        )

    def available_skill_context(self, limit: int = 20) -> str:
        entries = [entry for entry in self.registry.list() if entry.source_kind == "skill"]
        if not entries:
            return ""
        lines = [
            "== Available Skills ==",
            "The following local skills may be selected automatically or invoked with /<skill-id>-skill <request>.",
        ]
        for entry in entries[:limit]:
            desc = " ".join((entry.description or "").split())[:180]
            lines.append(f"- {entry.qualified_id}: {entry.display_name} — {desc}")
        if len(entries) > limit:
            lines.append(f"- ... {len(entries) - limit} more skills available via /skill list")
        return "\n".join(lines) + "\n\n"
