"""Performance tests for skill router at scale, cache invalidation, and LLM fallback cost.

Covers:
    Skill-S1: Large-scale skill routing (500+ entries)
    Skill-S2: Skill update cache invalidation — index rebuild time
    Skill-S3: LLM fallback cost — token consumption during ambiguous routing
"""

from __future__ import annotations

import time
from pathlib import Path

from agentnexus.services.skill import SkillService
from agentnexus.skills.registry import SkillEntry, SkillRegistry
from agentnexus.skills.router import SkillRouter
from agentnexus.skills.workflow import Workflow

# ── Thresholds ──────────────────────────────────────────────────────

ROUTING_500_P95_MAX_MS = 5.0
REBUILD_500_DELTA_MAX_MS = 50.0
LLM_FALLBACK_TOKEN_CEILING = 8000


def _entry(skill_id: str, name: str, description: str) -> SkillEntry:
    workflow = Workflow.model_validate({
        "id": skill_id,
        "version": "1",
        "display_name": name,
        "description": description,
        "prompt_profile": {"system": "react"},
        "tool_policy": {"max_risk": "low"},
        "steps": [{"type": "prompt", "id": "guide", "prompt": f"Use {name}."}],
        "success_criteria": ["Done."],
    })
    return SkillEntry(
        "default", skill_id, name, description, Path("SKILL.md"), workflow, source_kind="skill"
    )


def _build_entries(count: int) -> list[SkillEntry]:
    domains = [
        ("docx", "DOCX", "Create edit inspect and format Microsoft Word documents"),
        ("pdf", "PDF", "Read split merge rotate and extract content from PDF files"),
        ("xlsx", "XLSX", "Analyze edit calculate formulas in spreadsheets"),
        ("pptx", "PPTX", "Create edit and inspect PowerPoint presentations"),
        ("email", "Email", "Send receive and organize email messages"),
        ("calendar", "Calendar", "Schedule manage and query calendar events"),
        ("search", "Search", "Full text web search with query expansion"),
        ("code", "Code", "Write review debug and refactor source code"),
        ("translate", "Translate", "Translate text between multiple languages"),
        ("summarize", "Summarize", "Summarize long documents into key points"),
    ]
    entries = []
    for i in range(min(count, len(domains))):
        sid, name, desc = domains[i]
        entries.append(_entry(sid, name, desc))
    for i in range(len(domains), count):
        entries.append(_entry(
            f"skill-{i:04d}",
            f"Generic Skill {i}",
            f"General helper for task {i} covering writing coding review and planning.",
        ))
    return entries


class _TokenTrackingLLM:
    """Mock LLM that tracks prompt token count and call count for fallback testing."""

    def __init__(self, response_skill_id: str | None = None):
        self.call_count = 0
        self.total_prompt_chars = 0
        self.total_prompt_tokens_estimate = 0
        self.response_skill_id = response_skill_id

    def think(self, messages, **kwargs):
        self.call_count += 1
        content = messages[0]["content"] if messages else ""
        self.total_prompt_chars += len(content)
        self.total_prompt_tokens_estimate += len(content) // 4
        if self.response_skill_id:
            return f'{{"skill_id": "{self.response_skill_id}"}}'
        return '{"skill_id": null}'

    @property
    def capabilities(self):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.supports_thinking = False
        m.supports_tool_calling = False
        return m


# ── Skill-S1: Large-scale skill routing (500+) ────────────────────


