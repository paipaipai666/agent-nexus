from __future__ import annotations

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
    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    buffer = paragraphs[0]
    for paragraph in paragraphs[1:]:
        if len(buffer) + len(paragraph) <= chunk_size:
            buffer += "\n\n" + paragraph
            continue
        if len(buffer) > chunk_size:
            chunks.extend(_recursive_split(buffer, chunk_size, overlap))
        else:
            chunks.append(buffer)
        buffer = paragraph

    if len(buffer) > chunk_size:
        chunks.extend(_recursive_split(buffer, chunk_size, overlap))
    else:
        chunks.append(buffer)

    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for index in range(1, len(chunks)):
            prefix = chunks[index - 1][-overlap:].strip()
            overlapped.append(f"{prefix}\n{chunks[index]}".strip() if prefix else chunks[index])
        return overlapped
    return chunks


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
