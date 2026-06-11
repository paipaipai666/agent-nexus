"""Knowledge base API routes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

router = APIRouter(tags=["knowledge"])

# Keep references to background tasks to prevent garbage collection
_background_tasks: set = set()

# Dedicated thread pool for ingestion to avoid blocking the default pool
_ingestion_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ingestion")


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
    from dataclasses import asdict

    from agentnexus.core.config import get_settings
    from agentnexus.rag.store import get_knowledge_base_catalog

    settings = get_settings()
    catalog = get_knowledge_base_catalog()
    kb = catalog.get_knowledge_base(settings.rag_default_namespace)
    if kb is None:
        return {"documents": [], "total_chunks": 0}
    docs = catalog.list_documents()
    doc_dicts = []
    for d in docs:
        doc_dict = asdict(d)
        doc_dict["chunk_count"] = catalog.count_chunks(d.document_id)
        doc_dicts.append(doc_dict)
    total = catalog.count_chunks_by_kb(kb.kb_id)
    return {"documents": doc_dicts, "total_chunks": total}


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
    import asyncio
    import os
    import tempfile
    import uuid
    from pathlib import Path

    from agentnexus.core.config import get_settings
    from agentnexus.rag.kb_service import ingest_one_document

    settings = get_settings()
    filename = file.filename or "upload.txt"
    suffix = Path(filename).suffix
    run_id = f"ingest_{uuid.uuid4().hex[:12]}"

    loop = asyncio.get_running_loop()

    def _save_file():
        """Read file and save to temp — runs in dedicated thread."""
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = tmp.name
        try:
            # Read from underlying file object directly in this thread
            file.file.seek(0)
            while True:
                chunk = file.file.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                tmp.write(chunk)
        finally:
            tmp.close()
        return tmp_path

    # Save file in dedicated thread — never blocks event loop or default pool
    tmp_path = await loop.run_in_executor(_ingestion_executor, _save_file)

    def _run_ingestion():
        """Run ingestion in dedicated thread pool."""
        try:
            ingest_one_document(
                tmp_path,
                namespace=settings.rag_default_namespace,
                enable_contextual=settings.enable_contextual_retrieval,
                run_id=run_id,
                source_uri=filename,
            )
        except Exception:
            pass  # Error recorded in IngestionRunRecord
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # Run ingestion in dedicated thread pool — never blocks default pool
    task = loop.run_in_executor(_ingestion_executor, _run_ingestion)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"status": "processing", "run_id": run_id, "filename": filename}


@router.get("/documents/runs/{run_id}")
def get_ingestion_run(run_id: str):
    from agentnexus.rag.store import get_knowledge_base_catalog

    catalog = get_knowledge_base_catalog()
    run = catalog.get_ingestion_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Ingestion run not found: {run_id}")
    return {
        "run_id": run.run_id,
        "status": run.status,
        "source_uri": run.source_uri,
        "documents_seen": run.documents_seen,
        "chunks_written": run.chunks_written,
        "error_message": run.error_message,
        "metadata": run.metadata,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


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