class TestSkillRoutingScale:
    """Measure routing latency as skill count grows to 500+."""

    def test_routing_500_entries_latency(self):
        entries = _build_entries(500)
        registry = SkillRegistry([])
        registry._entries = entries
        service = SkillService(registry)

        queries = [
            "生成一份word docx文档",
            "extract pages from this pdf file",
            "analyze spreadsheet formulas in xlsx",
            "create powerpoint slides",
            "write a python function to sort data",
            "translate this paragraph to French",
            "schedule a meeting for tomorrow",
            "search the web for recent AI news",
        ]

        times = []
        for query in queries * 10:
            service.reset()
            start = time.perf_counter()
            service.maybe_auto_select(query)
            times.append(time.perf_counter() - start)

        sorted_times = sorted(times)
        p95_idx = int(len(sorted_times) * 0.95)
        if p95_idx >= len(sorted_times):
            p95_idx = len(sorted_times) - 1
        p95_ms = sorted_times[p95_idx] * 1000

        assert p95_ms < ROUTING_500_P95_MAX_MS, (
            f"500-skill routing p95={p95_ms:.2f}ms >= {ROUTING_500_P95_MAX_MS}ms"
        )

    def test_routing_1000_entries_latency(self):
        entries = _build_entries(1000)
        registry = SkillRegistry([])
        registry._entries = entries
        service = SkillService(registry)

        start = time.perf_counter()
        for _ in range(5):
            service.reset()
            service.maybe_auto_select("analyze spreadsheet xlsx formulas")
        elapsed = time.perf_counter() - start

        per_call_ms = (elapsed / 5) * 1000
        assert per_call_ms < ROUTING_500_P95_MAX_MS * 2, (
            f"1000-skill routing avg={per_call_ms:.2f}ms too slow"
        )

    def test_routing_scales_sublinearly(self):
        """Routing time should grow sub-linearly (O(n) or better) with entry count."""
        times = {}
        for count in (100, 500, 1000):
            entries = _build_entries(count)
            registry = SkillRegistry([])
            registry._entries = entries
            service = SkillService(registry)

            start = time.perf_counter()
            for _ in range(20):
                service.reset()
                service.maybe_auto_select("create powerpoint pptx slides presentation")
            elapsed = time.perf_counter() - start
            times[count] = elapsed / 20

        ratio_5x = times[500] / max(times[100], 1e-9)
        assert ratio_5x < 10, (
            f"Routing did not scale sub-linearly: 100={times[100]:.4f}s, 500={times[500]:.4f}s, ratio={ratio_5x:.1f}x"
        )


# ── Skill-S2: Skill update cache invalidation ──────────────────────


class TestSkillCacheInvalidation:
    """Measure index rebuild cost when skill set changes."""

    def test_rebuild_after_add_one_skill(self):
        entries = _build_entries(500)
        router = SkillRouter()
        router.rebuild(entries)

        new_entry = _entry("new-skill", "New Skill", "Brand new skill for testing cache rebuild time")
        updated_entries = entries + [new_entry]

        times = []
        for _ in range(20):
            start = time.perf_counter()
            router.rebuild(updated_entries)
            times.append(time.perf_counter() - start)

        sorted_times = sorted(times)
        p95_idx = int(len(sorted_times) * 0.95)
        if p95_idx >= len(sorted_times):
            p95_idx = len(sorted_times) - 1
        p95_ms = sorted_times[p95_idx] * 1000

        assert p95_ms < REBUILD_500_DELTA_MAX_MS, (
            f"Index rebuild (500+1) p95={p95_ms:.2f}ms >= {REBUILD_500_DELTA_MAX_MS}ms"
        )

    def test_rebuild_after_remove_one_skill(self):
        entries = _build_entries(500)
        router = SkillRouter()
        router.rebuild(entries)

        reduced_entries = entries[:-1]

        start = time.perf_counter()
        router.rebuild(reduced_entries)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < REBUILD_500_DELTA_MAX_MS, (
            f"Index rebuild (remove 1 from 500) took {elapsed_ms:.2f}ms >= {REBUILD_500_DELTA_MAX_MS}ms"
        )

    def test_decide_detects_signature_change(self):
        """decide() should auto-rebuild when entries signature changes."""
        entries = _build_entries(100)
        router = SkillRouter()
        router.rebuild(entries)
        original_sig = router.index.signature

        mutated = entries + [_entry("extra", "Extra", "Extra skill")]
        router.decide("test query", mutated)

        assert router.index.signature != original_sig
        assert len(router.index.items) == 101

    def test_full_refresh_service(self):
        entries = _build_entries(500)
        registry = SkillRegistry([])
        registry._entries = entries
        service = SkillService(registry)

        registry._entries.append(
            _entry("new-skill", "New", "Newly added skill")
        )

        start = time.perf_counter()
        service.refresh()
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < REBUILD_500_DELTA_MAX_MS * 2, (
            f"Service refresh with 501 skills took {elapsed_ms:.2f}ms"
        )


