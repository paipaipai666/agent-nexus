"""Knowledge base API routes."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

router = APIRouter(tags=["knowledge"])


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    namespace: str | None = None
    source: str | None = None
    file_format: str | None = None
    section_title: str | None = None
    view: str | None = None


@router.get("/documents")
def list_documents():
    from agentnexus.core.config import get_settings
    from agentnexus.rag.store import get_knowledge_base_catalog

    settings = get_settings()
    catalog = get_knowledge_base_catalog()
    kb = catalog.get_knowledge_base(settings.rag_default_namespace)
    if kb is None:
        return {"documents": [], "total_chunks": 0}
    docs = catalog.list_documents()
    return {
        "documents": [d.__dict__ if hasattr(d, "__dict__") else d for d in docs],
        "total_chunks": kb.total_chunks if hasattr(kb, "total_chunks") else 0,
    }


@router.post("/search")
def search_kb(req: SearchRequest):
    from agentnexus.rag.kb_service import search_kb

    try:
        results = search_kb(
            query=req.query,
            namespace=req.namespace,
            top_k=req.top_k,
            source=req.source,
            file_format=req.file_format,
            section_title=req.section_title,
            view=req.view,
        )
        results_data = []
        for r in results:
            if hasattr(r, "__dict__"):
                results_data.append(r.__dict__)
            elif isinstance(r, dict):
                results_data.append(r)
            else:
                results_data.append({"text": str(r)})
        return {"results": results_data, "query": req.query}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/documents")
async def ingest_document(file: UploadFile = File(...)):
    import tempfile
    from pathlib import Path

    from agentnexus.rag.kb_service import ingest_one_document

    suffix = Path(file.filename or "upload.txt").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = ingest_one_document(tmp_path)
        return {"status": "ok", "filename": file.filename, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str):
    from agentnexus.rag.store import get_knowledge_base_catalog

    catalog = get_knowledge_base_catalog()
    try:
        catalog.delete_document(doc_id)
        return {"status": "deleted", "doc_id": doc_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/documents/runs")
def list_ingestion_runs():
    from agentnexus.rag.store import get_knowledge_base_catalog

    catalog = get_knowledge_base_catalog()
    runs = catalog.list_ingestion_runs() if hasattr(catalog, "list_ingestion_runs") else []
    return {"runs": [r.__dict__ if hasattr(r, "__dict__") else r for r in runs]}
