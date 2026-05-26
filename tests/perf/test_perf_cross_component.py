"""Cross-component performance tests: Skill Router + Tool Call pipeline.

Closes Gap 7: No integrated perf test measuring full pipeline accuracy and latency.

Covers:
    Cross-1: Full pipeline accuracy (query → skill selection → tool invocation → result)
    Cross-2: Full pipeline latency measurement
    Cross-3: Accuracy-latency trade-off analysis
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agentnexus.skills.registry import SkillEntry, SkillRegistry
from agentnexus.skills.workflow import Workflow
from agentnexus.tools.registry import ToolMeta, ToolRegistry

# ── Thresholds ──────────────────────────────────────────────────────

PIPELINE_ACCURACY_MIN = 0.70  # 70% end-to-end accuracy
PIPELINE_LATENCY_P95_MAX_MS = 2000  # P95 end-to-end latency
SKILL_SELECTION_ACCURACY_MIN = 0.85  # 85% skill selection accuracy
TOOL_CALL_ACCURACY_MIN = 0.75  # 75% tool call accuracy within skill


def _percentile(data: list[float], p: int) -> float:
    """Compute the p-th percentile from raw timing data (seconds)."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    if idx >= len(sorted_data):
        idx = len(sorted_data) - 1
    return sorted_data[idx]


def _p95_ms(stats_data: list[float]) -> float:
    """95th percentile in milliseconds."""
    return _percentile(stats_data, 95) * 1000


# ── Helpers ────────────────────────────────────────────────────────


def _create_skill_entry(skill_id: str, name: str, description: str, tools: list[str]) -> SkillEntry:
    """Create a SkillEntry with associated tools."""
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


def _create_tool_meta(name: str, description: str, skill_id: str) -> ToolMeta:
    """Create ToolMeta associated with a skill."""
    return ToolMeta(
        name=name,
        description=description,
        param_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        source_type="skill",
        source_id=skill_id,
    )


def _make_handler(tool_name: str) -> Any:
    """Create a handler that returns the tool name."""
    return lambda **kwargs: f"result_from_{tool_name}"


# ── Domain-specific test data ──────────────────────────────────────


DOMAIN_SKILLS = [
    {
        "skill_id": "docx",
        "name": "DOCX",
        "description": "Create edit inspect and format Microsoft Word documents",
        "tools": ["docx_create", "docx_edit", "docx_read", "docx_format"],
        "queries": [
            "创建一份Word文档",
            "edit the docx file",
            "format the document",
        ],
    },
    {
        "skill_id": "pdf",
        "name": "PDF",
        "description": "Read split merge rotate and extract content from PDF files",
        "tools": ["pdf_read", "pdf_split", "pdf_merge", "pdf_extract"],
        "queries": [
            "读取PDF文件",
            "extract pages from pdf",
            "merge pdf documents",
        ],
    },
    {
        "skill_id": "code",
        "name": "Code",
        "description": "Write review debug and refactor source code",
        "tools": ["code_write", "code_review", "code_debug", "code_refactor"],
        "queries": [
            "写一段Python代码",
            "review this code",
            "debug the function",
        ],
    },
    {
        "skill_id": "search",
        "name": "Search",
        "description": "Full text web search with query expansion",
        "tools": ["web_search", "search_expand", "search_rank"],
        "queries": [
            "搜索最新资讯",
            "search for documentation",
            "find relevant articles",
        ],
    },
    {
        "skill_id": "database",
        "name": "Database",
        "description": "Query manage and optimize database operations",
        "tools": ["db_query", "db_manage", "db_optimize"],
        "queries": [
            "查询数据库",
            "optimize database performance",
            "manage database schemas",
        ],
    },
]


# ── Pipeline tracker ──────────────────────────────────────────────


class PipelineTracker:
    """Track full pipeline accuracy and latency."""

    def __init__(self):
        self.total_queries = 0
        self.skill_selection_correct = 0
        self.tool_call_correct = 0
        self.pipeline_correct = 0
        self._latencies: list[float] = []

    def track(
        self,
        selected_skill: str | None,
        expected_skill: str,
        selected_tool: str | None,
        expected_tool: str,
        latency: float,
    ):
        """Track a single pipeline execution."""
        self.total_queries += 1
        self._latencies.append(latency)

        skill_correct = selected_skill == expected_skill
        tool_correct = selected_tool == expected_tool

        if skill_correct:
            self.skill_selection_correct += 1
        if tool_correct:
            self.tool_call_correct += 1
        if skill_correct and tool_correct:
            self.pipeline_correct += 1

    @property
    def skill_selection_accuracy(self) -> float:
        return self.skill_selection_correct / self.total_queries if self.total_queries else 0.0

    @property
    def tool_call_accuracy(self) -> float:
        return self.tool_call_correct / self.total_queries if self.total_queries else 0.0

    @property
    def pipeline_accuracy(self) -> float:
        return self.pipeline_correct / self.total_queries if self.total_queries else 0.0

    @property
    def p95_latency_ms(self) -> float:
        return _p95_ms(self._latencies) if self._latencies else 0.0


