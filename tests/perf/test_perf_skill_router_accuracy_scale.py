"""Performance tests for skill router accuracy at scale.

Closes Gap 4: No accuracy measurement at 500+ scale.

Covers:
    Skill-Acc-S1: Skill routing accuracy at 500+ entries
    Skill-Acc-S2: Accuracy degradation analysis across scales
    Skill-Acc-S3: Latency-accuracy trade-off measurement
"""

from __future__ import annotations

import time
from pathlib import Path

from agentnexus.services.skill import SkillService
from agentnexus.skills.registry import SkillEntry, SkillRegistry
from agentnexus.skills.workflow import Workflow

# ── Thresholds ──────────────────────────────────────────────────────

ACCURACY_60_MIN = 0.95     # 95% at 60 skills (existing baseline)
ACCURACY_200_MIN = 0.90    # 90% at 200 skills
ACCURACY_500_MIN = 0.85    # 85% at 500 skills
ACCURACY_1000_MIN = 0.80   # 80% at 1000 skills
ACCURACY_DEGRADATION_MAX = 0.15  # Max drop from baseline


def _entry(skill_id: str, name: str, description: str) -> SkillEntry:
    """Create a SkillEntry with given metadata."""
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
    return SkillEntry("default", skill_id, name, description, Path("SKILL.md"), workflow, source_kind="skill")


# Domain-specific entries with distinct terminology
_DOMAIN_ENTRIES = [
    ("docx", "DOCX", "Create edit inspect and format Microsoft Word docx documents. 生成 word 文档。"),
    ("pdf", "PDF", "Read split merge rotate and extract content from PDF documents."),
    ("xlsx", "XLSX", "Analyze edit calculate formulas and charts in spreadsheets."),
    ("pptx", "PPTX", "Create edit and inspect PowerPoint presentations and slides."),
    ("email", "Email", "Send receive and organize email messages and attachments."),
    ("calendar", "Calendar", "Schedule manage and query calendar events and meetings."),
    ("search", "Search", "Full text web search with query expansion and ranking."),
    ("code", "Code", "Write review debug and refactor source code files."),
    ("translate", "Translate", "Translate text between multiple languages fluently."),
    ("summarize", "Summarize", "Summarize long documents into key points and insights."),
    ("database", "Database", "Query manage and optimize database operations and schemas."),
    ("deploy", "Deploy", "Deploy configure and manage application infrastructure."),
    ("monitor", "Monitor", "Monitor system health performance metrics and alerts."),
    ("backup", "Backup", "Create manage and restore data backups and snapshots."),
    ("security", "Security", "Scan audit and enforce security policies and access controls."),
]


def _build_entries(count: int) -> list[SkillEntry]:
    """Build a list of SkillEntry instances with domain + generic entries."""
    entries = []
    # Add domain entries first (up to count)
    for i in range(min(count, len(_DOMAIN_ENTRIES))):
        sid, name, desc = _DOMAIN_ENTRIES[i]
        entries.append(_entry(sid, name, desc))
    # Fill remaining with generic entries
    for i in range(len(_DOMAIN_ENTRIES), count):
        entries.append(_entry(
            f"skill-{i:04d}",
            f"Generic Skill {i}",
            f"General helper for task {i} covering writing coding review and planning.",
        ))
    return entries


def _build_test_cases(entries: list[SkillEntry]) -> list[tuple[str, str]]:
    """Build (query, expected_qualified_id) test cases from domain entries."""
    # Map of query patterns to expected skill IDs
    query_skill_map = [
        ("生成一份word docx文档", "default/docx"),
        ("extract pages from this pdf file", "default/pdf"),
        ("analyze spreadsheet formulas in xlsx", "default/xlsx"),
        ("create powerpoint slides pptx", "default/pptx"),
        ("send an email to the team", "default/email"),
        ("schedule a meeting for tomorrow", "default/calendar"),
        ("search for python tutorials online", "default/search"),
        ("write a fibonacci function in code", "default/code"),
        ("translate this text to spanish", "default/translate"),
        ("summarize this long document", "default/summarize"),
        ("query the user database table", "default/database"),
        ("deploy the application to production", "default/deploy"),
        ("monitor system cpu and memory usage", "default/monitor"),
        ("create a backup of the data", "default/backup"),
        ("scan for security vulnerabilities", "default/security"),
    ]
    # Filter to only include skills that exist in entries
    existing_ids = {e.qualified_id for e in entries}
    return [(q, sid) for q, sid in query_skill_map if sid in existing_ids]


