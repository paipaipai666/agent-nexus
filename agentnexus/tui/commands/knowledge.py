"""Knowledge operations use the existing catalog, ingestion and search services."""

import json
import uuid
from dataclasses import asdict, is_dataclass

from . import COMMAND_HELP, argument_path, split_action


def run(context, command, args, sid):
    """Manage documents and inspect local retrieval models."""
    from agentnexus.core.config import get_settings
    from agentnexus.rag.store import get_knowledge_base_catalog

    action, rest = split_action(args)
    settings = get_settings()
    namespace = settings.rag_default_namespace
    catalog = get_knowledge_base_catalog()
    if action == "list":
        kb = catalog.get_knowledge_base(namespace)
        if kb is None:
            return {"documents": [], "total_chunks": 0}
        documents = [{**asdict(doc), "chunk_count": catalog.count_chunks(doc.document_id)} for doc in catalog.list_documents(kb.kb_id)]
        return {"documents": documents, "total_chunks": catalog.count_chunks_by_kb(kb.kb_id)}
    if action == "search":
        from agentnexus.rag.kb_service import search_kb

        options = json.loads(rest) if rest.startswith("{") else {"query": rest}
        options.setdefault("namespace", namespace)
        options.setdefault("top_k", 5)
        options.setdefault("view", "chunk")
        if not options.get("query") or not 1 <= options["top_k"] <= 100:
            raise ValueError("请输入检索文本，top_k 必须在 1 到 100 之间")
        results = search_kb(**options)
        return [asdict(item) if is_dataclass(item) else item for item in results]
    if action == "ingest":
        from agentnexus.rag.kb_service import ingest_one_document

        path = argument_path(rest)
        if not path.is_file():
            raise ValueError(f"文档不存在: {path}")
        run_id = f"ingest_{uuid.uuid4().hex[:12]}"
        context.last_ingestion[sid] = run_id
        _, ingestion = ingest_one_document(str(path), namespace=namespace,
                                           enable_contextual=settings.enable_contextual_retrieval,
                                           llm_client=context.runtime.llm, run_id=run_id, source_uri=str(path))
        return asdict(ingestion)
    if action == "progress":
        run_id = rest or context.last_ingestion.get(sid)
        ingestion = catalog.get_ingestion_run(run_id) if run_id else None
        if ingestion is None:
            raise ValueError("导入记录不存在，运行 /kb ingest 后可用 /kb progress 查看")
        return asdict(ingestion)
    if action == "delete":
        from agentnexus.rag.kb_service import delete_document_and_vectors

        return {"deleted": rest, "deleted_vectors": delete_document_and_vectors(namespace, rest)}
    if action == "models":
        from agentnexus.rag.model_assets import cached_snapshot, statuses

        embedding = cached_snapshot(settings.embedding_model)
        kb = catalog.get_knowledge_base(namespace)
        if kb is None or catalog.count_chunks_by_kb(kb.kb_id) == 0:
            index_state = "empty"
        else:
            index_state = "ready" if embedding is not None and kb.metadata.get("embedding_identity") == embedding.identity else "rebuild_required"
        return {"models": statuses(), "index_state": index_state}
    if action == "prepare":
        from agentnexus.rag.model_assets import start_embedding_prepare, start_reranker_prepare

        if rest in {"", "embedding"}:
            return start_embedding_prepare(namespace).to_dict()
        if rest == "reranker":
            return start_reranker_prepare().to_dict()
    if action == "deletions":
        return [asdict(item) if is_dataclass(item) else item for item in catalog.list_deletion_logs()]
    if action in {"retry", "rollback"}:
        from agentnexus.rag.kb_service import retry_failed_deletion, rollback_failed_deletion

        operation = retry_failed_deletion if action == "retry" else rollback_failed_deletion
        return operation(int(rest))
    raise ValueError(f"用法: {command} {COMMAND_HELP[command]}")
