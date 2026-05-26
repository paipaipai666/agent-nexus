from __future__ import annotations

import json
from pathlib import Path

from agentnexus.rag.models import SourceDocument

from .common import _build_sectioned_document, _build_single_section_document, _compose_indexed_text, clean_text


def _load_json(file_path: str) -> SourceDocument:
    data = json.loads(Path(file_path).read_text(encoding="utf-8"))
    parsed_sections = _split_json_sections(data, Path(file_path).stem)
    if not parsed_sections:
        rendered = clean_text(json.dumps(data, ensure_ascii=False, indent=2))
        return _build_single_section_document(
            file_path,
            raw_text=rendered,
            indexed_text=rendered,
            file_format="json",
        )
    return _build_sectioned_document(file_path, parsed_sections, {"format": "json"})


def _split_json_sections(data, fallback_title: str) -> list[tuple[str, str, dict]]:
    sections: list[tuple[str, str, dict]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            section_text = _render_json_value(value, key)
            cleaned = clean_text(section_text)
            if not cleaned:
                continue
            metadata = {"format": "json", "section_title": str(key), "heading_path": [str(key)]}
            sections.append((cleaned, _compose_indexed_text(metadata, cleaned), metadata))
        return sections

    if isinstance(data, list):
        for index, value in enumerate(data):
            title = f"{fallback_title}[{index}]"
            section_text = _render_json_value(value, title)
            cleaned = clean_text(section_text)
            if not cleaned:
                continue
            metadata = {"format": "json", "section_title": title, "heading_path": [title]}
            sections.append((cleaned, _compose_indexed_text(metadata, cleaned), metadata))
        return sections

    rendered = clean_text(_render_json_scalar(data))
    if not rendered:
        return []
    metadata = {"format": "json", "section_title": fallback_title, "heading_path": [fallback_title]}
    return [(rendered, _compose_indexed_text(metadata, rendered), metadata)]


def _render_json_value(value, path: str) -> str:
    lines: list[str] = []

    def walk(node, current_path: str):
        if isinstance(node, dict):
            if not node:
                lines.append(f"{current_path}: {{}}")
                return
            for key, child in node.items():
                child_path = f"{current_path}.{key}" if current_path else str(key)
                walk(child, child_path)
            return
        if isinstance(node, list):
            if not node:
                lines.append(f"{current_path}: []")
                return
            for index, child in enumerate(node):
                walk(child, f"{current_path}[{index}]")
            return
        lines.append(f"{current_path}: {_render_json_scalar(node)}")

    walk(value, path)
    return "\n".join(lines)


def _render_json_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
