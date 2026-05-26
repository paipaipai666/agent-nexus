from __future__ import annotations

import re
from pathlib import Path

from agentnexus.rag.models import SourceDocument

from .common import (
    _build_sectioned_document,
    _clean_markdown_text,
    _compose_indexed_text,
    _is_code_fence,
    _normalize_common,
)

_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def _load_markdown(file_path: str) -> SourceDocument:
    raw_text = Path(file_path).read_text(encoding="utf-8")
    parsed_sections = _split_markdown_sections(raw_text, Path(file_path).stem)
    return _build_sectioned_document(file_path, parsed_sections, {"format": "markdown"}, raw_text_override=raw_text)


def _split_markdown_sections(raw_text: str, fallback_title: str) -> list[tuple[str, str, dict]]:
    normalized = _normalize_common(raw_text)
    lines = normalized.split("\n")
    sections: list[tuple[str, str, dict]] = []
    heading_path: list[str] = []
    buffer: list[str] = []
    in_code_block = False

    def flush_buffer():
        section_text = _clean_markdown_text("\n".join(buffer))
        buffer.clear()
        if not section_text:
            return
        metadata: dict[str, object] = {"format": "markdown"}
        if heading_path:
            metadata["heading_path"] = heading_path.copy()
            metadata["section_title"] = heading_path[-1]
        else:
            metadata["section_title"] = fallback_title
        indexed_text = _compose_indexed_text(metadata, section_text)
        sections.append((section_text, indexed_text, metadata))

    for line in lines:
        stripped = line.strip()
        if _is_code_fence(stripped):
            buffer.append(line)
            in_code_block = not in_code_block
            continue
        if not in_code_block:
            match = _HEADING_PATTERN.match(stripped)
            if match:
                flush_buffer()
                level = len(match.group(1))
                title = match.group(2).strip()
                heading_path[:] = heading_path[: level - 1]
                heading_path.append(title)
                continue
        buffer.append(line)

    flush_buffer()
    if sections:
        return sections

    fallback_text = _clean_markdown_text(raw_text)
    if not fallback_text:
        return []
    fallback_metadata = {
        "format": "markdown",
        "heading_path": [fallback_title],
        "section_title": fallback_title,
    }
    return [(fallback_text, _compose_indexed_text(fallback_metadata, fallback_text), fallback_metadata)]
