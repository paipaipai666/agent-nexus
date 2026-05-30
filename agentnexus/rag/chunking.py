from __future__ import annotations

import re
from enum import Enum

from .models import ChunkRecord, DocumentSection, SourceDocument


class ChunkStrategy(Enum):
    FIXED = "fixed"
    RECURSIVE = "recursive"
    SEMANTIC = "semantic"


_SEPARATORS = [
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    "；",
    "，",
    ".",
    "!",
    "?",
    ";",
    ",",
    " ",
    "",
]

_LIST_LINE_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_HEADING_LINE_RE = re.compile(r"^\s*#{1,6}\s+")
_CODE_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def chunk_text(
    text: str,
    strategy: ChunkStrategy = ChunkStrategy.RECURSIVE,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return []
    if strategy == ChunkStrategy.FIXED:
        return _fixed_window_split(normalized, chunk_size, chunk_overlap)
    if strategy == ChunkStrategy.RECURSIVE:
        return _recursive_split(normalized, chunk_size, chunk_overlap)
    if strategy == ChunkStrategy.SEMANTIC:
        return _semantic_split(normalized, chunk_size, chunk_overlap)
    raise ValueError(f"未知的分块策略: {strategy}")


def chunk_structured_document(
    document: SourceDocument,
    strategy: ChunkStrategy = ChunkStrategy.RECURSIVE,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> list[ChunkRecord]:
    sections = document.sections or [
        DocumentSection.create(
            document.document_version,
            section_index=0,
            raw_text=document.raw_text or document.content,
            indexed_text=document.indexed_text or document.content,
            sparse_text=document.sparse_text or document.content,
            metadata=dict(document.metadata),
        )
    ]

    chunks: list[ChunkRecord] = []
    for section in sections:
        prefix = _section_prefix(section)
        body_text = _section_body(section)
        parts = chunk_text(body_text, strategy=strategy, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        if not parts and section.indexed_text.strip():
            parts = [section.indexed_text.strip()]
        for part in parts:
            indexed_text = _prepend_prefix(prefix, part)
            metadata = dict(document.metadata)
            metadata.update(section.metadata)
            metadata["section_id"] = section.section_id
            metadata["section_index"] = section.section_index
            metadata.update(_chunk_structure_metadata(part, section))
            chunks.append(
                ChunkRecord.create(
                    document,
                    chunk_index=len(chunks),
                    raw_text=part,
                    indexed_text=indexed_text,
                    sparse_text=indexed_text,
                    metadata=metadata,
                )
            )
    return chunks


def _fixed_window_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    if len(text) <= chunk_size:
        return [text] if text.strip() else []
    step = max(chunk_size - overlap, 1)
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += step
    return chunks


def _recursive_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        separators=_SEPARATORS,
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        length_function=len,
        is_separator_regex=False,
    )
    return [doc.strip() for doc in splitter.split_text(text) if doc.strip()]


def _semantic_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    blocks = _split_semantic_blocks(text)
    if not blocks:
        return []

    chunks: list[str] = []
    buffer = blocks[0]
    for block in blocks[1:]:
        candidate = f"{buffer}\n\n{block}".strip()
        if len(candidate) <= chunk_size:
            buffer = candidate
            continue
        if len(buffer) > chunk_size:
            chunks.extend(_recursive_split(buffer, chunk_size, overlap))
        else:
            chunks.append(buffer)
        buffer = block

    if len(buffer) > chunk_size:
        chunks.extend(_recursive_split(buffer, chunk_size, overlap))
    else:
        chunks.append(buffer)

    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for index in range(1, len(chunks)):
            raw_prefix = chunks[index - 1][-overlap:]
            # Find nearest word boundary to avoid splitting mid-word
            if len(raw_prefix) < overlap:
                prefix = raw_prefix.strip()
            else:
                space_idx = raw_prefix.find(' ')
                if space_idx > 0:
                    prefix = raw_prefix[space_idx + 1:].strip()
                else:
                    prefix = raw_prefix.strip()
            overlapped.append(f"{prefix}\n{chunks[index]}".strip() if prefix else chunks[index])
        return overlapped
    return chunks


def _split_semantic_blocks(text: str) -> list[str]:
    lines = text.splitlines()
    if not lines:
        return []

    blocks: list[str] = []
    buffer: list[str] = []

    def flush_buffer():
        if not buffer:
            return
        block = "\n".join(line.rstrip() for line in buffer).strip()
        buffer.clear()
        if block:
            blocks.append(block)

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if _CODE_FENCE_RE.match(stripped):
            flush_buffer()
            code_lines = [line.rstrip()]
            index += 1
            while index < len(lines):
                code_lines.append(lines[index].rstrip())
                if _CODE_FENCE_RE.match(lines[index].strip()):
                    index += 1
                    break
                index += 1
            block = "\n".join(code_lines).strip()
            if block:
                blocks.append(block)
            continue

        if not stripped:
            flush_buffer()
            index += 1
            continue

        if _HEADING_LINE_RE.match(stripped):
            flush_buffer()
            heading_block = [line.rstrip()]
            index += 1
            while index < len(lines):
                candidate = lines[index]
                candidate_stripped = candidate.strip()
                if not candidate_stripped:
                    break
                if _HEADING_LINE_RE.match(candidate_stripped) or _CODE_FENCE_RE.match(candidate_stripped):
                    break
                if _LIST_LINE_RE.match(candidate_stripped):
                    break
                heading_block.append(candidate.rstrip())
                index += 1
            block = "\n".join(heading_block).strip()
            if block:
                blocks.append(block)
            continue

        if _LIST_LINE_RE.match(stripped):
            flush_buffer()
            list_block = [line.rstrip()]
            index += 1
            while index < len(lines):
                candidate = lines[index]
                candidate_stripped = candidate.strip()
                if not candidate_stripped:
                    break
                if _LIST_LINE_RE.match(candidate_stripped) or candidate.startswith((" ", "\t")):
                    list_block.append(candidate.rstrip())
                    index += 1
                    continue
                break
            block = "\n".join(list_block).strip()
            if block:
                blocks.append(block)
            continue

        buffer.append(line.rstrip())
        index += 1

    flush_buffer()
    return blocks


def _chunk_structure_metadata(text: str, section: DocumentSection) -> dict[str, object]:
    metadata: dict[str, object] = {
        "block_type": _detect_block_type(text),
        "has_code": bool(_CODE_FENCE_RE.search(text)),
        "has_list": any(_LIST_LINE_RE.match(line.strip()) for line in text.splitlines() if line.strip()),
    }
    heading_path = section.metadata.get("heading_path") or []
    if isinstance(heading_path, list):
        normalized = [part for part in heading_path if isinstance(part, str) and part.strip()]
        if normalized:
            metadata["heading_depth"] = len(normalized)
    return metadata


def _detect_block_type(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "paragraph"
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if not lines:
        return "paragraph"
    if _CODE_FENCE_RE.match(lines[0]):
        return "code"
    if _HEADING_LINE_RE.match(lines[0]):
        return "heading"
    if _LIST_LINE_RE.match(lines[0]):
        return "list"
    return "paragraph"


def _section_prefix(section: DocumentSection) -> str:
    metadata = section.metadata
    if metadata.get("format") == "markdown":
        heading_path = metadata.get("heading_path") or []
        return "\n".join(part for part in heading_path if isinstance(part, str) and part).strip()
    if metadata.get("format") == "pdf" and isinstance(section.page_number, int):
        return f"Page {section.page_number}"
    return ""


def _section_body(section: DocumentSection) -> str:
    if section.metadata.get("format") == "markdown":
        return (section.raw_text or section.indexed_text).strip()
    if section.metadata.get("format") == "pdf":
        return (section.raw_text or section.indexed_text).strip()
    return section.indexed_text.strip()


def _prepend_prefix(prefix: str, body: str) -> str:
    prefix = prefix.strip()
    body = body.strip()
    if prefix and body:
        return f"{prefix}\n\n{body}".strip()
    return prefix or body