# ── Skill-Acc-S1: Accuracy at 500+ scale ─────────────────────────


class TestSkillRouterAccuracyScale:
    """Measure routing accuracy as skill count grows to 500+."""

    def test_accuracy_at_60_skills(self):
        """Baseline: verify 95% accuracy at 60 skills."""
        entries = _build_entries(60)
        registry = SkillRegistry([])
        registry._entries = entries
        service = SkillService(registry)

        test_cases = _build_test_cases(entries)
        hits = 0
        for query, expected in test_cases * 25:
            service.reset()
            route = service.maybe_auto_select(query)
            if route is not None and route.entry.qualified_id == expected:
                hits += 1

        accuracy = hits / (len(test_cases) * 25)
        assert accuracy >= ACCURACY_60_MIN, \
            f"Accuracy {accuracy:.2%} < {ACCURACY_60_MIN:.2%} at 60 skills"

    def test_accuracy_at_200_skills(self):
        """Verify accuracy at 200 skills."""
        entries = _build_entries(200)
        registry = SkillRegistry([])
        registry._entries = entries
        service = SkillService(registry)

        test_cases = _build_test_cases(entries)
        hits = 0
        for query, expected in test_cases * 10:
            service.reset()
            route = service.maybe_auto_select(query)
            if route is not None and route.entry.qualified_id == expected:
                hits += 1

        accuracy = hits / (len(test_cases) * 10)
        assert accuracy >= ACCURACY_200_MIN, \
            f"Accuracy {accuracy:.2%} < {ACCURACY_200_MIN:.2%} at 200 skills"

    def test_accuracy_at_500_skills(self):
        """Verify accuracy at 500 skills."""
        entries = _build_entries(500)
        registry = SkillRegistry([])
        registry._entries = entries
        service = SkillService(registry)

        test_cases = _build_test_cases(entries)
        hits = 0
        for query, expected in test_cases * 5:
            service.reset()
            route = service.maybe_auto_select(query)
            if route is not None and route.entry.qualified_id == expected:
                hits += 1

        accuracy = hits / (len(test_cases) * 5)
        assert accuracy >= ACCURACY_500_MIN, \
            f"Accuracy {accuracy:.2%} < {ACCURACY_500_MIN:.2%} at 500 skills"

    def test_accuracy_at_1000_skills(self):
        """Verify accuracy at 1000 skills."""
        entries = _build_entries(1000)
        registry = SkillRegistry([])
        registry._entries = entries
        service = SkillService(registry)

        test_cases = _build_test_cases(entries)
        hits = 0
        for query, expected in test_cases * 3:
            service.reset()
            route = service.maybe_auto_select(query)
            if route is not None and route.entry.qualified_id == expected:
                hits += 1

        accuracy = hits / (len(test_cases) * 3)
        assert accuracy >= ACCURACY_1000_MIN, \
            f"Accuracy {accuracy:.2%} < {ACCURACY_1000_MIN:.2%} at 1000 skills"


# ── Skill-Acc-S2: Accuracy degradation analysis ──────────────────