# ── Skill-S3: LLM fallback cost test ──────────────────────────────


class TestSkillLLMFallbackCost:
    """Measure token consumption when deterministic routing is ambiguous and LLM fallback triggers."""

    def _make_ambiguous_entries(self) -> list[SkillEntry]:
        return [
            _entry("writer", "Writer", "Write creative fiction stories and essays"),
            _entry("editor", "Editor", "Edit and proofread written content for clarity"),
            _entry("reviewer", "Reviewer", "Review documents and provide feedback on quality"),
        ]

    def test_fallback_triggers_on_ambiguous_query(self):
        entries = self._make_ambiguous_entries()
        router = SkillRouter(min_score=2.0, margin=0.75)
        llm = _TokenTrackingLLM(response_skill_id="default/writer")

        route = router.route_with_llm("write and edit a story", entries, llm_client=llm)

        assert llm.call_count == 1, "LLM fallback should have been called once"
        assert route is not None
        assert route.source == "llm"

    def test_fallback_token_consumption_ceiling(self):
        entries = _build_entries(50)
        router = SkillRouter(min_score=999.0, margin=0.75)
        llm = _TokenTrackingLLM(response_skill_id=None)

        router.route_with_llm("general purpose task", entries, llm_client=llm)

        assert llm.call_count == 1
        assert llm.total_prompt_tokens_estimate < LLM_FALLBACK_TOKEN_CEILING, (
            f"LLM fallback used ~{llm.total_prompt_tokens_estimate} tokens >= {LLM_FALLBACK_TOKEN_CEILING}"
        )

    def test_no_fallback_when_deterministic_succeeds(self):
        entries = [
            _entry("docx", "DOCX", "Create edit and format Microsoft Word docx documents"),
            _entry("pdf", "PDF", "Read split and merge PDF documents"),
        ]
        router = SkillRouter(min_score=2.0, margin=0.75)
        llm = _TokenTrackingLLM()

        route = router.route_with_llm("generate a word docx document", entries, llm_client=llm)

        assert llm.call_count == 0, "LLM should not be called when deterministic routing succeeds"
        assert route is not None

    def test_fallback_cost_with_many_candidates(self):
        """Fallback prompt size should stay bounded even with many borderline candidates."""
        entries = _build_entries(200)
        router = SkillRouter(min_score=999.0, margin=0.75)
        llm = _TokenTrackingLLM(response_skill_id=None)

        router.route_with_llm("common task query", entries, llm_client=llm)

        assert llm.call_count == 1
        candidates_in_prompt = llm.total_prompt_chars
        assert llm.total_prompt_tokens_estimate < LLM_FALLBACK_TOKEN_CEILING, (
            f"Fallback with 200 candidates: ~{llm.total_prompt_tokens_estimate} tokens"
        )
        assert llm.total_prompt_chars < 10000, (
            f"Fallback prompt too large: {candidates_in_prompt} chars"
        )

    def test_service_auto_select_llm_cost(self):
        entries = self._make_ambiguous_entries()
        registry = SkillRegistry([])
        registry._entries = entries
        llm = _TokenTrackingLLM(response_skill_id="default/writer")
        service = SkillService(registry, llm_client=llm, auto_route_llm_fallback=True)

        service.maybe_auto_select("write and edit content with review")

        assert llm.total_prompt_tokens_estimate < LLM_FALLBACK_TOKEN_CEILING
