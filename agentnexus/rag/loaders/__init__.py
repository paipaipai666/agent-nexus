from __future__ import annotations

from pathlib import Path

from agentnexus.rag.models import SourceDocument

from . import pdf as _pdf
from .common import (
    _build_sectioned_document,
    _build_single_section_document,
    _clean_markdown_text,
    _compose_indexed_text,
    _is_code_fence,
    _normalize_common,
    clean_text,
)
from .html import _load_html, _StructuredHtmlParser
from .json_loader import _load_json
from .markdown import _load_markdown, _split_markdown_sections
from .office import _load_docx, _load_xlsx
from .pdf import _extract_pdf_page_text_with_ocr, fitz
from .text import _load_text

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".md", ".txt", ".html", ".htm", ".json", ".docx", ".xlsx"})


def load_document(file_path: str) -> str:
    return load_structured_document(file_path).raw_text


def _extract_pdf_page_payload(page) -> dict[str, object]:
    _pdf._extract_pdf_page_text_with_ocr = _extract_pdf_page_text_with_ocr
    return _pdf._extract_pdf_page_payload(page)


def _load_pdf(file_path: str) -> SourceDocument:
    _pdf._extract_pdf_page_text_with_ocr = _extract_pdf_page_text_with_ocr
    return _pdf._load_pdf(file_path)


def load_structured_document(file_path: str) -> SourceDocument:
    ext = Path(file_path).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"不支持的文件格式: {ext}，支持: {SUPPORTED_EXTENSIONS}")
    if ext == ".pdf":
        return _load_pdf(file_path)
    if ext == ".md":
        return _load_markdown(file_path)
    if ext in {".html", ".htm"}:
        return _load_html(file_path)
    if ext == ".json":
        return _load_json(file_path)
    if ext == ".docx":
        return _load_docx(file_path)
    if ext == ".xlsx":
        return _load_xlsx(file_path)
    return _load_text(file_path)


__all__ = [
    "SUPPORTED_EXTENSIONS",
    "clean_text",
    "load_document",
    "load_structured_document",
    "_StructuredHtmlParser",
    "_build_sectioned_document",
    "_build_single_section_document",
    "_clean_markdown_text",
    "_compose_indexed_text",
    "_extract_pdf_page_payload",
    "_extract_pdf_page_text_with_ocr",
    "fitz",
    "_is_code_fence",
    "_load_docx",
    "_load_html",
    "_load_json",
    "_load_markdown",
    "_load_pdf",
    "_load_text",
    "_load_xlsx",
    "_normalize_common",
    "_split_markdown_sections",
]
