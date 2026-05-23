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