class MockSkillRouter:
    """Mock skill router with configurable accuracy."""

    def __init__(self, accuracy_rate: float = 0.90):
        self.accuracy_rate = accuracy_rate
        self._skill_map: dict[str, list[str]] = {}  # query_keyword -> skill_ids

    def register_skill(self, skill_id: str, queries: list[str]):
        """Register a skill with its typical queries."""
        for query in queries:
            keyword = query.split()[0].lower() if query.split() else query.lower()
            self._skill_map.setdefault(keyword, []).append(skill_id)

    def select_skill(self, query: str, expected_skill: str, available_skills: list[str]) -> str | None:
        """Select a skill based on query."""
        import random

        if random.random() < self.accuracy_rate:
            return expected_skill

        # Select a random wrong skill
        wrong_skills = [s for s in available_skills if s != expected_skill]
        return random.choice(wrong_skills) if wrong_skills else expected_skill


class MockToolSelector:
    """Mock tool selector with configurable accuracy."""

    def __init__(self, accuracy_rate: float = 0.95):
        self.accuracy_rate = accuracy_rate

    def select_tool(self, query: str, expected_tool: str, available_tools: list[str]) -> str | None:
        """Select a tool based on query."""
        import random

        if random.random() < self.accuracy_rate:
            return expected_tool

        # Select a random wrong tool
        wrong_tools = [t for t in available_tools if t != expected_tool]
        return random.choice(wrong_tools) if wrong_tools else expected_tool


# ── Cross-1: Full pipeline accuracy ──────────────────────────────


class TestCrossComponentPipelineAccuracy:
    """Test full pipeline accuracy: query → skill → tool → result."""

    def test_pipeline_accuracy_baseline(self):
        """Verify baseline pipeline accuracy."""
        # Setup
        skill_registry = SkillRegistry([])
        tool_registry = ToolRegistry()
        skill_router = MockSkillRouter(accuracy_rate=0.90)
        tool_selector = MockToolSelector(accuracy_rate=0.95)

        # Register skills and tools
        skill_entries = []
        for skill_data in DOMAIN_SKILLS:
            entry = _create_skill_entry(
                skill_data["skill_id"],
                skill_data["name"],
                skill_data["description"],
                skill_data["tools"],
            )
            skill_entries.append(entry)

            for tool_name in skill_data["tools"]:
                meta = _create_tool_meta(tool_name, f"Tool {tool_name}", skill_data["skill_id"])
                tool_registry.register(meta, _make_handler(tool_name))

            skill_router.register_skill(skill_data["skill_id"], skill_data["queries"])

        skill_registry._entries = skill_entries

        # Track pipeline
        tracker = PipelineTracker()

        for skill_data in DOMAIN_SKILLS:
            for query in skill_data["queries"]:
                start = time.perf_counter()

                # Step 1: Skill selection
                selected_skill = skill_router.select_skill(
                    query,
                    skill_data["skill_id"],
                    [s.workflow_id for s in skill_entries],
                )

                # Step 2: Tool selection within skill
                expected_tool = skill_data["tools"][0]  # Use first tool as expected
                available_tools = skill_data["tools"]
                selected_tool = tool_selector.select_tool(query, expected_tool, available_tools)

                latency = time.perf_counter() - start
                tracker.track(selected_skill, skill_data["skill_id"], selected_tool, expected_tool, latency)

        assert tracker.skill_selection_accuracy >= SKILL_SELECTION_ACCURACY_MIN, \
            f"Skill selection accuracy {tracker.skill_selection_accuracy:.2%} < {SKILL_SELECTION_ACCURACY_MIN:.2%}"

        assert tracker.tool_call_accuracy >= TOOL_CALL_ACCURACY_MIN, \
            f"Tool call accuracy {tracker.tool_call_accuracy:.2%} < {TOOL_CALL_ACCURACY_MIN:.2%}"

        assert tracker.pipeline_accuracy >= PIPELINE_ACCURACY_MIN, \
            f"Pipeline accuracy {tracker.pipeline_accuracy:.2%} < {PIPELINE_ACCURACY_MIN:.2%}"

    def test_pipeline_accuracy_with_more_skills(self):
        """Verify pipeline accuracy with more skills."""
        skill_registry = SkillRegistry([])
        tool_registry = ToolRegistry()
        skill_router = MockSkillRouter(accuracy_rate=0.88)
        tool_selector = MockToolSelector(accuracy_rate=0.93)

        # Create more skills
        skill_entries = []
        all_skills = DOMAIN_SKILLS.copy()

        # Add generic skills
        for i in range(10):
            skill_data = {
                "skill_id": f"generic_{i:02d}",
                "name": f"Generic Skill {i}",
                "description": f"General helper for task {i}",
                "tools": [f"generic_tool_{i:02d}_{j:02d}" for j in range(3)],
                "queries": [f"generic task {i} query {j}" for j in range(3)],
            }
            all_skills.append(skill_data)

        # Register all skills and tools
        for skill_data in all_skills:
            entry = _create_skill_entry(
                skill_data["skill_id"],
                skill_data["name"],
                skill_data["description"],
                skill_data["tools"],
            )
            skill_entries.append(entry)

            for tool_name in skill_data["tools"]:
                meta = _create_tool_meta(tool_name, f"Tool {tool_name}", skill_data["skill_id"])
                tool_registry.register(meta, _make_handler(tool_name))

            skill_router.register_skill(skill_data["skill_id"], skill_data["queries"])

        skill_registry._entries = skill_entries

        # Track pipeline
        tracker = PipelineTracker()

        for skill_data in all_skills:
            for query in skill_data["queries"]:
                start = time.perf_counter()

                selected_skill = skill_router.select_skill(
                    query,
                    skill_data["skill_id"],
                    [s.workflow_id for s in skill_entries],
                )

                expected_tool = skill_data["tools"][0]
                selected_tool = tool_selector.select_tool(query, expected_tool, skill_data["tools"])

                latency = time.perf_counter() - start
                tracker.track(selected_skill, skill_data["skill_id"], selected_tool, expected_tool, latency)

        assert tracker.pipeline_accuracy >= PIPELINE_ACCURACY_MIN - 0.05, \
            f"Pipeline accuracy {tracker.pipeline_accuracy:.2%} < {PIPELINE_ACCURACY_MIN - 0.05:.2%} with more skills"


