"""双路由检索评估：对比 Grep / RAG / 双路由三种策略"""
import time
import json
from pathlib import Path

from agentnexus.rag.chroma_client import delete_collection, insert_documents
from agentnexus.rag.retriever import build_knowledge_base, search_knowledge_base
from agentnexus.rag.grep_search import grep_search, grep_available
from agentnexus.rag.router import retrieve, is_code_query


TEST_DOCS = [
    "# config.yaml\nllm_model_id: deepseek-v4-flash\nllm_base_url: https://api.deepseek.com",
    "# Dockerfile\nFROM python:3.13\nRUN pip install agentnexus\nCMD [\"nexus\", \"run\"]",
    "def fibonacci(n):\n    if n <= 1: return n\n    return fibonacci(n-1) + fibonacci(n-2)",
    "Qdrant 是一个高性能向量数据库，使用 HNSW 索引加速最近邻搜索。",
    "RAG 检索增强生成结合了信息检索和大语言模型，先从知识库检索相关文档再生成回答。",
    "Python 是一种广泛使用的编程语言，以其简洁的语法和丰富的生态系统闻名。",
    "LangGraph 是一个用于构建有状态多智能体应用的框架，通过 StateGraph 显式控制 Agent 流程。",
    "混合检索将稠密向量检索和 BM25 稀疏检索的结果进行融合，取长补短提升召回率和准确率。",
]

TEST_QUERIES = [
    ("代码查询", "def fibonacci", ["fibonacci"], "grep_win"),
    ("代码查询", "Dockerfile pip install", ["Dockerfile", "pip install"], "grep_win"),
    ("自然语言", "什么是 RAG", ["RAG", "检索"], "rag_win"),
    ("自然语言", "多智能体框架", ["LangGraph", "Agent"], "rag_win"),
    ("混合查询", "向量数据库", ["Qdrant", "向量数据库"], "either"),
    ("混合查询", "Python 编程语言", ["Python", "编程"], "either"),
]


def evaluate_strategy(name, search_fn, queries):
    total = len(queries)
    hits = 0
    latencies = []
    for label, query, keywords, _ in queries:
        t0 = time.perf_counter()
        results = search_fn(query)
        latencies.append((time.perf_counter() - t0) * 1000)
        text = " ".join(str(r) for r in results if isinstance(r, dict))
        if any(kw in text for kw in keywords):
            hits += 1
    return {
        "name": name,
        "hit_rate": hits / total if total else 0,
        "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0,
    }


def grep_strategy(query):
    return grep_search(query, root_dir=".", top_k=5)


def rag_strategy(query):
    return [{"text": search_knowledge_base(query), "source": "rag"}]


def dual_strategy(query):
    if is_code_query(query):
        r = grep_search(query, root_dir=".", top_k=5)
        if len(r) >= 2:
            return r
    return [{"text": search_knowledge_base(query), "source": "dual"}]


if __name__ == "__main__":
    print("=" * 60)
    print("双路由检索评估: Grep vs RAG vs Dual-Route")
    print("=" * 60)

    print("\n[1] 构建知识库")
    delete_collection()
    build_knowledge_base(TEST_DOCS, load_reranker=False)
    print(f"    ChromaDB: {len(TEST_DOCS)} 篇文档")

    has_rg = grep_available()
    print(f"    ripgrep: {'可用' if has_rg else '不可用'}")

    print("\n[2] 运行评估")
    strategies = [
        ("Grep Only", grep_strategy, has_rg),
        ("RAG Only", rag_strategy, True),
        ("Dual Route", dual_strategy, has_rg),
    ]

    results = []
    for name, fn, enabled in strategies:
        if not enabled:
            print(f"    {name}: 跳过 (rg 不可用)")
            continue
        r = evaluate_strategy(name, fn, TEST_QUERIES)
        results.append(r)
        print(f"    {name}: 命中率={r['hit_rate']:.0%}  延迟={r['avg_latency_ms']:.0f}ms")

    print("\n[3] 分场景命中率")
    for name, fn, enabled in strategies:
        if not enabled:
            continue
        for scenario in ["代码查询", "自然语言", "混合查询"]:
            sub = [(l, q, k, e) for l, q, k, e in TEST_QUERIES if l == scenario]
            if sub:
                r = evaluate_strategy(name, fn, sub)
                print(f"    {name:<12} {scenario:<6} 命中率={r['hit_rate']:.0%}")

    print("\n" + "=" * 60)
    print("[4] 结论")

    if results:
        best = max(results, key=lambda r: r["hit_rate"])
        fastest = min(results, key=lambda r: r["avg_latency_ms"])
        print(f"    最佳命中率: {best['name']} ({best['hit_rate']:.0%})")
        print(f"    最低延迟:   {fastest['name']} ({fastest['avg_latency_ms']:.0f}ms)")

    print("\n    双路由优势:")
    print("    - 代码/配置查询 → Grep 精确匹配 (零 embedding 开销)")
    print("    - 自然语言问题  → RAG 语义检索 (理解措辞差异)")
    print("    - 命中不足时    → fallback 到另一路径 (保证召回)")

    with open("eval_routes.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n结果已保存到 eval_routes.json")
