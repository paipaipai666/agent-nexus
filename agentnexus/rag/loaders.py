from __future__ import annotations

import re
from pathlib import Path

import fitz

from .models import DocumentSection, SourceDocument

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".md", ".txt"})
_FULLWIDTH_TABLE = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
)
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def clean_text(text: str) -> str:
    text = _normalize_common(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    stripped_lines = [line.strip() for line in text.split("\n")]
    filtered_lines: list[str] = []
    for line in stripped_lines:
        if line:
            if len(line) >= 3 or re.search(r"[，。！？、；：\"\"''（）]", line):
                filtered_lines.append(line)
            continue
        if filtered_lines and filtered_lines[-1] != "":
            filtered_lines.append("")
    text = "\n".join(filtered_lines)
    text = re.sub(r"(?<=[^\x00-\x7f])\n(?=[^\x00-\x7f])", "", text)
    return text.strip()


def load_document(file_path: str) -> str:
    return load_structured_document(file_path).raw_text


def load_structured_document(file_path: str) -> SourceDocument:
    ext = Path(file_path).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"不支持的文件格式: {ext}，支持: {SUPPORTED_EXTENSIONS}")
    if ext == ".pdf":
        return _load_pdf(file_path)
    if ext == ".md":
        return _load_markdown(file_path)
    return _load_text(file_path)


def _normalize_common(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", normalized)
    return normalized.translate(_FULLWIDTH_TABLE)


def _clean_markdown_text(text: str) -> str:
    normalized = _normalize_common(text)
    lines = normalized.split("\n")
    cleaned_lines: list[str] = []
    in_code_block = False
    for line in lines:
        stripped = line.strip()
        if _is_code_fence(stripped):
            cleaned_lines.append(stripped)
            in_code_block = not in_code_block
            continue
        if in_code_block:
            cleaned_lines.append(line.rstrip())
        else:
            cleaned_lines.append(stripped)
    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _load_text(file_path: str) -> SourceDocument:
    raw_text = Path(file_path).read_text(encoding="utf-8")
    indexed_text = clean_text(raw_text)
    document = SourceDocument.create(
        source_uri=file_path,
        raw_text=raw_text,
        metadata={"format": "text"},
        indexed_text=indexed_text,
        sparse_text=indexed_text,
        sections=[],
    )
    document.sections = [
        DocumentSection.create(
            document.document_version,
            section_index=0,
            raw_text=raw_text,
            indexed_text=indexed_text,
            sparse_text=indexed_text,
            metadata={"format": "text", "section_title": Path(file_path).stem},
        )
    ]
    return document


def _load_pdf(file_path: str) -> SourceDocument:
    pdf = fitz.open(file_path)
    try:
        page_texts = [page.get_text("text") for page in pdf]
    finally:
        pdf.close()

    raw_text = "\n\n".join(page_texts).strip()
    metadata = {"format": "pdf", "page_count": len(page_texts)}
    indexed_sections: list[str] = []
    document = SourceDocument.create(
        source_uri=file_path,
        raw_text=raw_text,
        metadata=metadata,
        indexed_text="",
        sparse_text="",
        sections=[],
    )
    sections: list[DocumentSection] = []
    for index, page_text in enumerate(page_texts):
        page_number = index + 1
        cleaned_text = clean_text(page_text)
        page_metadata = {
            "format": "pdf",
            "page_number": page_number,
            "section_title": f"Page {page_number}",
        }
        indexed_text = _compose_indexed_text(page_metadata, cleaned_text)
        indexed_sections.append(indexed_text)
        sections.append(
            DocumentSection.create(
                document.document_version,
                section_index=index,
                raw_text=page_text.strip(),
                indexed_text=indexed_text,
                sparse_text=indexed_text,
                metadata=page_metadata,
                page_number=page_number,
            )
        )
    document.sections = sections
    joined = "\n\n".join(section for section in indexed_sections if section).strip()
    document.indexed_text = joined
    document.sparse_text = joined
    return document


def _load_markdown(file_path: str) -> SourceDocument:
    raw_text = Path(file_path).read_text(encoding="utf-8")
    parsed_sections = _split_markdown_sections(raw_text, Path(file_path).stem)
    metadata = {"format": "markdown"}
    indexed_sections = [section[1] for section in parsed_sections]
    joined_indexed = "\n\n".join(text for text in indexed_sections if text).strip()
    document = SourceDocument.create(
        source_uri=file_path,
        raw_text=raw_text,
        metadata=metadata,
        indexed_text=joined_indexed,
        sparse_text=joined_indexed,
        sections=[],
    )
    document.sections = [
        DocumentSection.create(
            document.document_version,
            section_index=index,
            raw_text=raw_section,
            indexed_text=indexed_text,
            sparse_text=indexed_text,
            metadata=section_metadata,
        )
        for index, (raw_section, indexed_text, section_metadata) in enumerate(parsed_sections)
    ]
    return document


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


def _compose_indexed_text(metadata: dict, body: str) -> str:
    heading_path = metadata.get("heading_path") or []
    prefix_parts = [part for part in heading_path if isinstance(part, str) and part]
    if not prefix_parts:
        section_title = metadata.get("section_title")
        if isinstance(section_title, str) and section_title:
            prefix_parts.append(section_title)
    prefix = "\n".join(prefix_parts)
    if prefix and body:
        return f"{prefix}\n\n{body}".strip()
    return (prefix or body).strip()


def _is_code_fence(line: str) -> bool:
    return line.startswith("```") or line.startswith("~~~")
