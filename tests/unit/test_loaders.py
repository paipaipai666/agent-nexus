"""Tests for agentnexus.rag.loaders."""

import json
import zipfile
from pathlib import Path

import pytest

from agentnexus.rag.loaders import (
    _compose_indexed_text,
    _is_code_fence,
    _normalize_common,
    _split_markdown_sections,
    clean_text,
    load_document,
    load_structured_document,
)


class TestCleanText:
    def test_normalizes_fullwidth_chars(self):
        result = clean_text("ＡＢＣ")
        assert result == "ABC"

    def test_removes_control_chars(self):
        result = clean_text("\x00hello\x01\x02world\x7f")
        assert result == "helloworld"

    def test_normalizes_newlines(self):
        result = clean_text("line1\r\nline2\rline3")
        assert "line1" in result
        assert "line2" in result
        assert "line3" in result

    def test_collapses_excessive_newlines(self):
        result = clean_text("aaa\n\n\n\n\nbbb")
        assert result == "aaa\n\nbbb"

    def test_single_whitespace_lines_removed(self):
        result = clean_text("aaa\n   \nbbb")
        assert result == "aaa\n\nbbb"

    def test_keeps_short_lines_with_punctuation(self):
        result = clean_text("aaa\n好。\nbbb")
        assert "好。" in result

    def test_keeps_short_lines_with_quotes(self):
        result = clean_text('aaa\n"hi"\nbbb')
        assert '"hi"' in result

    def test_removes_short_lines_without_punctuation(self):
        result = clean_text("hello\na\nworld")
        assert "hello\nworld" in result

    def test_removes_cjk_lines_without_punctuation(self):
        result = clean_text("项目\n简介")
        assert result == ""

    def test_strips_final_result(self):
        result = clean_text("  hello world  ")
        assert result == "hello world"


class TestNormalizeCommon:
    def test_fullwidth_to_ascii(self):
        result = _normalize_common("０１２ＡＢＣａｂｃ")
        assert result == "012ABCabc"

    def test_control_chars_removed(self):
        result = _normalize_common("\x00\x01\x02hello\x7f")
        assert result == "hello"

    def test_newlines_normalized(self):
        result = _normalize_common("a\r\nb\rc")
        assert result == "a\nb\nc"


class TestLoadDocument:
    def test_delegates_to_structured_and_returns_raw_text(self, temp_agentnexus_home: Path):
        path = temp_agentnexus_home / "hello.txt"
        path.write_text("hello world", encoding="utf-8")
        result = load_document(str(path))
        assert result == "hello world"


class TestLoadStructuredDocument:
    def test_unsupported_extension_raises(self):
        with pytest.raises(ValueError, match="不支持的文件格式"):
            load_structured_document("file.csv")

    def test_txt_loader(self, temp_agentnexus_home: Path):
        path = temp_agentnexus_home / "test.txt"
        path.write_text("some text content", encoding="utf-8")
        doc = load_structured_document(str(path))
        assert doc.metadata["format"] == "text"
        assert doc.raw_text == "some text content"

    def test_md_loader(self, temp_agentnexus_home: Path):
        path = temp_agentnexus_home / "doc.md"
        path.write_text("# Title\n\nBody", encoding="utf-8")
        doc = load_structured_document(str(path))
        assert doc.metadata["format"] == "markdown"

    def test_html_loader(self, temp_agentnexus_home: Path):
        path = temp_agentnexus_home / "page.html"
        path.write_text("<h1>Guide</h1><p>Hello <b>world</b></p>", encoding="utf-8")

        doc = load_structured_document(str(path))

        assert doc.metadata["format"] == "html"
        assert doc.sections[0].metadata["section_title"] == "Guide"
        assert "Hello world" in doc.sections[0].raw_text

    def test_json_loader(self, temp_agentnexus_home: Path):
        path = temp_agentnexus_home / "config.json"
        path.write_text(
            json.dumps({"database": {"engine": "sqlite", "enabled": True}}, ensure_ascii=False),
            encoding="utf-8",
        )

        doc = load_structured_document(str(path))

        assert doc.metadata["format"] == "json"
        assert doc.sections[0].metadata["section_title"] == "database"
        assert "database.engine: sqlite" in doc.sections[0].raw_text

    def test_docx_loader(self, temp_agentnexus_home: Path):
        path = temp_agentnexus_home / "guide.docx"
        _write_minimal_docx(
            path,
            [
                ("Heading 1", "Guide"),
                (None, "Install the package"),
                ("Heading 2", "Usage"),
                (None, "Run the command"),
            ],
        )

        doc = load_structured_document(str(path))

        assert doc.metadata["format"] == "docx"
        assert [section.metadata["section_title"] for section in doc.sections] == ["Guide", "Usage"]
        assert "Install the package" in doc.sections[0].raw_text

    def test_xlsx_loader(self, temp_agentnexus_home: Path):
        path = temp_agentnexus_home / "matrix.xlsx"
        _write_minimal_xlsx(path, sheet_name="Metrics", rows=[["name", "value"], ["latency", "12"]])

        doc = load_structured_document(str(path))

        assert doc.metadata["format"] == "xlsx"
        assert doc.sections[0].metadata["section_title"] == "Metrics"
        assert "latency\t12" in doc.sections[0].raw_text

    def test_pdf_loader_missing_file_raises(self):
        with pytest.raises(Exception):
            load_structured_document("nonexistent.pdf")


