"""UI-neutral skill service."""

from __future__ import annotations

import logging
import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentnexus.skills import SkillEntry, SkillRegistry, WorkflowRunResult, WorkflowRuntime, validate_session_profile
from agentnexus.skills.router import SkillRoute, SkillRouter

logger = logging.getLogger(__name__)


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
        self._local = threading.local()
        self._session_bound = False
        self._default_current: SkillEntry | None = None
        self.status = "idle"
        self.last_run = None
        self.last_route: SkillRoute | None = None
        self.selection_source = "none"
        self.enabled_skills: dict[str, bool] = {}
        self.generation = 0
        self._source: SkillService | None = None
        self._source_generation = 0
        self._embed_rebuild_gen = 0
        self._rebuild_router_index()

    @property
    def current(self) -> SkillEntry | None:
        if self._session_bound:
            return self._default_current
        if hasattr(self._local, "current"):
            return self._local.current
        return self._default_current

    @current.setter
    def current(self, entry: SkillEntry | None) -> None:
        if self._session_bound:
            self._default_current = entry
        else:
            self._local.current = entry

    def refresh(self) -> list[SkillEntry]:
        current = self.current
        was_default = current is not None and current is self._default_current
        selection_source = self.selection_source
        entries = self.registry.discover()
        if current is not None:
            try:
                refreshed = self.registry.get(current.qualified_id)
            except ValueError:
                refreshed = None
            if refreshed is None or not self.is_enabled(refreshed):
                self.reset()
                if was_default:
                    self._default_current = None
            else:
                try:
                    self.use(refreshed.qualified_id)
                    self.selection_source = selection_source
                    if was_default:
                        self._default_current = refreshed
                except Exception as exc:
                    self.reset()
                    if was_default:
                        self._default_current = None
                    self.registry.errors.append(f"{refreshed.qualified_id}: {exc}")
        self._rebuild_router_index()
        self.generation += 1
        self.status = "error" if self.registry.errors else ("selected" if self.current else "idle")
        return entries

    def for_workspace(self, workspace_path: str | Path | None, *, agent: Any = None) -> "SkillService":
        """Create an independent skill service for a session's workspace."""
        service = type(self)(
            self.registry.for_workspace(workspace_path), agent=agent,
            auto_route=self.auto_route_enabled,
            auto_route_llm_fallback=self.auto_route_llm_fallback,
            llm_client=self.llm_client, router=deepcopy(self.router),
        )
        service.set_enabled_map(self.enabled_skills)
        service._session_bound = True
        service._source = self
        service._source_generation = self.generation
        current = self._default_current or (self.current if workspace_path is None else None)
        if current is not None:
            service.use_default(current.qualified_id)
        return service

    def synchronize(self) -> None:
        """Apply shared capability changes without replacing this workspace's roots."""
        if self._source is None or self._source_generation == self._source.generation:
            return
        generation = self._source.generation
        self.set_enabled_map(self._source.enabled_skills)
        self.refresh()
        self._source_generation = generation

    def list(self) -> list[SkillEntry]:
        return [entry for entry in self.registry.list() if self.is_enabled(entry)]

    def is_enabled(self, entry: SkillEntry | str) -> bool:
        qualified_id = entry if isinstance(entry, str) else entry.qualified_id
        return self.enabled_skills.get(qualified_id, True)

    def set_enabled(self, target: str, enabled: bool) -> None:
        entry = self.registry.get(target)
        qualified_id = entry.qualified_id if entry is not None else target
        self.enabled_skills = {**self.enabled_skills, qualified_id: enabled}
        if not enabled and self.current is not None and self.current.qualified_id == qualified_id:
            self.reset()
        self._rebuild_router_index()
        self.generation += 1

    def set_enabled_map(self, enabled_map: dict[str, bool]) -> None:
        self.enabled_skills = dict(enabled_map or {})
        if self.current is not None and not self.is_enabled(self.current):
            self.reset()
        self._rebuild_router_index()
        self.generation += 1

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
        if not self.is_enabled(entry):
            self.status = "error"
            raise ValueError(f"Skill disabled: {entry.qualified_id}")
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
            self._default_current = entry
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
        entries = [entry for entry in self.list() if entry.source_kind == "skill"]
        llm_client = self.llm_client if self.auto_route_llm_fallback else None
        candidates = self.router.rank(text, entries)
        if not candidates:
            return None
        route = self.router.route_with_llm(text, entries, llm_client=llm_client)
        if route is None:
            top = candidates[0]
            has_lexical = any(
                not t.startswith("semantic_match(") for t in top.matched_terms
            )
            if not has_lexical:
                return None
            if len(candidates) >= 2 and (top.score - candidates[1].score) < 0.15:
                return None
            self.last_route = top
            self.selection_source = "recommend"
            return top
        if not self._should_hard_activate(route, candidates):
            self.last_route = route
            self.selection_source = "recommend"
            return route
        try:
            self.use(route.entry.qualified_id)
        except Exception as exc:
            self.status = "error"
            self.registry.errors.append(f"auto_skill {route.entry.qualified_id}: {exc}")
            return None
        self.last_route = route
        self.selection_source = "auto"
        return route

    def _should_hard_activate(self, route: SkillRoute, candidates: list[SkillRoute]) -> bool:
        """Raise the auto-activate bar so soft matches do not lock a session profile.

        Uses skill_auto_route_margin (router.margin) as the required score gap.
        """
        if getattr(route, "source", "") == "llm":
            return True
        if len(candidates) == 1:
            return route.score >= self.router.min_score
        gap = route.score - candidates[1].score
        return route.score >= self.router.min_score * 1.25 and gap >= self.router.margin

    def get_recommendations(self, text: str) -> list[SkillRoute]:
        """Get ranked skill recommendations for the given text.

        The router scores and ranks candidates, but does NOT decide
        whether to activate a skill. The Agent makes that decision
        using its full context (conversation history, LTM, etc.).
        """
        if not self.auto_route_enabled or self.current is not None:
            return []
        entries = [entry for entry in self.list() if entry.source_kind == "skill"]
        ranked = self.router.rank(text, entries)
        if len(ranked) >= 2 and self.llm_client is not None and self.auto_route_llm_fallback:
            ranked = self.router.llm_rerank(text, ranked, self.llm_client)
        return ranked

    def _rebuild_router_index(self) -> None:
        entries = [entry for entry in self.list() if entry.source_kind == "skill"]
        if not self.router.use_embeddings or not entries:
            self.router.rebuild(entries)
            return
        self.router.rebuild(entries, compute_embeddings=False)
        self._rebuild_embeddings_async(entries)

    def _rebuild_embeddings_async(self, entries: list[SkillEntry]) -> None:
        self._embed_rebuild_gen += 1
        gen = self._embed_rebuild_gen

        def worker() -> None:
            try:
                from agentnexus.skills.router.retrieve import compute_skill_embeddings
                embeddings = compute_skill_embeddings(entries)
                if gen != self._embed_rebuild_gen:
                    return
                self.router.rebuild(entries, embeddings=embeddings)
            except Exception as exc:
                logger.warning("Background skill embedding rebuild failed: %s", exc)

        threading.Thread(target=worker, daemon=True, name="skill-embed-rebuild").start()

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
            available=len(self.list()),
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
                for entry in self.list()
                if entry.source_kind == "skill"
            ),
        )

    def _context_window_tokens(self) -> int:
        """Resolve the active model context window (tokens)."""
        for source in (self.llm_client, getattr(self.agent, "llm_client", None)):
            caps = getattr(source, "capabilities", None)
            if caps is not None:
                n = getattr(caps, "max_context_tokens", None)
                if isinstance(n, int) and n > 0:
                    return n
        try:
            from agentnexus.core.capabilities import detect_capabilities
            from agentnexus.core.config import get_settings

            settings = get_settings()
            caps = detect_capabilities(
                getattr(settings, "llm_model_id", ""),
                getattr(settings, "llm_base_url", ""),
            )
            n = getattr(caps, "max_context_tokens", None)
            if isinstance(n, int) and n > 0:
                return n
        except Exception:
            pass
        return 128_000

    def skill_context_token_budget(
        self,
        *,
        token_budget: int | None = None,
        context_window_tokens: int | None = None,
        budget_ratio: float | None = None,
        max_tokens: int | None = None,
    ) -> int:
        """Token budget for the skill catalog block.

        Dual bound (not a single magic ratio):
        1. Soft share of the model context window (skill_context_token_ratio).
        2. Hard ceiling skill_context_max_tokens (default 4k ≈ Claude Code's
           ~16k-char available_skills budget / ~100 tokens per skill metadata).

        budget = clamp(window * ratio, 160, max_tokens)
        """
        if token_budget is not None and token_budget > 0:
            return int(token_budget)
        window = context_window_tokens or self._context_window_tokens()
        settings = None
        try:
            from agentnexus.core.config import get_settings

            settings = get_settings()
        except Exception:
            settings = None
        if budget_ratio is None:
            budget_ratio = float(getattr(settings, "skill_context_token_ratio", 0.02) or 0.02)
        if max_tokens is None:
            max_tokens = int(getattr(settings, "skill_context_max_tokens", 4000) or 4000)
        budget_ratio = max(0.001, min(float(budget_ratio), 0.2))
        max_tokens = max(200, int(max_tokens))
        return max(160, min(int(window * budget_ratio), max_tokens))

    def available_skill_context(
        self,
        limit: int = 20,
        recommendations: list[SkillRoute] | None = None,
        *,
        token_budget: int | None = None,
        context_window_tokens: int | None = None,
        budget_ratio: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Build the skill catalog block for the system prompt.

        Packs skill lines greedily under a token budget derived from the model
        context window (skill_context_token_ratio). Fits everything when the
        catalog is small; otherwise injects the rank-ordered shortlist first.
        """
        entries = [entry for entry in self.list() if entry.source_kind == "skill"]
        if not entries:
            return ""

        budget = self.skill_context_token_budget(
            token_budget=token_budget,
            context_window_tokens=context_window_tokens,
            budget_ratio=budget_ratio,
            max_tokens=max_tokens,
        )
        rec_list = list(recommendations or [])
        rec_ids = {r.entry.qualified_id for r in rec_list}
        by_id = {entry.qualified_id: entry for entry in entries}

        lines = [
            "== Available Skills ==",
            "The following local skills may be selected automatically or invoked with /<skill-id>-skill <request>.",
        ]

        if rec_list:
            lines.append("")
            lines.append("Recommended for your request (ranked by relevance):")
            for i, rec in enumerate(rec_list[:3], 1):
                lines.append(
                    f"  {i}. {rec.entry.qualified_id}: {rec.entry.display_name} "
                    f"(score={rec.score:.1f}) — {', '.join(rec.matched_terms[:3]) or 'semantic match'}"
                )

        def _line(entry) -> str:
            desc = " ".join((entry.description or "").split())[:180]
            marker = " [recommended]" if entry.qualified_id in rec_ids else ""
            return f"- {entry.qualified_id}: {entry.display_name} — {desc}{marker}"

        ordered: list = []
        seen: set[str] = set()
        for rec in rec_list:
            entry = by_id.get(rec.entry.qualified_id) or rec.entry
            if entry.qualified_id not in seen:
                ordered.append(entry)
                seen.add(entry.qualified_id)
        for entry in entries:
            if entry.qualified_id not in seen:
                ordered.append(entry)
                seen.add(entry.qualified_id)

        from agentnexus.skills.router.retrieve import estimate_text_tokens

        used = estimate_text_tokens("\n".join(lines))
        body_lines: list[str] = []
        for entry in ordered:
            line = _line(entry)
            cost = estimate_text_tokens(line) + 1
            if body_lines and used + cost > budget:
                break
            body_lines.append(line)
            used += cost

        all_fit = len(body_lines) >= len(ordered)
        if all_fit:
            if rec_list:
                lines.append("")
                lines.append("All available skills:")
            lines.extend(body_lines)
            return "\n".join(lines) + "\n\n"

        lines.append("")
        lines.append("Skills matching this request (context budget limited):")
        lines.extend(body_lines)
        remaining = len(ordered) - len(body_lines)
        if remaining > 0:
            lines.append(f"- ... {remaining} more skills available via /skill list")
        return "\n".join(lines) + "\n\n"
