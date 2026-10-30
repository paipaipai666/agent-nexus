"""Wiki browsing, ingestion, review and maintenance commands."""

import json
from dataclasses import asdict

from . import COMMAND_HELP, argument_path, split_action


def run(context, command, args, sid):
    """Use persisted wiki records and the existing WikiService."""
    from agentnexus.core.config import get_settings
    from agentnexus.services.wiki import get_wiki_service

    action, rest = split_action(args)
    namespace = get_settings().rag_default_namespace
    service = getattr(context.runtime, "wiki_service", None)
    if service is None:
        service = get_wiki_service(getattr(context.runtime, "modules", None))
    if action == "list":
        return [asdict(page) for page in service.list_pages(source_namespace=rest or namespace)]
    if action == "show":
        page = service.get_page(rest)
        if page is None:
            raise ValueError(f"Wiki 页面不存在: {rest}")
        return asdict(page)
    if action == "delete":
        if service.get_page(rest) is None:
            raise ValueError(f"Wiki 页面不存在: {rest}")
        service.delete_page(rest)
        return {"deleted": rest}
    if action == "reviews":
        return [asdict(item) for item in service.list_review_items(status=rest or "pending", limit=100)]
    if action == "resolve" and rest:
        service.resolve_review_item(rest)
        return {"status": "resolved", "item_id": rest}
    if action == "stats":
        return service.get_stats(rest or namespace)
    if action == "query":
        options = json.loads(rest) if rest.startswith("{") else {"question": rest}
        options.setdefault("source_namespace", namespace)
        if not options.get("question"):
            raise ValueError("请输入查询文本")
        return asdict(service.query(**options))
    if action in {"ingest", "text"}:
        if action == "ingest":
            path = argument_path(rest)
            source_text, source_uri = path.read_text(encoding="utf-8"), str(path)
        else:
            source_text, source_uri = rest, f"tui:{sid}"
        if not source_text.strip():
            raise ValueError("导入文本不能为空")
        return asdict(service.ingest_source(source_text=source_text, source_uri=source_uri, source_namespace=namespace))
    if action == "lint":
        return service.run_lint(source_namespace=rest or namespace)
    if action == "process":
        return service.process_overdue_reviews()
    if action == "backfill":
        return service.backfill(rest or namespace)
    raise ValueError(f"用法: {command} {COMMAND_HELP[command]}")
