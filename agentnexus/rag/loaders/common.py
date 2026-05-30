from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)
from pathlib import Path

from agentnexus.rag.models import DocumentSection, SourceDocument

SUPPORTED_EXTENSIONS = frozenset({
    ".pdf", ".md", ".txt", ".html", ".htm", ".json", ".docx", ".xlsx",
    ".py", ".js", ".ts", ".java", ".go", ".rs", ".cpp", ".c", ".h",
    ".rb", ".php", ".swift", ".kt", ".scala", ".sh", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".sql", ".r", ".m",
})
_FULLWIDTH_TABLE = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
)


def clean_text(text: str) -> str:
    text = _normalize_common(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    stripped_lines = [line.strip() for line in text.split("\n")]
    filtered_lines: list[str] = []
    for line in stripped_lines:
        if line:
            if len(line) >= 3 or re.search(r"[一-鿿，。！？、；：\"\"''（）]", line):
                filtered_lines.append(line)
            continue
        if filtered_lines and filtered_lines[-1] != "":
            filtered_lines.append("")
    text = "\n".join(filtered_lines)
    text = re.sub(r"(?<=[^\x00-\x7f])\n(?=[^\x00-\x7f])", "", text)
    return text.strip()


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


def _build_single_section_document(
    file_path: str,
    *,
    raw_text: str,
    indexed_text: str,
    file_format: str,
) -> SourceDocument:
    document = SourceDocument.create(
        source_uri=file_path,
        raw_text=raw_text,
        metadata={"format": file_format},
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
            metadata={"format": file_format, "section_title": Path(file_path).stem},
        )
    ]
    return document


def _build_sectioned_document(
    file_path: str,
    parsed_sections: list[tuple[str, str, dict]],
    metadata: dict,
    *,
    raw_text_override: str | None = None,
) -> SourceDocument:
    indexed_sections = [section[1] for section in parsed_sections if section[1]]
    joined_indexed = "\n\n".join(indexed_sections).strip()
    raw_text = raw_text_override
    if raw_text is None:
        raw_text = "\n\n".join(section[0] for section in parsed_sections if section[0]).strip()
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
