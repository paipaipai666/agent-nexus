"""Model delivery and legacy vector migration through real catalog/storage APIs."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentnexus.rag import kb_index, model_assets
from agentnexus.rag.models import ChunkRecord, KnowledgeBaseRecord, SourceDocument
from agentnexus.rag.store import get_knowledge_base_catalog
from agentnexus.server.error_handlers import register_error_handlers
from agentnexus.server.routes.knowledge import router
from agentnexus.storage.chroma import get_collection, reset_storage_client


class Embedder:
    def get_sentence_embedding_dimension(self):
        return 512

    def encode(self, texts, **_):
        vector = [1.0] + [0.0] * 511
        return vector if isinstance(texts, str) else [vector[:] for _ in texts]


@pytest.fixture
def delivery(monkeypatch):
    from agentnexus.core.config import get_settings
    from agentnexus.rag import retriever

    settings = get_settings()
    settings.enable_query_rewrite = False
    settings.enable_multi_query = False
    settings.wiki_enabled = False
    monkeypatch.setattr(model_assets, "_statuses", {})
    monkeypatch.setattr(model_assets, "_loaded_embedding_model", None)
    monkeypatch.setattr(model_assets, "_loaded_embedding_identity", None)
    monkeypatch.setattr(model_assets, "_reranker_thread", None)
    monkeypatch.setattr(retriever, "_retriever", None)
    retriever.reset_reranker_cache()
    reset_storage_client()
    yield get_knowledge_base_catalog()
    reset_storage_client()


def client():
    app = FastAPI()
    app.include_router(router, prefix="/api/kb")
    register_error_handlers(app)
    return TestClient(app)


def wait_run(api, run_id):
    for _ in range(100):
        response = api.get(f"/api/kb/documents/runs/{run_id}")
        if response.status_code == 404:
            time.sleep(0.02)
            continue
        assert response.status_code == 200
        run = response.json()
        if run["status"] in {"failed", "completed"}:
            return run
        time.sleep(0.02)
    pytest.fail("ingestion did not finish")


def test_missing_offline_model_fails_import_without_hash_vectors(delivery, monkeypatch):
    monkeypatch.setattr(model_assets, "cached_snapshot", lambda _: None)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    with client() as api:
        response = api.post("/api/kb/documents", files={"file": ("source.md", b"Knowledge delivery fixture")})
        assert response.status_code == 200
        run = wait_run(api, response.json()["run_id"])
        assert run["status"] == "failed"
        assert "HF_HUB_OFFLINE" in run["error_message"]
        assert api.get("/api/kb/documents").json() == {"documents": [], "total_chunks": 0}
        assert api.get("/api/kb/models").json()["models"]["embedding"]["state"] == "failed"
    assert get_collection(namespace="default").count() == 0


def test_explicit_download_retries_and_then_reuses_verified_cache(delivery, monkeypatch, tmp_path):
    from agentnexus.rag import embeddings

    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    snapshot_dir = tmp_path / "7999e1d3359715c523056ef9478215996d62a620"
    monkeypatch.setattr(model_assets, "_download_directory", lambda _: snapshot_dir)
    downloaded = False
    downloads = []

    def download(name, **options):
        nonlocal downloaded
        if options["local_files_only"]:
            if not downloaded:
                raise FileNotFoundError("no local snapshot")
            return str(snapshot_dir)
        downloads.append(options)
        assert options["local_dir"] == str(snapshot_dir)
        if len(downloads) == 1:
            raise OSError("connection interrupted")
        for filename in options["allow_patterns"]:
            path = snapshot_dir / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture", encoding="utf-8")
        downloaded = True
        return str(snapshot_dir)

    monkeypatch.setattr("huggingface_hub.snapshot_download", download)
    monkeypatch.setattr(embeddings, "get_embedding_model", lambda: Embedder())
    assert model_assets.status("embedding").state == "missing"
    assert downloads == []
    with pytest.raises(model_assets.ModelUnavailable):
        model_assets.prepare_embedding()
    assert model_assets.status("embedding").state == "failed"
    model, identity = model_assets.prepare_embedding()
    assert identity == "BAAI/bge-small-zh-v1.5@7999e1d3359715c523056ef9478215996d62a620"
    assert model.encode("fixture") == [1.0] + [0.0] * 511
    assert model_assets.status("embedding").state == "ready"
    second, second_identity = model_assets.prepare_embedding()
    assert second is model and second_identity == identity
    assert len(downloads) == 2
    assert downloads[1]["revision"] == "7999e1d3359715c523056ef9478215996d62a620"
    assert "model.safetensors" in downloads[1]["allow_patterns"]
    assert "pytorch_model.bin" not in downloads[1]["allow_patterns"]


def seed_legacy(catalog):
    catalog.upsert_knowledge_base(KnowledgeBaseRecord(
        kb_id="kb_default", namespace="default", display_name="default", collection_name="kb_default",
    ))
    document = SourceDocument.create("legacy.md", "Legacy knowledge fixture", kb_id="kb_default")
    chunk = ChunkRecord.create(document, 0, "Legacy knowledge fixture")
    catalog.upsert_document(document)
    catalog.upsert_chunks([chunk])
    get_collection(name="kb_default").upsert(
        ids=[chunk.chunk_id], documents=[chunk.text], embeddings=[[0.0, 1.0] + [0.0] * 510],
    )
    return chunk


def test_legacy_vectors_rebuild_before_pointer_switch_and_survive_retry(delivery):
    chunk = seed_legacy(delivery)

    class BrokenEmbedder(Embedder):
        def encode(self, texts, **_):
            raise OSError("interrupted rebuild")

    with pytest.raises(OSError, match="interrupted rebuild"):
        kb_index.ensure_embedding_index("default", BrokenEmbedder(), "model@revision")
    assert delivery.get_knowledge_base("default").collection_name == "kb_default"
    assert delivery.get_knowledge_base("default").metadata == {}
    assert delivery.count_chunks_by_kb("kb_default") == 1

    active = kb_index.ensure_embedding_index("default", Embedder(), "model@revision")
    assert active != "kb_default"
    assert delivery.get_knowledge_base("default").metadata["embedding_identity"] == "model@revision"
    rows = get_collection(namespace="default").get(include=["embeddings", "documents"])
    assert rows["ids"] == [chunk.chunk_id]
    assert rows["documents"] == ["Legacy knowledge fixture"]
    assert rows["embeddings"][0].tolist() == [1.0] + [0.0] * 511
    assert get_collection(name="kb_default").get(include=["embeddings"])["embeddings"][0].tolist() == [0.0, 1.0] + [0.0] * 510
    assert kb_index.ensure_embedding_index("default", Embedder(), "model@revision") == active


def test_delete_waits_for_rebuild_and_removes_vectors_from_retired_generation(delivery):
    from agentnexus.rag.kb_service import delete_document_and_vectors

    chunk = seed_legacy(delivery)
    encoding = threading.Event()
    release = threading.Event()
    deleting = threading.Event()

    class SlowEmbedder(Embedder):
        def encode(self, texts, **options):
            encoding.set()
            assert release.wait(5)
            return super().encode(texts, **options)

    def delete():
        deleting.set()
        return delete_document_and_vectors("default", chunk.document_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        rebuild = pool.submit(kb_index.ensure_embedding_index, "default", SlowEmbedder(), "model@revision")
        assert encoding.wait(5)
        deletion = pool.submit(delete)
        try:
            assert deleting.wait(5)
            assert not deletion.done()
        finally:
            release.set()
        rebuild.result(timeout=5)
        assert deletion.result(timeout=5) == 1
    assert delivery.count_chunks_by_kb("kb_default") == 0
    assert get_collection(namespace="default").count() == 0
    assert get_collection(name="kb_default").count() == 0


def test_optional_reranker_download_starts_only_on_request_and_is_single_flight(delivery, monkeypatch, tmp_path):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    requested = threading.Event()
    release = threading.Event()
    downloads = []
    completed = False
    snapshot_dir = tmp_path / "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"

    def download(name, **options):
        nonlocal completed
        if options["local_files_only"]:
            if not completed:
                raise FileNotFoundError("missing snapshot")
            return str(snapshot_dir)
        downloads.append(name)
        requested.set()
        assert release.wait(5)
        for filename in options["allow_patterns"]:
            path = snapshot_dir / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture", encoding="utf-8")
        completed = True
        return str(snapshot_dir)

    monkeypatch.setattr("huggingface_hub.snapshot_download", download)
    monkeypatch.setattr("sentence_transformers.CrossEncoder", lambda *_args, **_options: object())
    assert model_assets.status("reranker").state == "missing"
    assert downloads == []
    model_assets.start_reranker_prepare()
    try:
        assert requested.wait(5)
        assert model_assets.start_reranker_prepare().state == "downloading"
        assert downloads == ["BAAI/bge-reranker-v2-m3"]
    finally:
        release.set()
        model_assets._reranker_thread.join(timeout=5)
    assert not model_assets._reranker_thread.is_alive()
    assert model_assets.status("reranker").state == "ready"


def test_api_search_reports_model_error_then_explicit_hybrid_ranking(delivery, monkeypatch):
    seed_legacy(delivery)
    monkeypatch.setattr(model_assets, "cached_snapshot", lambda _: None)
    with client() as api:
        response = api.post("/api/kb/search", json={"query": "Legacy knowledge"})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "model_not_ready"
        assert delivery.get_knowledge_base("default").collection_name == "kb_default"
        monkeypatch.setattr(model_assets, "prepare_embedding", lambda **_: (Embedder(), "model@revision"))
        response = api.post("/api/kb/search", json={"query": "Legacy knowledge"})
        assert response.status_code == 200
        assert response.json()["ranking_mode"] == "hybrid"
        assert response.json()["results"][0]["text"] == "Legacy knowledge fixture"


def test_failed_import_can_be_uploaded_again_without_changing_document_identity(delivery, monkeypatch):
    def unavailable(**_):
        raise model_assets.ModelUnavailable("test offline model")

    monkeypatch.setattr(model_assets, "prepare_embedding", unavailable)
    with client() as api:
        first = api.post("/api/kb/documents", files={"file": ("retry.md", b"Retry fixture")}).json()
        assert wait_run(api, first["run_id"])["status"] == "failed"
        monkeypatch.setattr(model_assets, "prepare_embedding", lambda **_: (Embedder(), "model@revision"))
        second = api.post("/api/kb/documents", files={"file": ("retry.md", b"Retry fixture")}).json()
        assert wait_run(api, second["run_id"])["status"] == "completed"
        documents = api.get("/api/kb/documents").json()
        assert len(documents["documents"]) == 1
        assert documents["total_chunks"] == 1
        assert api.get(f"/api/kb/documents/runs/{first['run_id']}").json()["status"] == "failed"


def test_search_refreshes_after_first_ingest_replacement_and_external_deletion(delivery, monkeypatch, tmp_path):
    from agentnexus.rag.kb_service import ingest_one_document
    from agentnexus.rag.retriever import _get_retriever
    from agentnexus.rag.store import KnowledgeBaseCatalog

    monkeypatch.setattr(model_assets, "prepare_embedding", lambda **_: (Embedder(), "model@revision"))
    monkeypatch.setattr(model_assets, "cached_snapshot", lambda _: None)
    source = tmp_path / "mutable.md"
    with client() as api:
        assert api.post("/api/kb/search", json={"query": "knowledge"}).json()["results"] == []
        for text in ("Original knowledge fixture", "Replacement knowledge fixture"):
            source.write_text(text, encoding="utf-8")
            _, run = ingest_one_document(str(source), namespace="default", enable_contextual=False)
            assert run.status == "completed"
            response = api.post("/api/kb/search", json={"query": "knowledge"})
            assert response.status_code == 200
            assert [result["text"] for result in response.json()["results"]] == [text]
            current = _get_retriever()
            assert _get_retriever() is current
        external = KnowledgeBaseCatalog(db_path=delivery._db_path)
        try:
            external.delete_document(delivery.list_documents()[0].document_id)
        finally:
            external.close()
        assert api.post("/api/kb/search", json={"query": "knowledge"}).json()["results"] == []
