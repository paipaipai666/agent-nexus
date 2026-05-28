from dataclasses import dataclass

from agentnexus.rag.citations import result_citation, result_display_text


@dataclass
class _FakeSearchResult:
    id: str
    text: str
    context_text: str | None = None
    citation: str | None = None
    metadata: dict | None = None


class TestResultDisplayText:
    def test_returns_context_text_when_present(self):
        result = _FakeSearchResult(id="r1", text="fallback", context_text="visible context")
        assert result_display_text(result) == "visible context"

    def test_falls_back_to_text_when_context_is_none(self):
        result = _FakeSearchResult(id="r1", text="fallback text", context_text=None)
        assert result_display_text(result) == "fallback text"

    def test_falls_back_to_text_when_context_is_empty(self):
        result = _FakeSearchResult(id="r1", text="fallback text", context_text="   ")
        assert result_display_text(result) == "fallback text"

    def test_falls_back_to_text_when_context_is_blank_string(self):
        result = _FakeSearchResult(id="r1", text="fallback text", context_text="")
        assert result_display_text(result) == "fallback text"


class TestResultCitation:
    def test_returns_existing_citation_when_present(self):
        result = _FakeSearchResult(id="r1", text="t", citation="custom citation")
        assert result_citation(result) == "custom citation"

    def test_returns_existing_citation_stripped(self):
        result = _FakeSearchResult(id="r1", text="t", citation="  valid citation  ")
        assert result_citation(result) == "  valid citation  "

    def test_ignores_whitespace_only_citation(self):
        result = _FakeSearchResult(
            id="r1", text="t", citation="   ", metadata={"source_uri": "doc.md"}
        )
        assert result_citation(result) == "doc.md"

    def test_uses_source_uri_from_metadata(self):
        result = _FakeSearchResult(
            id="r1", text="t", metadata={"source_uri": "guide.pdf"}
        )
        assert result_citation(result) == "guide.pdf"

    def test_falls_back_to_id_when_no_source_uri(self):
        result = _FakeSearchResult(id="chunk_42", text="t", metadata={})
        assert result_citation(result) == "chunk_42"

    def test_falls_back_to_id_when_metadata_is_none(self):
        result = _FakeSearchResult(id="chunk_99", text="t", metadata=None)
        assert result_citation(result) == "chunk_99"

    def test_includes_section_title_label(self):
        result = _FakeSearchResult(
            id="r1", text="t", metadata={"source_uri": "doc.md", "section_title": "Intro"}
        )
        assert result_citation(result) == "doc.md [Intro]"

    def test_includes_page_number_label(self):
        result = _FakeSearchResult(
            id="r1", text="t", metadata={"source_uri": "doc.md", "page_number": 5}
        )
        assert result_citation(result) == "doc.md [Page 5]"

    def test_includes_heading_depth_label(self):
        result = _FakeSearchResult(
            id="r1", text="t", metadata={"source_uri": "doc.md", "heading_depth": 2}
        )
        assert result_citation(result) == "doc.md [H2]"

    def test_includes_multiple_labels_joined(self):
        result = _FakeSearchResult(
            id="r1",
            text="t",
            metadata={
                "source_uri": "doc.md",
                "section_title": "Methods",
                "page_number": 12,
                "heading_depth": 3,
            },
        )
        assert result_citation(result) == "doc.md [Methods | Page 12 | H3]"

    def test_ignores_non_string_section_title(self):
        result = _FakeSearchResult(
            id="r1", text="t", metadata={"source_uri": "doc.md", "section_title": 42}
        )
        assert result_citation(result) == "doc.md"

    def test_ignores_non_int_page_number(self):
        result = _FakeSearchResult(
            id="r1", text="t", metadata={"source_uri": "doc.md", "page_number": "5"}
        )
        assert result_citation(result) == "doc.md"

    def test_ignores_whitespace_only_section_title(self):
        result = _FakeSearchResult(
            id="r1", text="t", metadata={"source_uri": "doc.md", "section_title": "   "}
        )
        assert result_citation(result) == "doc.md"