class TestSplitMarkdownSections:
    def test_basic_heading_split(self, temp_agentnexus_home: Path):
        path = temp_agentnexus_home / "doc.md"
        path.write_text("# H1\n\ncontent1\n\n## H2\n\ncontent2", encoding="utf-8")
        doc = load_structured_document(str(path))
        assert len(doc.sections) >= 2
        assert doc.sections[0].metadata["section_title"] == "H1"

    def test_no_headings_fallback(self, temp_agentnexus_home: Path):
        path = temp_agentnexus_home / "plain.md"
        path.write_text("Just a plain paragraph\n\nNo headings here.", encoding="utf-8")
        doc = load_structured_document(str(path))
        assert len(doc.sections) >= 1

    def test_empty_text_returns_empty(self):
        result = _split_markdown_sections("", "untitled")
        assert result == []

    def test_blank_text_returns_empty(self):
        result = _split_markdown_sections("   \n\n  ", "untitled")
        assert result == []

    def test_code_block_respected(self):
        text = "# Title\n\n```\n# This is not a heading\n```\n\nBody text"
        result = _split_markdown_sections(text, "doc")
        assert len(result) >= 1


class TestComposeIndexedText:
    def test_with_heading_path(self):
        meta = {"heading_path": ["Guide", "Install"]}
        assert _compose_indexed_text(meta, "body") == "Guide\nInstall\n\nbody"

    def test_no_heading_path_uses_section_title(self):
        meta = {"section_title": "Overview"}
        assert _compose_indexed_text(meta, "body") == "Overview\n\nbody"

    def test_no_heading_path_no_title(self):
        meta = {}
        assert _compose_indexed_text(meta, "body") == "body"

    def test_prefix_only_no_body(self):
        meta = {"heading_path": ["Title"]}
        assert _compose_indexed_text(meta, "") == "Title"

    def test_no_prefix_no_body(self):
        meta = {}
        assert _compose_indexed_text(meta, "") == ""


class TestIsCodeFence:
    def test_backtick_fence(self):
        assert _is_code_fence("```python") is True

    def test_tilde_fence(self):
        assert _is_code_fence("~~~") is True

    def test_plain_line(self):
        assert _is_code_fence("hello") is False

    def test_empty(self):
        assert _is_code_fence("") is False


def _write_minimal_docx(path: Path, paragraphs: list[tuple[str | None, str]]) -> None:
    content_types = "\n".join(
        [
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>",
            "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">",
            "  <Default Extension=\"rels\" "
            "ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>",
            "  <Default Extension=\"xml\" ContentType=\"application/xml\"/>",
            "  <Override PartName=\"/word/document.xml\" "
            "ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>",
            "</Types>",
        ]
    )
    rels = "\n".join(
        [
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>",
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">",
            "  <Relationship Id=\"rId1\" "
            "Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" "
            "Target=\"word/document.xml\"/>",
            "</Relationships>",
        ]
    )
    body = []
    for style, text in paragraphs:
        if style:
            body.append(
                f"<w:p><w:pPr><w:pStyle w:val=\"{style}\"/></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>"
            )
        else:
            body.append(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>")
    document = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        f"<w:body>{''.join(body)}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)


def _write_minimal_xlsx(path: Path, *, sheet_name: str, rows: list[list[str]]) -> None:
    content_types = "\n".join(
        [
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>",
            "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">",
            "  <Default Extension=\"rels\" "
            "ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>",
            "  <Default Extension=\"xml\" ContentType=\"application/xml\"/>",
            "  <Override PartName=\"/xl/workbook.xml\" "
            "ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml\"/>",
            "  <Override PartName=\"/xl/worksheets/sheet1.xml\" "
            "ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/>",
            "  <Override PartName=\"/xl/sharedStrings.xml\" "
            "ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml\"/>",
            "</Types>",
        ]
    )
    rels = "\n".join(
        [
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>",
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">",
            "  <Relationship Id=\"rId1\" "
            "Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" "
            "Target=\"xl/workbook.xml\"/>",
            "</Relationships>",
        ]
    )
    workbook = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="{sheet_name}" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"""
    workbook_rels = "\n".join(
        [
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>",
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">",
            "  <Relationship Id=\"rId1\" "
            "Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" "
            "Target=\"worksheets/sheet1.xml\"/>",
            "  <Relationship Id=\"rId2\" "
            "Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings\" "
            "Target=\"sharedStrings.xml\"/>",
            "</Relationships>",
        ]
    )
    unique_strings: list[str] = []
    for row in rows:
        for value in row:
            if value not in unique_strings:
                unique_strings.append(value)
    shared_strings = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        f"<sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" "
        f"count=\"{len(unique_strings)}\" uniqueCount=\"{len(unique_strings)}\">"
        + "".join(f"<si><t>{value}</t></si>" for value in unique_strings)
        + "</sst>"
    )
    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            cell_ref = f"{chr(64 + column_index)}{row_index}"
            string_index = unique_strings.index(value)
            cells.append(f"<c r=\"{cell_ref}\" t=\"s\"><v>{string_index}</v></c>")
        sheet_rows.append(f"<row r=\"{row_index}\">{''.join(cells)}</row>")
    worksheet = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">"
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/sharedStrings.xml", shared_strings)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
