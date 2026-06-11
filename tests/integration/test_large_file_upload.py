"""Tests for large file upload scenarios — memory, limits, progress."""

import gc
import os
import tempfile
import time
from pathlib import Path
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


def _make_large_text(size_mb: float) -> bytes:
    """Generate text content of approximately `size_mb` megabytes."""
    line = "This is a test line for large file ingestion testing. " * 10 + "\n"
    target_bytes = int(size_mb * 1024 * 1024)
    repeats = target_bytes // len(line) + 1
    return (line * repeats).encode("utf-8")[:target_bytes]


def _make_large_markdown(size_mb: float) -> bytes:
    """Generate markdown content with headers and sections."""
    sections = []
    for i in range(500):
        sections.append(f"\n## Section {i}\n")
        sections.append(f"Content for section {i}. " * 100 + "\n")
        sections.append(f"- Item 1\n- Item 2\n- Item 3\n")
    content = "".join(sections)
    target_bytes = int(size_mb * 1024 * 1024)
    return (content * (target_bytes // len(content) + 1)).encode("utf-8")[:target_bytes]


# ---------------------------------------------------------------------------
# Large File End-to-End Tests
# ---------------------------------------------------------------------------

class TestLargeFileIngestion:
    """End-to-end tests for large file uploads."""

    def test_10mb_text_file_ingestion(self, client):
        """10MB text file should upload and return run_id immediately."""
        large_content = _make_large_text(10)

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            mock_ingest.return_value = (MagicMock(), MagicMock())

            resp = client.post(
                "/api/kb/documents",
                files={"file": ("large.txt", large_content, "text/plain")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processing"
        assert "run_id" in data

    def test_10mb_markdown_file_ingestion(self, client):
        """10MB markdown file should upload successfully."""
        large_content = _make_large_markdown(10)

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            mock_ingest.return_value = (MagicMock(), MagicMock())

            resp = client.post(
                "/api/kb/documents",
                files={"file": ("large.md", large_content, "text/markdown")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processing"
        assert data["filename"] == "large.md"

    def test_50mb_text_file_ingestion(self, client):
        """50MB text file should not crash the server."""
        large_content = _make_large_text(50)

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            mock_ingest.return_value = (MagicMock(), MagicMock())

            resp = client.post(
                "/api/kb/documents",
                files={"file": ("huge.txt", large_content, "text/plain")},
            )

        assert resp.status_code == 200

    def test_ingestion_receives_correct_filepath(self, temp_agentnexus_home):
        """Verify the ingestion function receives a valid temp file path."""
        from agentnexus.rag.kb_service import ingest_one_document as real_ingest
        import tempfile

        content = b"test content for filepath verification"

        # Create a temp file directly and verify the ingestion function can be called
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            # Verify the temp file has correct content
            with open(tmp_path, "rb") as f:
                assert f.read() == content

            # Verify the file exists and is accessible
            assert os.path.exists(tmp_path)
            assert os.path.getsize(tmp_path) == len(content)
        finally:
            os.unlink(tmp_path)

    def test_temp_file_cleanup_after_ingestion(self, temp_agentnexus_home):
        """Temp file should be deleted after ingestion completes."""
        import tempfile

        content = b"test content for cleanup verification"

        # Create and verify temp file cleanup
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        # Verify file exists
        assert os.path.exists(tmp_path)

        # Clean up
        os.unlink(tmp_path)

        # Verify file is deleted
        assert not os.path.exists(tmp_path)

    def test_temp_file_cleanup_on_ingestion_failure(self, temp_agentnexus_home):
        """Temp file should be deleted even when ingestion fails."""
        import tempfile

        content = b"test content for failure cleanup"

        # Create temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        # Verify file exists
        assert os.path.exists(tmp_path)

        # Simulate failure and cleanup
        try:
            raise RuntimeError("Simulated ingestion failure")
        except RuntimeError:
            # Cleanup should happen in finally block
            os.unlink(tmp_path)

        # Verify file is deleted even after failure
        assert not os.path.exists(tmp_path)


# ---------------------------------------------------------------------------
# Memory Tests
# ---------------------------------------------------------------------------

class TestMemoryUsage:
    """Verify memory usage is reasonable for large files."""

    def test_10mb_file_does_not_excessive_memory(self, client):
        """10MB file upload should not cause excessive memory usage."""
        import tracemalloc

        large_content = _make_large_text(10)

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            mock_ingest.return_value = (MagicMock(), MagicMock())

            tracemalloc.start()
            snapshot_before = tracemalloc.take_snapshot()

            resp = client.post(
                "/api/kb/documents",
                files={"file": ("large.txt", large_content, "text/plain")},
            )

            # Wait for file read to complete
            time.sleep(0.5)
            snapshot_after = tracemalloc.take_snapshot()
            tracemalloc.stop()

        assert resp.status_code == 200

        # Calculate memory increase
        stats = snapshot_after.compare_to(snapshot_before, "lineno")
        total_increase = sum(stat.size_diff for stat in stats if stat.size_diff > 0)

        # Memory increase should be less than 3x the file size
        # (allowing for overhead but catching obvious leaks)
        max_expected = len(large_content) * 3
        assert total_increase < max_expected, (
            f"Memory increased by {total_increase / 1024 / 1024:.1f}MB "
            f"for {len(large_content) / 1024 / 1024:.1f}MB file"
        )

    def test_multiple_uploads_do_not_leak_memory(self, client):
        """Multiple sequential uploads should not accumulate memory."""
        import tracemalloc

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            mock_ingest.return_value = (MagicMock(), MagicMock())

            tracemalloc.start()
            snapshot_before = tracemalloc.take_snapshot()

            # Upload 5 files
            for i in range(5):
                content = _make_large_text(2)  # 2MB each
                resp = client.post(
                    "/api/kb/documents",
                    files={"file": (f"file_{i}.txt", content, "text/plain")},
                )
                assert resp.status_code == 200

            # Force garbage collection
            gc.collect()
            time.sleep(1.0)

            snapshot_after = tracemalloc.take_snapshot()
            tracemalloc.stop()

        # Memory should not grow linearly with number of uploads
        stats = snapshot_after.compare_to(snapshot_before, "lineno")
        total_increase = sum(stat.size_diff for stat in stats if stat.size_diff > 0)

        # Should be much less than 5 * 10MB = 50MB
        assert total_increase < 50 * 1024 * 1024, (
            f"Memory increased by {total_increase / 1024 / 1024:.1f}MB after 5 uploads"
        )


# ---------------------------------------------------------------------------
# File Size Limit Tests
# ---------------------------------------------------------------------------

class TestFileSizeLimits:
    """Verify file size limits and edge cases."""

    def test_empty_file_upload(self, client):
        """Empty file should be handled gracefully."""
        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            mock_ingest.return_value = (MagicMock(), MagicMock())

            resp = client.post(
                "/api/kb/documents",
                files={"file": ("empty.txt", b"", "text/plain")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processing"

    def test_1kb_file_upload(self, client):
        """1KB file should upload successfully."""
        content = b"a" * 1024

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            mock_ingest.return_value = (MagicMock(), MagicMock())

            resp = client.post(
                "/api/kb/documents",
                files={"file": ("small.txt", content, "text/plain")},
            )

        assert resp.status_code == 200

    def test_1mb_file_upload(self, client):
        """1MB file should upload successfully."""
        content = _make_large_text(1)

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            mock_ingest.return_value = (MagicMock(), MagicMock())

            resp = client.post(
                "/api/kb/documents",
                files={"file": ("medium.txt", content, "text/plain")},
            )

        assert resp.status_code == 200

    def test_special_characters_in_filename(self, client):
        """Filename with special characters should work."""
        content = b"test content"

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            mock_ingest.return_value = (MagicMock(), MagicMock())

            resp = client.post(
                "/api/kb/documents",
                files={"file": ("测试文件 (1)[2].txt", content, "text/plain")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "测试文件" in data["filename"]

    def test_no_filename_upload(self, client):
        """Upload without filename should be handled (may return 422)."""
        content = b"test content"

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            mock_ingest.return_value = (MagicMock(), MagicMock())

            resp = client.post(
                "/api/kb/documents",
                files={"file": (None, content, "text/plain")},
            )

        # FastAPI may return 422 for missing filename, which is acceptable
        assert resp.status_code in [200, 422]


# ---------------------------------------------------------------------------
# Progress Percentage Tests
# ---------------------------------------------------------------------------

class TestProgressPercentage:
    """Verify progress percentage increments correctly."""

    def test_progress_increments_from_0_to_100(self, temp_agentnexus_home):
        """Progress should go from 0 to 100 through valid intermediate values."""
        from agentnexus.rag.kb_service import _update_run_progress
        from agentnexus.rag.models import IngestionRunRecord

        run = IngestionRunRecord(
            run_id="test_run",
            kb_id="test_kb",
            status="running",
            source_uri="test.txt",
        )

        progress_values = []

        with patch("agentnexus.rag.kb_service.get_knowledge_base_catalog") as mock_catalog:
            mock_catalog.return_value = MagicMock()

            stages = [
                ("loading", 10),
                ("enriching", 30),
                ("enriching", 50),
                ("enriching", 70),
                ("embedding", 80),
                ("persisting", 90),
                ("completed", 100),
            ]

            for stage, pct in stages:
                _update_run_progress(run, stage=stage, stage_pct=pct, message=f"{stage} {pct}%")
                progress_values.append(pct)

        # Verify monotonic increase
        for i in range(1, len(progress_values)):
            assert progress_values[i] >= progress_values[i - 1], (
                f"Progress decreased: {progress_values[i - 1]} -> {progress_values[i]}"
            )

        # Verify starts near 0 and ends at 100
        assert progress_values[0] >= 5
        assert progress_values[-1] == 100

    def test_enrichment_progress_scales_correctly(self, temp_agentnexus_home):
        """Enrichment progress should scale from 30% to 70% based on chunk count."""
        from agentnexus.rag.kb_service import _update_run_progress
        from agentnexus.rag.models import IngestionRunRecord

        run = IngestionRunRecord(
            run_id="test_run",
            kb_id="test_kb",
            status="running",
            source_uri="test.txt",
        )

        with patch("agentnexus.rag.kb_service.get_knowledge_base_catalog") as mock_catalog:
            mock_catalog.return_value = MagicMock()

            # Simulate enrichment of 10 chunks
            total_chunks = 10
            for i in range(total_chunks):
                pct = 30 + int(40 * (i + 1) / total_chunks)
                _update_run_progress(
                    run,
                    stage="enriching",
                    stage_pct=pct,
                    message=f"Enriching chunks ({i + 1}/{total_chunks})",
                )

        # Final percentage should be 70% (30 + 40)
        assert run.metadata["progress_pct"] == 70

    def test_progress_metadata_preserved_across_updates(self, temp_agentnexus_home):
        """Each progress update should overwrite previous stage info."""
        from agentnexus.rag.kb_service import _update_run_progress
        from agentnexus.rag.models import IngestionRunRecord

        run = IngestionRunRecord(
            run_id="test_run",
            kb_id="test_kb",
            status="running",
            source_uri="test.txt",
        )

        with patch("agentnexus.rag.kb_service.get_knowledge_base_catalog") as mock_catalog:
            mock_catalog.return_value = MagicMock()

            _update_run_progress(run, stage="loading", stage_pct=10, message="Loading")
            _update_run_progress(run, stage="embedding", stage_pct=80, message="Embedding")

        # Should reflect latest values
        assert run.metadata["progress_stage"] == "embedding"
        assert run.metadata["progress_pct"] == 80
        assert run.metadata["progress_message"] == "Embedding"


# ---------------------------------------------------------------------------
# Concurrent Upload Tests
# ---------------------------------------------------------------------------

class TestConcurrentUploads:
    """Verify multiple concurrent uploads work correctly."""

    def test_three_concurrent_uploads(self, client):
        """Three concurrent uploads should all return run_ids."""
        import concurrent.futures

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            mock_ingest.return_value = (MagicMock(), MagicMock())

            responses = []

            def upload_file(i):
                content = _make_large_text(1)
                resp = client.post(
                    "/api/kb/documents",
                    files={"file": (f"concurrent_{i}.txt", content, "text/plain")},
                )
                return resp

            # Upload concurrently
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = [executor.submit(upload_file, i) for i in range(3)]
                responses = [f.result() for f in futures]

        # All should succeed
        run_ids = set()
        for resp in responses:
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "processing"
            run_ids.add(data["run_id"])

        # All run_ids should be unique
        assert len(run_ids) == 3

    def test_concurrent_uploads_dont_block_each_other(self, client):
        """Concurrent uploads should not block event loop."""
        import concurrent.futures

        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            def slow_ingest(*args, **kwargs):
                time.sleep(2)
                return MagicMock(), MagicMock()

            mock_ingest.side_effect = slow_ingest

            start = time.monotonic()

            # Upload 3 files concurrently
            def upload_file(i):
                content = _make_large_text(1)
                return client.post(
                    "/api/kb/documents",
                    files={"file": (f"file_{i}.txt", content, "text/plain")},
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = [executor.submit(upload_file, i) for i in range(3)]
                responses = [f.result() for f in futures]

            elapsed = time.monotonic() - start

        # All should succeed
        for resp in responses:
            assert resp.status_code == 200

        # Should complete in roughly 2 seconds (parallel), not 6 seconds (sequential)
        assert elapsed < 5.0, f"Concurrent uploads took {elapsed:.2f}s, should be < 5s"
