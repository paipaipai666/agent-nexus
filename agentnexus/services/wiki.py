"""Wiki management operations shared by terminal and HTTP clients."""


def get_wiki_service(module_host=None):
    """Resolve the selected Wiki implementation for this application."""
    from agentnexus.core.modules import get_module_host

    return (module_host or get_module_host()).require("wiki")


def backfill_wiki(service, namespace: str):
    """Rebuild a namespace from its existing knowledge documents."""
    from agentnexus.rag.store import get_knowledge_base_catalog

    catalog = get_knowledge_base_catalog()
    kb = catalog.get_knowledge_base(namespace)
    if kb is None:
        raise ValueError(f"No RAG knowledge base for namespace '{namespace}'")
    documents = catalog.list_documents(kb.kb_id)
    if not documents:
        return {"created": 0, "deleted": 0, "message": "No documents in RAG"}
    pages = service.list_pages(source_namespace=namespace)
    for page in pages:
        service.delete_page(page.page_id)
    created, errors = 0, []
    for doc in documents:
        text = doc.raw_text or doc.indexed_text or doc.content
        if not text.strip():
            continue
        try:
            service.ingest_source(source_text=text, source_uri=doc.source_uri, source_namespace=namespace)
            created += 1
        except Exception as exc:
            errors.append({"source": doc.source_uri, "error": str(exc)})
    return {"created": created, "deleted": len(pages), "errors": errors}