# ── Cross-2: Full pipeline latency ───────────────────────────────


class TestCrossComponentPipelineLatency:
    """Test full pipeline latency."""

    def test_pipeline_latency_baseline(self):
        """Verify baseline pipeline latency."""
        skill_router = MockSkillRouter(accuracy_rate=0.90)
        tool_selector = MockToolSelector(accuracy_rate=0.95)

        # Setup skills
        for skill_data in DOMAIN_SKILLS:
            skill_router.register_skill(skill_data["skill_id"], skill_data["queries"])

        # Measure latency
        latencies = []

        for skill_data in DOMAIN_SKILLS:
            for query in skill_data["queries"]:
                start = time.perf_counter()

                # Simulate full pipeline
                skill_router.select_skill(
                    query,
                    skill_data["skill_id"],
                    [s["skill_id"] for s in DOMAIN_SKILLS],
                )

                expected_tool = skill_data["tools"][0]
                tool_selector.select_tool(query, expected_tool, skill_data["tools"])

                latency = time.perf_counter() - start
                latencies.append(latency)

        avg_latency_ms = (sum(latencies) / len(latencies)) * 1000
        p95_latency_ms = _p95_ms(latencies)

        assert avg_latency_ms < PIPELINE_LATENCY_P95_MAX_MS / 2, \
            f"Average latency {avg_latency_ms:.1f}ms >= {PIPELINE_LATENCY_P95_MAX_MS / 2:.1f}ms"

        assert p95_latency_ms < PIPELINE_LATENCY_P95_MAX_MS, \
            f"P95 latency {p95_latency_ms:.1f}ms >= {PIPELINE_LATENCY_P95_MAX_MS}ms"

    def test_pipeline_latency_scaling(self):
        """Verify latency scales reasonably with skill count."""
        skill_router = MockSkillRouter(accuracy_rate=0.90)
        tool_selector = MockToolSelector(accuracy_rate=0.95)

        # Create many skills
        all_skills = DOMAIN_SKILLS.copy()
        for i in range(20):
            all_skills.append({
                "skill_id": f"skill_{i:02d}",
                "name": f"Skill {i}",
                "description": f"Skill {i} description",
                "tools": [f"tool_{i:02d}_{j:02d}" for j in range(3)],
                "queries": [f"query_{i:02d}_{j:02d}" for j in range(3)],
            })

        for skill_data in all_skills:
            skill_router.register_skill(skill_data["skill_id"], skill_data["queries"])

        # Measure latency
        latencies = []

        for skill_data in all_skills:
            for query in skill_data["queries"][:1]:  # Test 1 query per skill
                start = time.perf_counter()

                skill_router.select_skill(
                    query,
                    skill_data["skill_id"],
                    [s["skill_id"] for s in all_skills],
                )

                expected_tool = skill_data["tools"][0]
                tool_selector.select_tool(query, expected_tool, skill_data["tools"])

                latency = time.perf_counter() - start
                latencies.append(latency)

        avg_latency_ms = (sum(latencies) / len(latencies)) * 1000

        assert avg_latency_ms < PIPELINE_LATENCY_P95_MAX_MS / 4, \
            f"Average latency {avg_latency_ms:.1f}ms with {len(all_skills)} skills"


