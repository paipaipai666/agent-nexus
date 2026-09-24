"""UI-neutral skill service."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
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
        # The current skill is per-run-thread: concurrent chat sessions share
        # this service, and a session-scoped skill must not leak into another
        # session's auto-routing (each agent run lives on its own thread).
        self._local = threading.local()
        self._default_current: SkillEntry | None = None
        self.status = "idle"
        self.last_run = None
        self.last_route: SkillRoute | None = None
        self.selection_source = "none"
        self.enabled_skills: dict[str, bool] = {}
        # Generation counter guarding the background embedding rebuild —
        # a stale worker must not overwrite a newer index.
        self._embed_rebuild_gen = 0
        self._rebuild_router_index()

    @property
    def current(self) -> SkillEntry | None:
        # 线程内显式设置过（含 None）就尊重该值；否则回退到全局默认技能。
        if hasattr(self._local, "current"):
            return self._local.current
        return self._default_current

    @current.setter
    def current(self, entry: SkillEntry | None) -> None:
        self._local.current = entry

    def refresh(self) -> list[SkillEntry]:
        entries = self.registry.discover()
        self._rebuild_router_index()
        self.status = "error" if self.registry.errors else ("selected" if self.current else "idle")
        return entries

    def list(self) -> list[SkillEntry]:
        return [entry for entry in self.registry.list() if self.is_enabled(entry)]

    def is_enabled(self, entry: SkillEntry | str) -> bool:
        qualified_id = entry if isinstance(entry, str) else entry.qualified_id
        return self.enabled_skills.get(qualified_id, True)

    def set_enabled(self, target: str, enabled: bool) -> None:
        entry = self.registry.get(target)
        qualified_id = entry.qualified_id if entry is not None else target
        self.enabled_skills[qualified_id] = enabled
        if not enabled and self.current is not None and self.current.qualified_id == qualified_id:
            self.reset()
        self._rebuild_router_index()

    def set_enabled_map(self, enabled_map: dict[str, bool]) -> None:
        self.enabled_skills = dict(enabled_map or {})
        if self.current is not None and not self.is_enabled(self.current):
            self.reset()
        self._rebuild_router_index()

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
            # 默认技能是全局回退：所有运行线程在未自行选择时继承它。
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
            # Near-tie with no LLM: keep a soft recommendation only when there is
            # a lexical leader. Pure-semantic blips and true ties stay abstain.
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
        # P1: only hard-activate on high confidence. Near-ties stay recommendations.
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
        """Raise the auto-activate bar so soft matches do not lock a session profile."""
        if getattr(route, "source", "") == "llm":
            return True
        if len(candidates) == 1:
            return route.score >= self.router.min_score
        margin = route.score - candidates[1].score
        return route.score >= self.router.min_score * 1.25 and margin >= self.router.min_score * 0.5

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
        # Keyword-only index immediately; semantic embeddings load in a
        # background thread so startup doesn't block on the
        # torch/sentence-transformers import (~10s+ on first load).
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
                    return  # superseded by a newer rebuild
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

    def available_skill_context(
        self,
        limit: int = 20,
        recommendations: list[SkillRoute] | None = None,
        *,
        inject_all_max: int = 8,
        top_k: int = 5,
    ) -> str:
        """Build the skill catalog block for the system prompt.

        Few skills (≤ inject_all_max): inject the full catalog.
        Many skills: inject only the router shortlist (ranked top_k) so the
        prompt stays bounded and high-signal skills are not truncated away.
        """
        entries = [entry for entry in self.list() if entry.source_kind == "skill"]
        if not entries:
            return ""

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

        few = len(entries) <= inject_all_max
        if few:
            ordered: list = []
            seen: set[str] = set()
            for rec in rec_list:
                if rec.entry.qualified_id in by_id and rec.entry.qualified_id not in seen:
                    ordered.append(by_id[rec.entry.qualified_id])
                    seen.add(rec.entry.qualified_id)
            for entry in entries:
                if entry.qualified_id not in seen:
                    ordered.append(entry)
                    seen.add(entry.qualified_id)
            if rec_list:
                lines.append("")
                lines.append("All available skills:")
            for entry in ordered:
                lines.append(_line(entry))
            return "\n".join(lines) + "\n\n"

        # Many skills: rank-ordered shortlist only (do not dump registry order).
        shortlist: list = []
        seen = set()
        if rec_list:
            from agentnexus.skills.router.rank import adaptive_shortlist_len

            keep_n = adaptive_shortlist_len(
                [r.score for r in rec_list],
                min_k=min(3, top_k),
                max_k=max(top_k, 12),
            )
        else:
            keep_n = top_k
        for rec in rec_list[:keep_n]:
            entry = by_id.get(rec.entry.qualified_id) or rec.entry
            if entry.qualified_id not in seen:
                shortlist.append(entry)
                seen.add(entry.qualified_id)
        if not shortlist:
            # No ranking for this turn — keep a tiny stable sample and point at /skill list.
            shortlist = entries[:top_k]

        lines.append("")
        lines.append("Skills matching this request:")
        for entry in shortlist:
            lines.append(_line(entry))
        remaining = len(entries) - len(shortlist)
        if remaining > 0:
            lines.append(f"- ... {remaining} more skills available via /skill list")
        return "\n".join(lines) + "\n\n"
