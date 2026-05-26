from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from agentnexus.rag.models import SourceDocument

from .common import _build_sectioned_document, _build_single_section_document, _compose_indexed_text, clean_text


def _load_html(file_path: str) -> SourceDocument:
    parser = _StructuredHtmlParser(Path(file_path).stem)
    parser.feed(Path(file_path).read_text(encoding="utf-8"))
    parser.close()
    parsed_sections = parser.build_sections()
    if not parsed_sections:
        return _build_single_section_document(
            file_path,
            raw_text="",
            indexed_text="",
            file_format="html",
        )
    return _build_sectioned_document(file_path, parsed_sections, {"format": "html"})


class _StructuredHtmlParser(HTMLParser):
    _BLOCK_TAGS = {
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "footer",
        "header",
        "li",
        "main",
        "ol",
        "p",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }

    def __init__(self, fallback_title: str):
        super().__init__(convert_charrefs=True)
        self._fallback_title = fallback_title
        self._ignore_depth = 0
        self._heading_level: int | None = None
        self._heading_parts: list[str] = []
        self._heading_path: list[str] = []
        self._buffer: list[str] = []
        self._sections: list[tuple[str, str, dict]] = []

    def handle_starttag(self, tag: str, attrs):
        del attrs
        normalized = tag.casefold()
        if normalized in {"script", "style", "noscript"}:
            self._ignore_depth += 1
            return
        if self._ignore_depth:
            return
        if re.fullmatch(r"h[1-6]", normalized):
            self._flush_buffer()
            self._heading_level = int(normalized[1])
            self._heading_parts = []
            return
        if normalized in self._BLOCK_TAGS:
            self._buffer.append("\n")

    def handle_endtag(self, tag: str):
        normalized = tag.casefold()
        if normalized in {"script", "style", "noscript"}:
            self._ignore_depth = max(self._ignore_depth - 1, 0)
            return
        if self._ignore_depth:
            return
        if self._heading_level is not None and normalized == f"h{self._heading_level}":
            heading_text = clean_text("".join(self._heading_parts))
            if heading_text:
                self._heading_path[:] = self._heading_path[: self._heading_level - 1]
                self._heading_path.append(heading_text)
            self._heading_level = None
            self._heading_parts = []
            self._buffer.append("\n")
            return
        if normalized in self._BLOCK_TAGS:
            self._buffer.append("\n")

    def handle_data(self, data: str):
        if self._ignore_depth or not data.strip():
            return
        if self._heading_level is not None:
            self._heading_parts.append(data)
            return
        self._buffer.append(data)

    def build_sections(self) -> list[tuple[str, str, dict]]:
        self._flush_buffer()
        if self._sections:
            return self._sections
        fallback = clean_text("".join(self._buffer))
        if not fallback:
            return []
        metadata = {"format": "html", "section_title": self._fallback_title, "heading_path": [self._fallback_title]}
        return [(fallback, _compose_indexed_text(metadata, fallback), metadata)]

    def _flush_buffer(self):
        body = clean_text("".join(self._buffer))
        self._buffer.clear()
        if not body:
            return
        metadata: dict[str, object] = {"format": "html"}
        if self._heading_path:
            metadata["heading_path"] = self._heading_path.copy()
            metadata["section_title"] = self._heading_path[-1]
        else:
            metadata["section_title"] = self._fallback_title
        self._sections.append((body, _compose_indexed_text(metadata, body), metadata))
