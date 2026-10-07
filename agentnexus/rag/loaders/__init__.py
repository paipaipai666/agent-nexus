from __future__ import annotations

from pathlib import Path
from typing import Callable

from agentnexus.rag.models import SourceDocument

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
from .pdf import _extract_pdf_page_payload, _extract_pdf_page_text_with_ocr, _load_pdf, fitz
from .text import _load_text

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".md", ".txt", ".html", ".htm", ".json", ".docx", ".xlsx"})

_LOADERS: dict[str, Callable[[str], SourceDocument]] = {
    ".pdf": _load_pdf,
    ".md": _load_markdown,
    ".html": _load_html,
    ".htm": _load_html,
    ".json": _load_json,
    ".docx": _load_docx,
    ".xlsx": _load_xlsx,
}


def load_document(file_path: str) -> str:
    return load_structured_document(file_path).raw_text


def load_structured_document(file_path: str) -> SourceDocument:
    ext = Path(file_path).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"不支持的文件格式: {ext}，支持: {SUPPORTED_EXTENSIONS}")
    return _LOADERS.get(ext, _load_text)(file_path)


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
