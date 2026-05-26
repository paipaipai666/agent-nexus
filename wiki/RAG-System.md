# 📚 RAG 检索系统

## 检索流水线

```
用户查询
    │
    ▼
1. 查询增强
    ├── [可选] 查询重写: 口语化→关键词 (默认开)
    ├── [可选] 多查询扩展: N=3 语义变体 (默认开)
    └── [可选] HyDE: 假设文档嵌入 (默认关)
    │
    ▼
2. 双路检索
    ├── 稠密: SentenceTransformer → ChromaDB HNSW 余弦
    └── 稀疏: jieba 分词 → BM25Okapi (内存, 每会话重建)
    │
    ▼
3. RRF 融合: score = Σ 1/(k + rank), k=60
    │
    ▼
4. 结构分数提升
    ├── 代码块(含代码相关词→+0.02)
    ├── 列表块(+0.015)
    └── 标题块(+0.01, 深度折算)
    │
    ▼
5. [可选] CrossEncoder 重排序: BGE-Reranker-v2-m3
    │
    ▼
6. 上下文扩展: 取命中块相邻块
```

## ChromaDB 双客户端

> ⚠ 两个独立的 `PersistentClient` 指向同一持久化目录

| 用途 | 客户端位置 | 集合名 | 缓存 |
|------|-----------|--------|------|
| RAG | `rag/chroma_client.py` | `"documents"` | 模块级单例 |
| LTM | `memory/long_term.py` | `"long_term_memories"` | 每次都重建 |

## 文档摄取

```
nexus kb add <path>
    │
    ▼
load_document(path) → 按类型选择加载器
    PDF (PyMuPDF + OCR 回退)
    Markdown (按标题层级)
    HTML (h1-h6)
    DOCX (XML 段落 → Heading 分组)
    XLSX (每工作表 = 章节)
    JSON (递归渲染)
    TXT (整文件)
    │
    ▼
chunk_structured_document()
    策略: FIXED / RECURSIVE / SEMANTIC
    大小: 默认 512 字符, 重叠 50
    │
    ▼
[可选] 上下文检索增强 (LLM 生成上下文)
    │
    ▼
持久化: SQLite 目录 + ChromaDB upsert
```

块元数据：`block_type`, `has_code`, `has_list`, `heading_depth`, `section_id` — 用于过滤和结构提升。

## 查询增强

| 阶段 | 行为 | 默认 |
|------|------|------|
| 查询重写 | 口语化→关键词 | 开 |
| 多查询 | N 个变体分别检索后 RRF 融合 | 开 (N=3) |
| HyDE | 假设文档编码搜索 (权重 0.8) | 关 |
