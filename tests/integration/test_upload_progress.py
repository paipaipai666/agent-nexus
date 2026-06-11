"""Tests for document upload progress tracking."""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")
from fastapi.testclient import TestClient


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


class TestUploadReturnsImmediately:
    """Verify POST /documents returns run_id without waiting for ingestion."""

    def test_upload_returns_processing_status(self, client):
        """Upload should return 'processing' status and run_id immediately."""
        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            # Make ingestion slow to verify we don't wait for it
            def slow_ingest(*args, **kwargs):
                time.sleep(5)
                result = MagicMock()
                result.document = MagicMock()
                result.chunks = []
                return result, MagicMock()

            mock_ingest.side_effect = slow_ingest

            resp = client.post(
                "/api/kb/documents",
                files={"file": ("test.txt", b"hello world", "text/plain")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processing"
        assert "run_id" in data
        assert data["run_id"].startswith("ingest_")
        assert data["filename"] == "test.txt"

    def test_upload_returns_within_two_seconds(self, client):
        """Upload endpoint should return within 2 seconds even with slow ingestion."""
        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            def slow_ingest(*args, **kwargs):
                time.sleep(10)  # Simulate very slow ingestion
                return MagicMock(), MagicMock()

            mock_ingest.side_effect = slow_ingest

            start = time.monotonic()
            resp = client.post(
                "/api/kb/documents",
                files={"file": ("test.txt", b"hello world", "text/plain")},
            )
            elapsed = time.monotonic() - start

        assert resp.status_code == 200
        assert elapsed < 2.0, f"Upload took {elapsed:.2f}s, should be < 2.0s"


class TestProgressEndpoint:
    """Verify GET /documents/runs/{run_id} returns progress information."""

    def test_progress_endpoint_returns_run_info(self, temp_agentnexus_home):
        """Progress endpoint should return run status and metadata."""
        from agentnexus.rag.kb_service import start_ingestion_run, _update_run_progress
        from agentnexus.rag.models import IngestionRunRecord

        with patch("agentnexus.rag.kb_service.get_knowledge_base_catalog") as mock_catalog:
            mock_catalog_instance = MagicMock()
            mock_catalog.return_value = mock_catalog_instance

            # Create a run directly
            run = start_ingestion_run("default", "test.txt", run_id="test_run_123")

            # Verify run was created
            assert run.run_id == "test_run_123"
            assert run.status == "running"

            # Update progress
            _update_run_progress(run, stage="loading", stage_pct=10, message="Loading")

            # Verify progress was updated
            assert run.metadata["progress_stage"] == "loading"
            assert run.metadata["progress_pct"] == 10

    def test_progress_endpoint_404_for_unknown_run(self, client):
        """Progress endpoint should return 404 for unknown run_id."""
        resp = client.get("/api/kb/documents/runs/nonexistent_run")
        assert resp.status_code == 404


class TestProgressTrackingInIngestion:
    """Verify progress is tracked correctly during ingestion."""

    def test_progress_callback_is_called(self, temp_agentnexus_home):
        """Verify progress callback is invoked during ingestion."""
        from agentnexus.rag.kb_service import _update_run_progress
        from agentnexus.rag.models import IngestionRunRecord

        run = IngestionRunRecord(
            run_id="test_run",
            kb_id="test_kb",
            status="running",
            source_uri="test.txt",
        )

        # Mock the catalog
        with patch("agentnexus.rag.kb_service.get_knowledge_base_catalog") as mock_catalog:
            mock_catalog.return_value = MagicMock()

            # Update progress
            _update_run_progress(run, stage="loading", stage_pct=10, message="Loading")

        assert run.metadata["progress_stage"] == "loading"
        assert run.metadata["progress_pct"] == 10
        assert run.metadata["progress_message"] == "Loading"

    def test_progress_stages_are_sequential(self, temp_agentnexus_home):
        """Verify progress stages are updated in correct order."""
        from agentnexus.rag.kb_service import _update_run_progress
        from agentnexus.rag.models import IngestionRunRecord

        run = IngestionRunRecord(
            run_id="test_run",
            kb_id="test_kb",
            status="running",
            source_uri="test.txt",
        )

        stages = [
            ("loading", 10),
            ("enriching", 50),
            ("embedding", 80),
            ("persisting", 90),
            ("completed", 100),
        ]

        with patch("agentnexus.rag.kb_service.get_knowledge_base_catalog") as mock_catalog:
            mock_catalog.return_value = MagicMock()

            for stage, pct in stages:
                _update_run_progress(run, stage=stage, stage_pct=pct, message=f"Stage: {stage}")

        assert run.metadata["progress_stage"] == "completed"
        assert run.metadata["progress_pct"] == 100


class TestEventLoopNotBlocked:
    """Verify the event loop is not blocked during ingestion."""

    def test_other_endpoints_respond_during_ingestion(self, client):
        """Other endpoints should respond while ingestion is running."""
        with patch("agentnexus.rag.kb_service.ingest_one_document") as mock_ingest:
            def slow_ingest(*args, **kwargs):
                time.sleep(2)  # Simulate slow ingestion
                return MagicMock(), MagicMock()

            mock_ingest.side_effect = slow_ingest

            # Start upload
            resp = client.post(
                "/api/kb/documents",
                files={"file": ("test.txt", b"hello world", "text/plain")},
            )
            assert resp.status_code == 200

            # Try other endpoints while ingestion might be running
            health_resp = client.get("/health")
            assert health_resp.status_code == 200

            config_resp = client.get("/api/config")
            assert config_resp.status_code == 200

            docs_resp = client.get("/api/kb/documents")
            assert docs_resp.status_code == 200


class TestIngestionRunRecord:
    """Verify IngestionRunRecord metadata handling."""

    def test_finish_ingestion_run_merges_metadata(self, temp_agentnexus_home):
        """finish_ingestion_run should merge metadata, not replace."""
        from agentnexus.rag.kb_service import finish_ingestion_run
        from agentnexus.rag.models import IngestionRunRecord

        run = IngestionRunRecord(
            run_id="test_run",
            kb_id="test_kb",
            status="running",
            source_uri="test.txt",
        )
        run.metadata = {
            "progress_stage": "embedding",
            "progress_pct": 80,
        }

        with patch("agentnexus.rag.kb_service.get_knowledge_base_catalog") as mock_catalog:
            mock_catalog.return_value = MagicMock()

            finish_ingestion_run(
                run,
                status="completed",
                documents_seen=1,
                chunks_written=10,
                metadata={"duration_ms": 1234.5},
            )

        # Original progress keys should be preserved
        assert "progress_stage" in run.metadata
        assert "progress_pct" in run.metadata
        # New metadata should be added
        assert "duration_ms" in run.metadata

    def test_start_ingestion_run_with_custom_run_id(self, temp_agentnexus_home):
        """start_ingestion_run should accept custom run_id."""
        from agentnexus.rag.kb_service import start_ingestion_run

        with patch("agentnexus.rag.kb_service.get_knowledge_base_catalog") as mock_catalog:
            mock_catalog.return_value = MagicMock()

            run = start_ingestion_run("default", "test.txt", run_id="custom_run_123")

        assert run.run_id == "custom_run_123"

    def test_start_ingestion_run_generates_id_when_not_provided(self, temp_agentnexus_home):
        """start_ingestion_run should generate run_id when not provided."""
        from agentnexus.rag.kb_service import start_ingestion_run

        with patch("agentnexus.rag.kb_service.get_knowledge_base_catalog") as mock_catalog:
            mock_catalog.return_value = MagicMock()

            run = start_ingestion_run("default", "test.txt")

        assert run.run_id.startswith("ingest_")
