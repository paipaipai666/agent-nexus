"""AgentNexus 新包全功能验证"""
import os, sys
os.environ["AGENTNEXUS_HOME"] = os.path.join(os.path.dirname(__file__), ".test_agentnexus")

print("=" * 60 + "\nAgentNexus\n" + "=" * 60)
errors = []
def ok(n): print(f"  OK  {n}")
def err(n, e): print(f"  FAIL {n}: {e}"); errors.append(n)

# 1
try:
    from agentnexus.core.config import get_settings
    assert get_settings().llm_model_id
    ok("配置加载")
except Exception as e: err("配置加载", e)

# 2
try:
    from agentnexus.rag.chroma_client import delete_collection, insert_documents, search
    delete_collection()
    insert_documents(["Qdrant 是向量数据库", "LangGraph 是多智能体框架"])
    r = search("什么是多智能体", limit=1)
    assert r and "LangGraph" in r[0]["text"]
    ok("ChromaDB 存+查")
except Exception as e: err("ChromaDB 存+查", e)

# 3
try:
    from agentnexus.memory.short_term import ShortTermMemory
    stm = ShortTermMemory(); stm.append("user", "你好"); stm.append("assistant", "你好")
    assert len(stm.get_all()) == 2
    ok("短期记忆")
except Exception as e: err("短期记忆", e)

try:
    from agentnexus.memory.long_term import LongTermMemory
    ltm = LongTermMemory()
    ltm.save("test", "用户喜欢简洁回答", category="user_preference", importance=0.9)
    assert len(ltm.list_recent(3)) >= 1
    ok("长期记忆")
except Exception as e: err("长期记忆", e)

# 4
try:
    from agentnexus.rag.ingestion import clean_text, load_and_clean
    assert len(clean_text("这是一个  \n\n测试文档  \x00")) > 0
    ok("PDF/MD 加载清洗")
except Exception as e: err("PDF/MD 加载清洗", e)

# 5
try:
    from agentnexus.rag.chroma_client import delete_collection
    from agentnexus.rag.retriever import build_knowledge_base, search_knowledge_base
    delete_collection()
    build_knowledge_base(["Python 用于 AI", "Qdrant 向量库", "LangGraph 多智能体", "BM25 文本检索"], load_reranker=False)
    r = search_knowledge_base("检索用什么")
    assert "BM25" in r
    ok("混合检索")
except Exception as e: err("混合检索", e)

# 6
try:
    from agentnexus.tools.tool_executor import ToolExecutor
    te = ToolExecutor()
    te.registerTool("Echo", "回显", lambda x: f"ECHO:{x}")
    assert te.getTool("Echo")("hello") == "ECHO:hello"
    ok("ToolExecutor")
except Exception as e: err("ToolExecutor", e)

# 7
try:
    from agentnexus.cli import app
    ok("CLI 入口")
except Exception as e: err("CLI 入口", e)

print("\n" + "=" * 60)
if errors:
    print(f"FAIL {len(errors)}: {errors}")
    sys.exit(1)
print("PASS 7/7")
