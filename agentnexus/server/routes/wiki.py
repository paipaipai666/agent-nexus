"""Wiki API routes — hybrid Wiki + RAG knowledge system."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

router = APIRouter(tags=["wiki"])


class WikiQueryRequest(BaseModel):
    question: str
    namespace: str = "default"
    force_rag: bool = False


class WikiIngestRequest(BaseModel):
    source_text: str
    source_uri: str
    namespace: str = "default"
    page_type: str = "concept"


class ReviewResolveRequest(BaseModel):
    item_id: str


def _get_wiki_service():
    from agentnexus.wiki.wiki_service import WikiService
    return WikiService()


# ── Wiki Stats ──────────────────────────────────────────────────────

@router.get("/stats")
def wiki_stats(namespace: str = "default"):
    """Get wiki health statistics."""
    try:
        service = _get_wiki_service()
        return service.get_stats(namespace)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Wiki Pages ──────────────────────────────────────────────────────

@router.get("/pages")
def list_wiki_pages(namespace: str = "default", limit: int = 100):
    """List all wiki pages."""
    from agentnexus.wiki.store import get_wiki_store

    try:
        store = get_wiki_store()
        pages = store.list_pages(source_namespace=namespace, limit=limit)
        result_pages = []
        for p in pages:
            full_page = store.get_page(p.page_id, include_statements=True)
            stmt_count = len(full_page.statements) if full_page else 0
            result_pages.append({
                "page_id": p.page_id,
                "title": p.title,
                "page_type": p.page_type,
                "confidence": p.confidence,
                "statement_count": stmt_count,
                "source_namespace": p.source_namespace,
                "created_at": p.created_at,
                "updated_at": p.updated_at,
            })
        return {"pages": result_pages, "total": len(pages)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pages/{page_id}")
def get_wiki_page(page_id: str):
    """Get a single wiki page with all statements."""
    from agentnexus.wiki.store import get_wiki_store

    try:
        store = get_wiki_store()
        page = store.get_page(page_id)
        if not page:
            raise HTTPException(status_code=404, detail=f"Page {page_id} not found")
        return {
            "page_id": page.page_id,
            "title": page.title,
            "page_type": page.page_type,
            "content": page.content,
            "confidence": page.confidence,
            "flags": page.flags,
            "source_namespace": page.source_namespace,
            "statements": [
                {
                    "statement_id": s.statement_id,
                    "text": s.text,
                    "synthesis_level": s.synthesis_level,
                    "verified_synthesis_level": s.verified_synthesis_level,
                    "source_chunk_ids": s.source_chunk_ids,
                    "canonical_term": s.canonical_term,
                }
                for s in page.statements
            ],
            "canonical_definitions": {
                term: {
                    "definitions": [
                        {"text": d.text, "source_chunk_id": d.source_chunk_id, "confidence": d.confidence}
                        for d in cd.definitions
                    ],
                    "consensus": cd.consensus,
                    "divergence": cd.divergence,
                }
                for term, cd in page.canonical_definitions.items()
            },
            "created_at": page.created_at,
            "updated_at": page.updated_at,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/pages/{page_id}")
def delete_wiki_page(page_id: str):
    """Delete a wiki page."""
    from agentnexus.wiki.store import get_wiki_store

    try:
        store = get_wiki_store()
        store.delete_page(page_id)
        return {"status": "deleted", "page_id": page_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Wiki Query ──────────────────────────────────────────────────────

@router.post("/query")
def wiki_query(req: WikiQueryRequest):
    """Query the wiki with confidence-based routing."""
    try:
        service = _get_wiki_service()
        result = service.query(
            question=req.question,
            source_namespace=req.namespace,
            force_rag=req.force_rag,
        )
        return {
            "used_wiki": result.used_wiki,
            "decision": result.decision,
            "confidence": result.confidence,
            "answer": result.answer,
            "source_chunks": result.source_chunks,
            "disclaimer": result.disclaimer,
            "rag_results": result.rag_results,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Wiki Ingest ─────────────────────────────────────────────────────

@router.post("/ingest")
def wiki_ingest_text(req: WikiIngestRequest):
    """Ingest text content into the wiki."""
    try:
        service = _get_wiki_service()
        page = service.ingest_source(
            source_text=req.source_text,
            source_uri=req.source_uri,
            source_namespace=req.namespace,
            page_type=req.page_type,
        )
        return {
            "status": "ok",
            "page_id": page.page_id,
            "title": page.title,
            "statement_count": len(page.statements),
            "confidence": page.confidence,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest/file")
async def wiki_ingest_file(
    file: UploadFile = File(...),
    namespace: str = "default",
    page_type: str = "concept",
):
    """Ingest a file into the wiki."""
    import asyncio
    import tempfile
    from pathlib import Path

    suffix = Path(file.filename or "upload.txt").suffix
    content = await file.read()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    def _sync_ingest():
        text = Path(tmp_path).read_text(encoding="utf-8")
        service = _get_wiki_service()
        return service.ingest_source(
            source_text=text,
            source_uri=file.filename or "upload",
            source_namespace=namespace,
            page_type=page_type,
        )

    try:
        # Run sync ingestion in thread pool to avoid blocking the event loop
        page = await asyncio.to_thread(_sync_ingest)
        return {
            "status": "ok",
            "page_id": page.page_id,
            "title": page.title,
            "statement_count": len(page.statements),
            "confidence": page.confidence,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ── Wiki Lint ───────────────────────────────────────────────────────

@router.post("/lint")
def wiki_lint(namespace: str = "default"):
    """Run wiki health checks."""
    try:
        service = _get_wiki_service()
        items = service.run_lint(source_namespace=namespace)
        return {"items": items, "total": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Review Queue ────────────────────────────────────────────────────

@router.get("/review")
def list_review_items(status: str = "pending", limit: int = 50):
    """List review queue items."""
    from agentnexus.wiki.store import get_wiki_store

    try:
        store = get_wiki_store()
        items = store.list_review_items(status=status, limit=limit)
        return {
            "items": [
                {
                    "item_id": i.item_id,
                    "priority": i.priority,
                    "page_id": i.page_id,
                    "statement_id": i.statement_id,
                    "description": i.description,
                    "status": i.status,
                    "deadline": i.deadline,
                    "created_at": i.created_at,
                }
                for i in items
            ],
            "total": len(items),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/review/resolve")
def resolve_review_item(req: ReviewResolveRequest):
    """Resolve a review item."""
    from agentnexus.wiki.store import get_wiki_store

    try:
        store = get_wiki_store()
        store.resolve_review_item(req.item_id)
        return {"status": "resolved", "item_id": req.item_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/review/process")
def process_overdue_reviews():
    """Process overdue review items (auto-degradation)."""
    try:
        service = _get_wiki_service()
        actions = service.process_overdue_reviews()
        return {"actions": actions, "total": len(actions)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/backfill")
def wiki_backfill(namespace: str = "default"):
    """Rebuild wiki from scratch: delete all existing pages, then regenerate from RAG."""
    from agentnexus.rag.store import get_knowledge_base_catalog
    from agentnexus.wiki.store import get_wiki_store

    catalog = get_knowledge_base_catalog()
    wiki_store = get_wiki_store()

    kb = catalog.get_knowledge_base(namespace)
    if not kb:
        raise HTTPException(status_code=404, detail=f"No RAG knowledge base for namespace '{namespace}'")

    docs = catalog.list_documents(kb.kb_id)
    if not docs:
        return {"created": 0, "deleted": 0, "message": "No documents in RAG"}

    # Delete all existing wiki pages for this namespace
    existing_pages = wiki_store.list_pages(source_namespace=namespace)
    for p in existing_pages:
        wiki_store.delete_page(p.page_id)
    deleted = len(existing_pages)

    service = _get_wiki_service()
    created = 0
    errors = []

    for doc in docs:
        source_text = doc.raw_text or doc.indexed_text or doc.content
        if not source_text.strip():
            continue

        try:
            service.ingest_source(
                source_text=source_text,
                source_uri=doc.source_uri,
                source_namespace=namespace,
            )
            created += 1
        except Exception as e:
            errors.append({"source": doc.source_uri, "error": str(e)})

    return {"created": created, "deleted": deleted, "errors": errors}


# ── Calibration ─────────────────────────────────────────────────────

@router.get("/calibration")
def get_calibration_status():
    """Get latest calibration status."""
    from agentnexus.wiki.store import get_wiki_store

    try:
        store = get_wiki_store()
        calibration = store.get_latest_calibration()
        return {"calibration": calibration}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
