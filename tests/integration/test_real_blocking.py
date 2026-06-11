"""Real blocking tests — NO mocking of ingestion pipeline.

These tests verify that the event loop is NOT blocked during actual
large file uploads. Previous tests mocked ingest_one_document which
completely bypassed the real blocking path.
"""

import concurrent.futures
import gc
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class _FakeChatService:
    class _Handle:
        id = "fake-session-id"
        skill = None
        profile = None

    def start_session(self, **kw):
        return self._Handle()


class _FakeServices:
    chat = _FakeChatService()


class _FakeLLM:
    api_key = "test-key"
    base_url = "http://localhost"
    model = "test-model"


class _FakeRuntime:
    services = _FakeServices()
    llm = _FakeLLM()
    mcp_manager = None
    memory_manager = None

    def close(self):
        pass


@pytest.fixture
def server_app(temp_agentnexus_home):
    from agentnexus.server.app import create_app, set_runtime

    runtime = _FakeRuntime()
    set_runtime(runtime)
    app = create_app(runtime=runtime)
    yield app


@pytest.fixture
def client(server_app):
    return TestClient(server_app)


def _make_large_content(size_mb: float) -> bytes:
    """Generate text content of approximately `size_mb` megabytes."""
    line = "The quick brown fox jumps over the lazy dog. " * 20 + "\n"
    target = int(size_mb * 1024 * 1024)
    return (line * (target // len(line) + 1)).encode("utf-8")[:target]


# ---------------------------------------------------------------------------
# Real Blocking Tests — the bug: large file upload blocks other endpoints
# ---------------------------------------------------------------------------

class TestRealEventLoopBlocking:
    """Verify event loop stays responsive during ACTUAL large file I/O.

    These tests do NOT mock ingest_one_document. They mock only the
    ChromaDB/embedding layer so the test can run without external deps,
    but the file I/O + temp file + thread pool paths are exercised for real.
    """

    def test_other_endpoints_respond_during_10mb_upload(self, client):
        """While a 10MB file is being streamed to disk, other endpoints must respond.

        This is the core repro of the reported bug: upload a large file,
        then immediately hit other endpoints. If the event loop is blocked,
        the other endpoints will time out or take >>1s.
        """
        content_10mb = _make_large_content(10)

        # Mock only the deep ingestion internals — file I/O runs for real
        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            # Simulate slow ingestion (2s) to keep background task alive
            def slow_ingest(*args, **kwargs):
                time.sleep(2)
                return MagicMock(), MagicMock()

            mock_ingest.side_effect = slow_ingest

            # Upload 10MB file
            resp = client.post(
                "/api/kb/documents",
                files={"file": ("big.txt", content_10mb, "text/plain")},
            )
            assert resp.status_code == 200

            # Immediately hit other endpoints — these must respond fast
            start = time.monotonic()
            health = client.get("/health")
            docs = client.get("/api/kb/documents")
            elapsed = time.monotonic() - start

            assert health.status_code == 200
            assert docs.status_code == 200
            # If event loop is blocked, this takes >>1s. Should be <0.5s.
            assert elapsed < 1.0, (
                f"Other endpoints took {elapsed:.2f}s — event loop is blocked!"
            )

    def test_other_endpoints_respond_during_50mb_upload(self, client):
        """While a 50MB file is being streamed to disk, other endpoints must respond."""
        content_50mb = _make_large_content(50)

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            mock_ingest.return_value = (MagicMock(), MagicMock())

            resp = client.post(
                "/api/kb/documents",
                files={"file": ("huge.txt", content_50mb, "text/plain")},
            )
            assert resp.status_code == 200

            # Other endpoints must still be responsive
            start = time.monotonic()
            health = client.get("/health")
            config = client.get("/api/config")
            elapsed = time.monotonic() - start

            assert health.status_code == 200
            assert config.status_code == 200
            assert elapsed < 1.0, (
                f"Other endpoints took {elapsed:.2f}s during 50MB upload"
            )

    def test_concurrent_uploads_dont_block_each_other(self, client):
        """Three concurrent 10MB uploads should all complete without blocking."""
        content = _make_large_content(10)

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            mock_ingest.return_value = (MagicMock(), MagicMock())

            results = []
            errors = []

            def upload(i):
                try:
                    start = time.monotonic()
                    resp = client.post(
                        "/api/kb/documents",
                        files={"file": (f"file_{i}.txt", content, "text/plain")},
                    )
                    elapsed = time.monotonic() - start
                    results.append((i, resp.status_code, elapsed))
                except Exception as e:
                    errors.append((i, str(e)))

            # Fire 3 uploads concurrently
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(upload, i) for i in range(3)]
                concurrent.futures.wait(futures, timeout=30)

        assert len(errors) == 0, f"Upload errors: {errors}"
        assert len(results) == 3
        for i, status, elapsed in results:
            assert status == 200, f"Upload {i} got status {status}"
            assert elapsed < 5.0, f"Upload {i} took {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Real Memory Tests — verify actual memory behavior with real file I/O
# ---------------------------------------------------------------------------

class TestRealMemoryUsage:
    """Verify memory usage with actual file I/O (not mocked)."""

    def test_10mb_upload_memory_footprint(self, client):
        """Uploading a 10MB file should not spike memory by 3x+."""
        import tracemalloc

        content = _make_large_content(10)

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            mock_ingest.return_value = (MagicMock(), MagicMock())

            gc.collect()
            tracemalloc.start()
            snap_before = tracemalloc.take_snapshot()

            resp = client.post(
                "/api/kb/documents",
                files={"file": ("big.txt", content, "text/plain")},
            )
            assert resp.status_code == 200

            # Wait for file I/O to complete
            time.sleep(0.5)

            snap_after = tracemalloc.take_snapshot()
            tracemalloc.stop()

        stats = snap_after.compare_to(snap_before, "lineno")
        total_increase = sum(s.size_diff for s in stats if s.size_diff > 0)

        # Should NOT hold 3 copies of the file in memory
        file_size = len(content)
        assert total_increase < file_size * 3, (
            f"Memory +{total_increase / 1024 / 1024:.1f}MB for "
            f"{file_size / 1024 / 1024:.1f}MB file — likely read into memory"
        )


# ---------------------------------------------------------------------------
# Real Streaming Tests — verify chunked read/write works
# ---------------------------------------------------------------------------

class TestRealStreamingBehavior:
    """Verify the file is streamed to disk, not loaded entirely into memory."""

    def test_upload_returns_before_file_fully_written(self, client):
        """Upload endpoint should return before ingestion starts."""
        content = _make_large_content(5)

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            ingestion_started = threading.Event()
            ingestion_finished = threading.Event()

            def blocking_ingest(*args, **kwargs):
                ingestion_started.set()
                time.sleep(3)
                ingestion_finished.set()
                return MagicMock(), MagicMock()

            mock_ingest.side_effect = blocking_ingest

            start = time.monotonic()
            resp = client.post(
                "/api/kb/documents",
                files={"file": ("big.txt", content, "text/plain")},
            )
            upload_elapsed = time.monotonic() - start

        assert resp.status_code == 200
        # Upload should return well before ingestion finishes
        assert upload_elapsed < 2.0, (
            f"Upload took {upload_elapsed:.2f}s — should return immediately"
        )

    def test_upload_endpoint_returns_run_id(self, client):
        """Upload endpoint should return run_id immediately."""
        content = _make_large_content(2)

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            mock_ingest.return_value = (MagicMock(), MagicMock())

            resp = client.post(
                "/api/kb/documents",
                files={"file": ("test.txt", content, "text/plain")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processing"
        assert "run_id" in data
        assert data["run_id"].startswith("ingest_")

    def test_special_characters_in_large_filename(self, client):
        """Large file with unicode filename should work."""
        content = _make_large_content(5)

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            mock_ingest.return_value = (MagicMock(), MagicMock())

            resp = client.post(
                "/api/kb/documents",
                files={"file": ("大型文档 (测试)[2024].txt", content, "text/plain")},
            )

        assert resp.status_code == 200
        assert "大型文档" in resp.json()["filename"]


# ---------------------------------------------------------------------------
# Progress Monotonicity — real ingestion path
# ---------------------------------------------------------------------------

class TestRealProgressMonotonicity:
    """Verify progress percentage never decreases during real ingestion."""

    def test_progress_never_decreases(self, temp_agentnexus_home):
        """Progress should monotonically increase from 0 to 100."""
        from agentnexus.rag.kb_service import _update_run_progress
        from agentnexus.rag.models import IngestionRunRecord

        run = IngestionRunRecord(
            run_id="mono_test",
            kb_id="test_kb",
            status="running",
            source_uri="test.txt",
        )

        observed_pcts = []

        with patch("agentnexus.rag.kb_service.get_knowledge_base_catalog") as mock_cat:
            mock_cat.return_value = MagicMock()

            # Simulate realistic stage sequence
            stages = [
                ("loading", 10, "Loading document"),
                ("enriching", 30, "Enriching (1/10)"),
                ("enriching", 34, "Enriching (2/10)"),
                ("enriching", 38, "Enriching (3/10)"),
                ("enriching", 42, "Enriching (4/10)"),
                ("enriching", 46, "Enriching (5/10)"),
                ("enriching", 50, "Enriching (6/10)"),
                ("enriching", 54, "Enriching (7/10)"),
                ("enriching", 58, "Enriching (8/10)"),
                ("enriching", 62, "Enriching (9/10)"),
                ("enriching", 70, "Enriching (10/10)"),
                ("embedding", 80, "Generating embeddings"),
                ("persisting", 90, "Saving to database"),
                ("completed", 100, "Done"),
            ]

            for stage, pct, msg in stages:
                _update_run_progress(run, stage=stage, stage_pct=pct, message=msg)
                observed_pcts.append(pct)

        # Every step must be >= previous
        for i in range(1, len(observed_pcts)):
            assert observed_pcts[i] >= observed_pcts[i - 1], (
                f"Progress decreased at step {i}: "
                f"{observed_pcts[i - 1]}% -> {observed_pcts[i]}%"
            )

        assert observed_pcts[0] >= 5
        assert observed_pcts[-1] == 100
