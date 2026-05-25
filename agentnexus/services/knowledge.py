"""Knowledge-base service facade."""

from __future__ import annotations

from typing import Any


class KnowledgeBaseService:
    def __init__(self, settings: Any):
        self.settings = settings

    def search(self, query: str, **kwargs: Any) -> str:
        from agentnexus.tools.kb_search import kb_search

        return kb_search(query=query, **kwargs)

    def import_document(self, path: str, **kwargs: Any) -> Any:
        from agentnexus.rag.ingestion import ingest

        return ingest(path, **kwargs)
