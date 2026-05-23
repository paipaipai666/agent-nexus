"""Performance test fixtures — latency simulation, large data generators."""

from __future__ import annotations

import os
import random
import tempfile
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

# ── Latency profiles ───────────────────────────────────────────

LATENCY_PROFILES: dict[str, dict[str, Any]] = {
    "fast": {
        "base_delay": 0.05,
        "streaming": False,
        "chunk_interval": 0.0,
        "fail_after": None,
        "jitter": 0.01,
    },
    "typical": {
        "base_delay": 0.8,
        "streaming": True,
        "chunk_interval": 0.02,
        "fail_after": None,
        "jitter": 0.05,
    },
    "slow": {
        "base_delay": 3.0,
        "streaming": True,
        "chunk_interval": 0.1,
        "fail_after": None,
        "jitter": 0.2,
    },
    "unstable": {
        "base_delay": 0.3,
        "streaming": False,
        "chunk_interval": 0.0,
        "fail_after": 2,
        "jitter": 0.02,
    },
}


class _CountingLLM:
    """Wrapper that counts calls and tracks latency."""

    def __init__(self, profile: dict[str, Any]):
        self.profile = profile
        self.call_count = 0
        self.last_latency = 0.0

    def think(self, *args, **kwargs):
        self.call_count += 1
        delay = self.profile["base_delay"]
        delay += random.uniform(0, self.profile["jitter"])

        fail_after = self.profile.get("fail_after")
        if fail_after is not None and self.call_count > fail_after:
            import openai
            raise openai.APITimeoutError("simulated timeout")

        start = time.perf_counter()
        time.sleep(delay)
        self.last_latency = time.perf_counter() - start
        return "mocked response"

    @property
    def capabilities(self):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.supports_thinking = True
        m.supports_tool_calling = True
        return m

    @property
    def last_truncated(self) -> bool:
        return False


@pytest.fixture
def mock_llm_latency(request: pytest.FixtureRequest) -> _CountingLLM:
    """Return a mock LLM with configurable latency profile.

    Usage::

        @pytest.mark.parametrize("mock_llm_latency", ["fast", "typical"], indirect=True)
        def test_something(mock_llm_latency):
            ...

    The default profile is "typical".
    """
    profile_name = getattr(request, "param", "typical")
    profile = LATENCY_PROFILES[profile_name]
    return _CountingLLM(profile)


# ── Large data fixtures ────────────────────────────────────────


@pytest.fixture
def perf_env() -> Generator[Path, None, None]:
    """Isolated AGENTNEXUS_HOME for perf tests."""
    import agentnexus.core.config as cfg

    old_home = os.environ.get("AGENTNEXUS_HOME")
    old_cache = cfg._settings_cache
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        os.environ["AGENTNEXUS_HOME"] = tmpdir
        cfg._settings_cache = None
        yield Path(tmpdir)
        cfg._settings_cache = old_cache
        if old_home:
            os.environ["AGENTNEXUS_HOME"] = old_home
        else:
            os.environ.pop("AGENTNEXUS_HOME", None)


@pytest.fixture
def chunk_records(request: pytest.FixtureRequest) -> list[Any]:
    """Generate N ChunkRecord-like dicts for perf tests."""
    count = getattr(request, "param", 100)
    from agentnexus.rag.models import ChunkRecord

    return [
        ChunkRecord(
            chunk_id=f"chunk_{i:06d}",
            kb_id="perf_kb",
            document_id="perf_doc",
            document_version="v1",
            chunk_index=i,
            section_index=0,
            text=f"This is chunk number {i} with some content for retrieval testing purposes.",
            indexed_text=f"chunk {i} content retrieval testing",
            sparse_text=f"chunk {i} sparse",
            metadata={"source": "perf", "index": i},
        )
        for i in range(count)
    ]


@pytest.fixture
def ltm_entries(request: pytest.FixtureRequest) -> list[dict[str, Any]]:
    """Generate N LTM entry dicts for perf tests."""
    count = getattr(request, "param", 50)
    categories = ["entity_fact", "preference", "task_result", "conversation_summary"]
    return [
        {
            "id": i,
            "content": f"Long term memory entry #{i} with some sample text that would be embedded and searched.",
            "category": random.choice(categories),
            "importance": round(random.uniform(0.1, 1.0), 2),
        }
        for i in range(count)
    ]


@pytest.fixture
def traces_dir(perf_env: Path) -> Path:
    """Create traces dir with 50 sample spans for agent perf tests."""
    import json
    import time

    d = perf_env / "traces"
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()

    with open(d / "perf.jsonl", "w", encoding="utf-8") as f:
        for i in range(50):
            span = {
                "trace_id": f"perf_trace_{i // 5}",
                "span_id": f"span_{i:04d}",
                "parent_span_id": "",
                "name": "llm" if i % 3 != 0 else "task",
                "start_time": now - (50 - i) * 0.5,
                "end_time": now - (50 - i) * 0.5 + 0.2,
                "latency_ms": 200.0,
                "metadata": {
                    "model": "deepseek-v4-flash",
                    "input_tokens": random.randint(100, 2000),
                    "output_tokens": random.randint(50, 1000),
                    "status": "ok",
                    "tool_calls": ["web_search"] if i % 4 == 0 else [],
                },
            }
            f.write(json.dumps(span, ensure_ascii=False) + "\n")
    return d


# ── Observability / Reranker helpers ─────────────────────────────


class MockCrossEncoder:
    """Mock CrossEncoder for reranker perf tests — simulates predict latency."""

    def __init__(self, model_name: str = ""):
        self.model_name = model_name

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        return [random.uniform(0.0, 1.0) for _ in pairs]


@pytest.fixture
def generate_spans(perf_env: Path):
    """Factory: returns a callable(count) that writes N spans to traces/perf.jsonl.

    Each trace (identified by trace_id) has 1 task span + 4 llm spans,
    for a realistic ratio that exercises compute_stats aggregation.
    """
    import json

    def _make(count: int) -> Path:
        d = perf_env / "traces"
        d.mkdir(parents=True, exist_ok=True)
        now = time.time()
        with open(d / "perf.jsonl", "w", encoding="utf-8") as f:
            for i in range(count):
                trace_idx = i // 5
                span_type = "task" if i % 5 == 0 else "llm"
                span = {
                    "trace_id": f"trace_{trace_idx:04d}",
                    "span_id": f"span_{i:06d}",
                    "parent_span_id": "",
                    "name": span_type,
                    "start_time": now - (count - i) * 0.5,
                    "end_time": now - (count - i) * 0.5 + 0.2,
                    "latency_ms": 200.0,
                    "input": {},
                    "output": {},
                    "metadata": {
                        "model": "deepseek-v4-flash",
                        "input_tokens": random.randint(100, 2000),
                        "output_tokens": random.randint(50, 1000),
                        "status": "ok",
                    },
                }
                f.write(json.dumps(span, ensure_ascii=False) + "\n")
        return d
    return _make
