"""Result rendering and citation helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .retriever import SearchResult


def result_display_text(result: SearchResult) -> str:
    context_text = result.context_text
    if isinstance(context_text, str) and context_text.strip():
        return context_text
    return result.text


def result_citation(result: SearchResult) -> str:
    if isinstance(result.citation, str) and result.citation.strip():
        return result.citation

    metadata = result.metadata or {}
    source_uri = str(metadata.get("source_uri") or result.id)
    labels: list[str] = []
    section_title = metadata.get("section_title")
    if isinstance(section_title, str) and section_title.strip():
        labels.append(section_title.strip())
    page_number = metadata.get("page_number")
    if isinstance(page_number, int):
        labels.append(f"Page {page_number}")
    heading_depth = metadata.get("heading_depth")
    if isinstance(heading_depth, int):
        labels.append(f"H{heading_depth}")
    if labels:
        return f"{source_uri} [{' | '.join(labels)}]"
    return source_uri
