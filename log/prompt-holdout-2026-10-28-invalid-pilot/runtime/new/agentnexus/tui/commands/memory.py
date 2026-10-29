"""Memory management using the existing memory stores and reflection service."""

from . import COMMAND_HELP, split_action


def run(context, command, args, sid):
    """List, query and manage memories without changing the terminal layout."""
    from agentnexus.memory.long_term import get_long_term_memory

    action, rest = split_action(args)
    if action == "short":
        return context.runtime.chat.session_memory(sid).short_term.get_all()
    store = get_long_term_memory()
    if action == "list":
        limit = int(rest or 20)
        if not 1 <= limit <= 1000:
            raise ValueError("数量必须在 1 到 1000 之间")
        return store.list_recent(limit)
    if action == "search":
        from agentnexus.services.memories import search_memories

        return search_memories(store, rest)
    if action == "delete":
        store.delete(int(rest))
        return {"deleted": int(rest)}
    if action == "clear":
        store.clear_all()
        return {"status": "cleared"}
    if action == "reflect":
        from agentnexus.memory.reflection import run_reflection
        from agentnexus.rag.embeddings import get_embedding_model

        days = int(rest or 7)
        if not 1 <= days <= 365:
            raise ValueError("天数必须在 1 到 365 之间")
        return run_reflection(llm=context.runtime.llm, embed_model=get_embedding_model(), long_term=store,
                              session_id="reflection", days=days, max_memories=50)
    raise ValueError(f"用法: {command} {COMMAND_HELP[command]}")
