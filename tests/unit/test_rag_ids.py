from agentnexus.rag.ids import make_chunk_id, make_document_version, make_source_id


class TestRagIds:
    def test_source_id_is_deterministic(self):
        source_id_1 = make_source_id("docs/Guide.md")
        source_id_2 = make_source_id("docs\\Guide.md")

        assert source_id_1 == source_id_2
        assert source_id_1.startswith("src_")

    def test_document_version_changes_with_content(self):
        source_id = make_source_id("docs/guide.md")

        version_1 = make_document_version(source_id, "hello")
        version_2 = make_document_version(source_id, "hello")
        version_3 = make_document_version(source_id, "hello world")

        assert version_1 == version_2
        assert version_1 != version_3
        assert version_1.startswith("doc_")

    def test_chunk_id_depends_on_version_and_index(self):
        source_id = make_source_id("docs/guide.md")
        document_version = make_document_version(source_id, "body")

        chunk_id_1 = make_chunk_id(document_version, 0, "chunk text")
        chunk_id_2 = make_chunk_id(document_version, 0, "chunk text")
        chunk_id_3 = make_chunk_id(document_version, 1, "chunk text")

        assert chunk_id_1 == chunk_id_2
        assert chunk_id_1 != chunk_id_3
        assert chunk_id_1.startswith("chunk_")
