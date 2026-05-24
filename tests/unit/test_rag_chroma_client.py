from types import SimpleNamespace
from unittest.mock import MagicMock

import agentnexus.rag.chroma_client as chroma_client
from agentnexus.rag.models import ChunkRecord, SourceDocument


class TestChromaClient:
    def test_insert_documents_passes_metadata_and_ids(self, monkeypatch):
        mock_collection = MagicMock()
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [[0.1, 0.2], [0.3, 0.4]]

        monkeypatch.setattr(
            chroma_client,
            "get_collection",
            lambda name=None, namespace=None, metadata=None: mock_collection,
        )
        monkeypatch.setattr(chroma_client, "get_embedding_model", lambda: mock_model)

        ids = chroma_client.insert_documents(
            ["alpha", "beta"],
            metadatas=[{"source": "a"}, {"source": "b"}],
            ids=["doc-1", "doc-2"],
            namespace="support",
        )

        assert ids == ["doc-1", "doc-2"]
        mock_collection.add.assert_called_once_with(
            ids=["doc-1", "doc-2"],
            embeddings=[[0.1, 0.2], [0.3, 0.4]],
            documents=["alpha", "beta"],
            metadatas=[{"source": "a"}, {"source": "b"}],
        )

    def test_collection_name_uses_prefix_for_namespace(self, temp_agentnexus_home):
        collection_name = chroma_client.resolve_collection_name(namespace="support")

        assert collection_name == "kb_support"

    def test_resolve_embedding_device_prefers_cuda(self, monkeypatch):
        fake_torch = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: True),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
        )
        monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)

        assert chroma_client._resolve_embedding_device() == "cuda"

    def test_resolve_embedding_device_falls_back_to_cpu(self, monkeypatch):
        fake_torch = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
        )
        monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)

        assert chroma_client._resolve_embedding_device() == "cpu"

    def test_reset_client_preserves_embedding_model_by_default(self, monkeypatch):
        sentinel_model = object()
        monkeypatch.setattr(chroma_client, "_model", sentinel_model)
        monkeypatch.setattr(chroma_client, "_model_name", "test-model")
        monkeypatch.setattr(chroma_client, "_model_device", "cpu")

        chroma_client._reset_chroma_client()

        assert chroma_client._model is sentinel_model
        assert chroma_client._model_name == "test-model"
        assert chroma_client._model_device == "cpu"

    def test_reset_client_can_clear_embedding_model(self, monkeypatch):
        monkeypatch.setattr(chroma_client, "_model", object())
        monkeypatch.setattr(chroma_client, "_model_name", "test-model")
        monkeypatch.setattr(chroma_client, "_model_device", "cpu")

        chroma_client._reset_chroma_client(reset_model=True)

        assert chroma_client._model is None
        assert chroma_client._model_name is None
        assert chroma_client._model_device is None

    def test_get_embedding_model_reloads_when_device_changes(self, monkeypatch):
        calls = []

        class FakeSentenceTransformer:
            def __init__(self, model_name, device=None):
                calls.append((model_name, device))

        monkeypatch.setattr(chroma_client, "_model", None)
        monkeypatch.setattr(chroma_client, "_model_name", None)
        monkeypatch.setattr(chroma_client, "_model_device", None)
        monkeypatch.setattr(chroma_client, "_configure_embedding_runtime", lambda device: None)
        monkeypatch.setattr(chroma_client, "_resolve_embedding_device", lambda: next(devices))
        monkeypatch.setattr(chroma_client, "get_settings", lambda: SimpleNamespace(embedding_model="test-model"))
        monkeypatch.setitem(__import__("sys").modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=FakeSentenceTransformer))

        devices = iter(["cpu", "cuda"])
        first = chroma_client.get_embedding_model()
        second = chroma_client.get_embedding_model()

        assert first is not second
        assert calls == [("test-model", "cpu"), ("test-model", "cuda")]

    def test_get_embedding_model_reuses_cache_when_device_unchanged(self, monkeypatch):
        calls = []

        class FakeSentenceTransformer:
            def __init__(self, model_name, device=None):
                self.model_name = model_name
                self.device = device
                calls.append((model_name, device))

        monkeypatch.setattr(chroma_client, "_model", None)
        monkeypatch.setattr(chroma_client, "_model_name", None)
        monkeypatch.setattr(chroma_client, "_model_device", None)
        monkeypatch.setattr(chroma_client, "_configure_embedding_runtime", lambda device: None)
        monkeypatch.setattr(chroma_client, "_resolve_embedding_device", lambda: "cpu")
        monkeypatch.setattr(chroma_client, "get_settings", lambda: SimpleNamespace(embedding_model="test-model"))
        monkeypatch.setitem(__import__("sys").modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=FakeSentenceTransformer))

        first = chroma_client.get_embedding_model()
        second = chroma_client.get_embedding_model()

        assert first is second
        assert calls == [("test-model", "cpu")]

    def test_embed_texts_uses_tuned_encode_options(self, monkeypatch):
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [[0.1, 0.2]]
        monkeypatch.setattr(chroma_client, "get_embedding_model", lambda: mock_model)

        result = chroma_client._embed_texts(["alpha"])

        assert result == [[0.1, 0.2]]
        mock_model.encode.assert_called_once_with(
            ["alpha"],
            normalize_embeddings=True,
            batch_size=1024,
            show_progress_bar=False,
        )

    def test_chunk_metadata_is_normalized_for_chroma(self):
        document = SourceDocument.create(
            source_uri="docs/guide.md",
            raw_text="# Guide\n\nBody",
            kb_id="kb_default",
            metadata={"format": "markdown"},
        )
        chunk = ChunkRecord.create(
            document,
            chunk_index=0,
            raw_text="Body",
            indexed_text="Guide\n\nBody",
            metadata={
                "format": "markdown",
                "heading_path": ["Guide", "Install"],
                "section_index": 2,
                "page_number": 5,
            },
        )

        metadata = chroma_client.chunk_metadata_to_chroma(chunk)

        assert metadata["heading_path_text"] == "Guide / Install"
        assert metadata["heading_depth"] == 2
        assert metadata["section_index"] == 2
        assert metadata["page_number"] == 5
        assert "heading_path" not in metadata
