

def test_config_loading():
    from agentnexus.core.config import get_settings

    assert get_settings().llm_model_id


def test_chromadb_store_and_search(temp_agentnexus_home):
    from agentnexus.storage.chroma import delete_collection, insert_documents, search

    delete_collection()
    insert_documents(["Qdrant 是向量数据库", "LangGraph 是多智能体框架"])
    results = search("什么是多智能体", limit=1)

    assert results
    assert "LangGraph" in results[0]["text"]


def test_short_term_memory():
    from agentnexus.memory.short_term import ShortTermMemory

    stm = ShortTermMemory()
    stm.append("user", "你好")
    stm.append("assistant", "你好")

    assert len(stm.get_all()) == 2


def test_long_term_memory(temp_agentnexus_home):
    from agentnexus.memory.long_term import get_long_term_memory

    ltm = get_long_term_memory()
    ltm.save("test", "用户喜欢简洁回答", category="user_preference", importance=0.9)

    assert len(ltm.list_recent(3)) >= 1


def test_ingestion_clean_text():
    from agentnexus.rag.ingestion import clean_text

    assert len(clean_text("这是一个  \n\n测试文档  \x00")) > 0


def test_hybrid_retrieval(temp_agentnexus_home):
    from agentnexus.rag.retriever import build_knowledge_base, search_knowledge_base
    from agentnexus.storage.chroma import delete_collection

    delete_collection()
    build_knowledge_base(
        ["Python 用于 AI", "Qdrant 向量库", "LangGraph 多智能体", "BM25 文本检索"],
        load_reranker=False,
    )

    assert "BM25" in search_knowledge_base("检索用什么")


def test_tool_executor():
    from agentnexus.tools.tool_executor import ToolExecutor

    te = ToolExecutor()
    te.registerTool("Echo", "回显", lambda x: f"ECHO:{x}")

    assert te.getTool("Echo")("hello") == "ECHO:hello"


def test_cli_entry():
    from agentnexus.cli import app

    assert app is not None
