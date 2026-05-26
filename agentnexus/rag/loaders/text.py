from __future__ import annotations

from pathlib import Path

from agentnexus.rag.models import SourceDocument

from .common import _build_single_section_document, clean_text


def _load_text(file_path: str) -> SourceDocument:
    raw_text = Path(file_path).read_text(encoding="utf-8")
    indexed_text = clean_text(raw_text)
    return _build_single_section_document(
        file_path,
        raw_text=raw_text,
        indexed_text=indexed_text,
        file_format="text",
    )
