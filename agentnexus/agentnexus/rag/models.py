from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .ids import make_chunk_id, make_document_version, make_source_id

MetadataDict = dict[str, Any]


@dataclass(slots=True)
class DocumentSection:
    section_id: str
    section_index: int
    raw_text: str
    indexed_text: str
    sparse_text: str
    metadata: MetadataDict = field(default_factory=dict)
    page_number: int | None = None

    def __post_init__(self):
        if self.page_number is None:
            value = self.metadata.get("page_number")
            self.page_number = value if isinstance(value, int) else None

    @classmethod
    def create(
        cls,
        document_version: str,
        section_index: int,
        raw_text: str,
        indexed_text: str | None = None,
        sparse_text: str | None = None,
        metadata: MetadataDict | None = None,
        page_number: int | None = None,
    ) -> "DocumentSection":
        normalized_metadata = dict(metadata or {})
        normalized_indexed = indexed_text if indexed_text is not None else raw_text
        normalized_sparse = sparse_text if sparse_text is not None else normalized_indexed
        normalized_page = page_number
        if normalized_page is None:
            value = normalized_metadata.get("page_number")
            normalized_page = value if isinstance(value, int) else None
        return cls(
            section_id=make_chunk_id(
                document_version,
                section_index,
                normalized_indexed,
                normalized_metadata,
            ),
            section_index=section_index,
            raw_text=raw_text,
            indexed_text=normalized_indexed,
            sparse_text=normalized_sparse,
            metadata=normalized_metadata,
            page_number=normalized_page,
        )


@dataclass(slots=True)
class KnowledgeBaseRecord:
    kb_id: str
    namespace: str
    display_name: str
    collection_name: str
    description: str = ""
    metadata: MetadataDict = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class SourceDocument:
    document_id: str
    kb_id: str
    source_id: str
    source_uri: str
    document_version: str
    content: str
    metadata: MetadataDict = field(default_factory=dict)
    raw_text: str | None = None
    indexed_text: str | None = None
    sparse_text: str | None = None
    sections: list[DocumentSection] = field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None

    def __post_init__(self):
        if self.raw_text is None:
            self.raw_text = self.content
        if self.indexed_text is None:
            self.indexed_text = self.content
        if self.sparse_text is None:
            self.sparse_text = self.indexed_text

    @classmethod
    def create(
        cls,
        source_uri: str,
        raw_text: str,
        *,
        kb_id: str = "",
        metadata: MetadataDict | None = None,
        indexed_text: str | None = None,
        sparse_text: str | None = None,
        sections: list[DocumentSection] | None = None,
    ) -> "SourceDocument":
        normalized_metadata = dict(metadata or {})
        normalized_indexed = indexed_text if indexed_text is not None else raw_text
        normalized_sparse = sparse_text if sparse_text is not None else normalized_indexed
        source_id = make_source_id(source_uri)
        document_version = make_document_version(source_id, raw_text, normalized_metadata)
        return cls(
            document_id=document_version,
            kb_id=kb_id,
            source_id=source_id,
            source_uri=source_uri,
            document_version=document_version,
            content=raw_text,
            metadata=normalized_metadata,
            raw_text=raw_text,
            indexed_text=normalized_indexed,
            sparse_text=normalized_sparse,
            sections=list(sections or []),
        )


@dataclass(slots=True)
class ChunkRecord:
    chunk_id: str
    kb_id: str
    document_id: str
    document_version: str
    chunk_index: int
    text: str
    metadata: MetadataDict = field(default_factory=dict)
    raw_text: str | None = None
    indexed_text: str | None = None
    sparse_text: str | None = None
    section_index: int | None = None
    page_number: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def __post_init__(self):
        if self.raw_text is None:
            self.raw_text = self.text
        if self.indexed_text is None:
            self.indexed_text = self.text
        if self.sparse_text is None:
            self.sparse_text = self.indexed_text
        if self.section_index is None:
            value = self.metadata.get("section_index")
            self.section_index = value if isinstance(value, int) else None
        if self.page_number is None:
            value = self.metadata.get("page_number")
            self.page_number = value if isinstance(value, int) else None

    @classmethod
    def create(
        cls,
        document: SourceDocument,
        chunk_index: int,
        raw_text: str,
        *,
        kb_id: str | None = None,
        metadata: MetadataDict | None = None,
        indexed_text: str | None = None,
        sparse_text: str | None = None,
    ) -> "ChunkRecord":
        normalized_metadata = dict(metadata or {})
        normalized_indexed = indexed_text if indexed_text is not None else raw_text
        normalized_sparse = sparse_text if sparse_text is not None else normalized_indexed
        return cls(
            chunk_id=make_chunk_id(
                document.document_version,
                chunk_index,
                normalized_indexed,
                normalized_metadata,
            ),
            kb_id=document.kb_id if kb_id is None else kb_id,
            document_id=document.document_id,
            document_version=document.document_version,
            chunk_index=chunk_index,
            text=normalized_indexed,
            metadata=normalized_metadata,
            raw_text=raw_text,
            indexed_text=normalized_indexed,
            sparse_text=normalized_sparse,
            section_index=normalized_metadata.get("section_index"),
            page_number=normalized_metadata.get("page_number"),
        )


@dataclass(slots=True)
class IngestedDocument:
    document: SourceDocument
    chunks: list[ChunkRecord] = field(default_factory=list)

    def legacy_chunks(self) -> list[str]:
        return [chunk.text for chunk in self.chunks]


@dataclass(slots=True)
class IngestionRunRecord:
    run_id: str
    kb_id: str
    status: str
    source_uri: str = ""
    error_message: str = ""
    documents_seen: int = 0
    chunks_written: int = 0
    metadata: MetadataDict = field(default_factory=dict)
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str | None = None