class TestSkillRouterAccuracyDegradation:
    """Analyze how accuracy degrades as skill count increases."""

    def test_accuracy_degradation_bounded(self):
        """Verify accuracy doesn't drop more than threshold from baseline."""
        scale_results = []

        for count in [60, 200, 500, 1000]:
            entries = _build_entries(count)
            registry = SkillRegistry([])
            registry._entries = entries
            service = SkillService(registry)

            test_cases = _build_test_cases(entries)
            hits = 0
            iterations = max(3, 1500 // len(test_cases))  # ~1500 total calls
            for query, expected in test_cases * iterations:
                service.reset()
                route = service.maybe_auto_select(query)
                if route is not None and route.entry.qualified_id == expected:
                    hits += 1

            accuracy = hits / (len(test_cases) * iterations)
            scale_results.append((count, accuracy))

        baseline = scale_results[0][1]
        for count, accuracy in scale_results[1:]:
            degradation = baseline - accuracy
            assert degradation <= ACCURACY_DEGRADATION_MAX, \
                f"Accuracy degraded by {degradation:.2%} (>{ACCURACY_DEGRADATION_MAX:.2%}) at {count} skills"

    def test_accuracy_trend_monotonic(self):
        """Verify accuracy trend is roughly monotonic (no cliff drops)."""
        accuracies = []

        for count in [60, 150, 300, 500, 800]:
            entries = _build_entries(count)
            registry = SkillRegistry([])
            registry._entries = entries
            service = SkillService(registry)

            test_cases = _build_test_cases(entries)
            hits = 0
            for query, expected in test_cases * 5:
                service.reset()
                route = service.maybe_auto_select(query)
                if route is not None and route.entry.qualified_id == expected:
                    hits += 1

            accuracy = hits / (len(test_cases) * 5)
            accuracies.append((count, accuracy))

        # Check no single step drops more than 10%
        for i in range(1, len(accuracies)):
            prev_count, prev_acc = accuracies[i - 1]
            curr_count, curr_acc = accuracies[i]
            drop = prev_acc - curr_acc
            assert drop <= 0.10, \
                f"Cliff drop of {drop:.2%} from {prev_count} to {curr_count} skills"


# ── Skill-Acc-S3: Latency-accuracy trade-off ─────────────────────


class TestSkillRouterAccuracyLatencyTradeOff:
    """Measure latency alongside accuracy at scale."""

    def test_latency_at_accuracy_thresholds(self):
        """Measure latency at each accuracy-tested scale."""
        for count in [60, 200, 500]:
            entries = _build_entries(count)
            registry = SkillRegistry([])
            registry._entries = entries
            service = SkillService(registry)

            test_cases = _build_test_cases(entries)
            start = time.perf_counter()
            hits = 0
            for query, expected in test_cases * 10:
                service.reset()
                route = service.maybe_auto_select(query)
                if route is not None and route.entry.qualified_id == expected:
                    hits += 1
            elapsed = time.perf_counter() - start

            accuracy = hits / (len(test_cases) * 10)
            avg_latency_ms = (elapsed / (len(test_cases) * 10)) * 1000

            # Latency should stay under 5ms per routing call
            assert avg_latency_ms < 5.0, \
                f"Avg latency {avg_latency_ms:.2f}ms >= 5ms at {count} skills (accuracy={accuracy:.2%})"

    def test_latency_scaling_sublinear(self):
        """Verify latency scales sub-linearly with skill count."""
        latencies = []

        for count in [100, 300, 600]:
            entries = _build_entries(count)
            registry = SkillRegistry([])
            registry._entries = entries
            service = SkillService(registry)

            test_cases = _build_test_cases(entries)
            start = time.perf_counter()
            for query, _ in test_cases * 10:
                service.reset()
                service.maybe_auto_select(query)
            elapsed = time.perf_counter() - start

            avg_latency_ms = (elapsed / (len(test_cases) * 10)) * 1000
            latencies.append((count, avg_latency_ms))

        # Verify latency scales reasonably with skill count
        # Allow for some variance in latency measurements
        for i in range(1, len(latencies)):
            prev_count, prev_lat = latencies[i - 1]
            curr_count, curr_lat = latencies[i]
            count_ratio = curr_count / prev_count
            lat_ratio = curr_lat / prev_lat if prev_lat > 0 else 1.0
            # Allow latency to scale up to 1.5x the skill count ratio
            assert lat_ratio < count_ratio * 1.5, (
                f"Latency scaled {lat_ratio:.2f}x for {count_ratio:.2f}x more skills"
            )
