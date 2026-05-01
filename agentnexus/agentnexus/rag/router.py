"""双路由检索：根据查询特征自动选择 Grep 或 RAG"""
from .grep_search import grep_search, grep_available
from .chroma_client import search as chroma_search
from .retriever import search_knowledge_base as rag_search, _get_retriever


def is_code_query(query: str) -> bool:
    indicators = [
        "代码", "函数", "class", "def ", "import ",
        "配置文件", "config", "yaml", "json", "toml",
        "日志", "log", "error", "traceback",
        "报错", "bug", "异常",
    ]
    return any(ind in query.lower() for ind in indicators)


def retrieve(query: str, kb_root: str = ".", top_k: int = 5) -> list[dict]:
    has_grep = grep_available()
    use_grep_first = is_code_query(query) and has_grep

    if use_grep_first:
        results = grep_search(query, root_dir=kb_root, top_k=top_k)
        if len(results) >= max(1, top_k // 2):
            return results
        # fallback to RAG
        try:
            rag_text = rag_search(query)
            return [{"text": rag_text, "source": "rag_fallback"}]
        except Exception:
            pass

    # RAG path
    try:
        rag_text = rag_search(query)
        if "知识库为空" in rag_text or "未找到" in rag_text:
            return []
        return [{"text": rag_text, "source": "rag"}]
    except Exception:
        return []