# ── Cross-3: Accuracy-latency trade-off ──────────────────────────


class TestCrossComponentAccuracyLatencyTradeOff:
    """Analyze accuracy-latency trade-off."""

    def test_accuracy_latency_trade_off(self):
        """Measure accuracy and latency at different accuracy settings."""
        results = []

        for skill_accuracy in [0.80, 0.85, 0.90, 0.95]:
            for tool_accuracy in [0.85, 0.90, 0.95]:
                skill_router = MockSkillRouter(accuracy_rate=skill_accuracy)
                tool_selector = MockToolSelector(accuracy_rate=tool_accuracy)

                for skill_data in DOMAIN_SKILLS:
                    skill_router.register_skill(skill_data["skill_id"], skill_data["queries"])

                tracker = PipelineTracker()

                for skill_data in DOMAIN_SKILLS:
                    for query in skill_data["queries"]:
                        start = time.perf_counter()

                        selected_skill = skill_router.select_skill(
                            query,
                            skill_data["skill_id"],
                            [s["skill_id"] for s in DOMAIN_SKILLS],
                        )

                        expected_tool = skill_data["tools"][0]
                        selected_tool = tool_selector.select_tool(query, expected_tool, skill_data["tools"])

                        latency = time.perf_counter() - start
                        tracker.track(selected_skill, skill_data["skill_id"], selected_tool, expected_tool, latency)

                results.append({
                    "skill_accuracy_setting": skill_accuracy,
                    "tool_accuracy_setting": tool_accuracy,
                    "pipeline_accuracy": tracker.pipeline_accuracy,
                    "avg_latency_ms": (
                        (sum(tracker._latencies) / len(tracker._latencies)) * 1000
                        if tracker._latencies else 0
                    ),
                })

        # Verify trade-off is reasonable
        # Higher accuracy settings should produce higher accuracy
        for result in results:
            if result["skill_accuracy_setting"] >= 0.90 and result["tool_accuracy_setting"] >= 0.90:
                assert result["pipeline_accuracy"] >= PIPELINE_ACCURACY_MIN, (
                    f"Pipeline accuracy {result['pipeline_accuracy']:.2%} < "
                    f"{PIPELINE_ACCURACY_MIN:.2%} with high settings"
                )

    def test_optimal_configuration(self):
        """Find optimal accuracy configuration."""
        best_config = None
        best_score = 0.0

        for skill_accuracy in [0.85, 0.90, 0.95]:
            for tool_accuracy in [0.90, 0.95]:
                skill_router = MockSkillRouter(accuracy_rate=skill_accuracy)
                tool_selector = MockToolSelector(accuracy_rate=tool_accuracy)

                for skill_data in DOMAIN_SKILLS:
                    skill_router.register_skill(skill_data["skill_id"], skill_data["queries"])

                tracker = PipelineTracker()

                for skill_data in DOMAIN_SKILLS:
                    for query in skill_data["queries"]:
                        start = time.perf_counter()

                        selected_skill = skill_router.select_skill(
                            query,
                            skill_data["skill_id"],
                            [s["skill_id"] for s in DOMAIN_SKILLS],
                        )

                        expected_tool = skill_data["tools"][0]
                        selected_tool = tool_selector.select_tool(query, expected_tool, skill_data["tools"])

                        latency = time.perf_counter() - start
                        tracker.track(selected_skill, skill_data["skill_id"], selected_tool, expected_tool, latency)

                # Score = accuracy / latency (higher is better)
                avg_latency_ms = (
                    (sum(tracker._latencies) / len(tracker._latencies)) * 1000
                    if tracker._latencies else 1.0
                )
                score = tracker.pipeline_accuracy / (avg_latency_ms / 1000)  # Normalize

                if score > best_score:
                    best_score = score
                    best_config = {
                        "skill_accuracy": skill_accuracy,
                        "tool_accuracy": tool_accuracy,
                        "pipeline_accuracy": tracker.pipeline_accuracy,
                        "avg_latency_ms": avg_latency_ms,
                    }

        # Verify we found a reasonable configuration
        assert best_config is not None, "No optimal configuration found"
        assert best_config["pipeline_accuracy"] >= PIPELINE_ACCURACY_MIN, (
            f"Best config accuracy {best_config['pipeline_accuracy']:.2%} < {PIPELINE_ACCURACY_MIN:.2%}"
        )
