> **[中文](Memory-RAG-Detailed.md) | [English](Memory-RAG-Detailed.en.md)**

# 🧠 Memory + RAG 模块（详细版）

## 概述

- **Memory** 模块：管理代理的短期记忆 (STM)、长期记忆 (LTM)、会话版本控制、上下文压缩、反射、卸载、投影和提取
- **RAG** 模块：实现检索增强生成，包括文档摄入、分块、嵌入、检索、重排和评估

## Memory 模块架构

```
用户消息
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│                    MemoryManager                             │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ ShortTerm│  │ LongTerm │  │ Version  │  │ Todo     │   │
│  │ Memory   │  │ Memory   │  │ Manager  │  │ List     │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│       │              │             │             │           │
│       ▼              ▼             ▼             ▼           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │Compaction│  │Extraction│  │Projection│  │Offload   │   │
│  │ 上下文压缩│  │ 记忆提取 │  │ 投影裁剪 │  │ 大结果卸载│   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│       │                                                       │
│       ▼                                                       │
│  ┌──────────┐                                                │
│  │Reflection│  自我反思，优化记忆策略                        │
│  └──────────┘                                                │
└─────────────────────────────────────────────────────────────┘
```

### 核心类：MemoryManager

```python
class MemoryManager:
    def __init__(self, session_id, llm=None, enable_long_term=True)
    def add_message(role, content)         # 添加消息到 STM
    def search_memory(query) -> list       # 搜索 LTM
    def save_to_ltm(content, category)     # 保存到 LTM
    def compact()                          # 压缩上下文
    def build_projection(messages) -> list # 构建投影消息
```

### 记忆子系统

| 文件 | 职责 |
| --- | --- |
| `short_term.py` | 短期记忆（当前会话消息列表 + 重要性评分） |
| `long_term.py` | 长期记忆（ChromaDB 向量存储，支持 7 种类别） |
| `versioned.py` | 会话版本控制（undo/redo 检查点） |
| `compaction.py` | 上下文压缩（LLM 摘要 + 工具结果裁剪） |
| `extraction.py` | 记忆提取（从对话中自动提取有价值的观察） |
| `projection.py` | 投影裁剪（构建 LLM 输入时的消息投影） |
| `offload.py` | 大结果卸载（将超大工具结果写入磁盘） |
| `reflection.py` | 自我反思（优化记忆策略） |
| `todo.py` | 待办事项管理（SQLite 持久化） |

### 长期记忆类别

| 类别 | 说明 |
| --- | --- |
| `user_preference` | 用户偏好 |
| `entity_fact` | 实体事实 |
| `conclusion` | 结论 |
| `conversation` | 对话历史 |
| `task_progress` | 任务进展 |
| `error_pattern` | 错误模式 |
| `tool_preference` | 工具偏好 |

### 上下文压缩策略

```
原始消息列表
    │
    ├── 工具结果裁剪 (microcompact)
    │       └── 移除冗余工具输出，保留摘要
    │
    ├── LLM 摘要压缩 (compact)
    │       └── 使用 LLM 将旧消息压缩为摘要
    │
    └── 投影裁剪 (projection)
            └── 根据 max_ctx 窗口裁剪消息列表
```

### 会话版本控制

```python
class ConversationVersionManager:
    def checkpoint stm, ltm)           # 创建检查点
    def undo() -> bool                  # 回退到上一个检查点
    def redo() -> bool                  # 重做到下一个检查点
    def get_head_stm() -> str           # 获取当前 STM 快照
```

## RAG 模块架构

```
源文档 (PDF/MD/TXT/HTML/JSON/DOCX/XLSX)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│                    Ingestion Pipeline                         │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ Loaders  │→│ Chunking │→│Embedding │→│ ChromaDB │   │
│  │ 文档加载 │  │ 智能分块 │  │ 向量嵌入 │  │ 向量存储 │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
└─────────────────────────────────────────────────────────────┘

查询流程:
    │
    ▼
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ Query    │→│ Rewrite  │→│ Hybrid   │→│ Rerank   │
│ 查询入口 │  │ 查询改写 │  │ 混合检索 │  │ 重排序   │
└──────────┘  └──────────┘  └──────────┘  └──────────┘
```

### 文档加载器

| 加载器 | 支持格式 |
| --- | --- |
| `markdown.py` | Markdown (.md) |
| `text.py` | 纯文本 (.txt) |
| `pdf.py` | PDF |
| `html.py` | HTML |
| `json_loader.py` | JSON |
| `office.py` | DOCX, XLSX |

### 检索策略

| 策略 | 说明 |
| --- | --- |
| 语义检索 | ChromaDB 向量相似度搜索 |
| 关键词检索 | BM25 稀疏检索 |
| 混合检索 | Reciprocal Rank Fusion (RRF) 融合 |
| 查询改写 | LLM 改写查询以提高召回率 |
| 多查询 | 生成多个查询变体 |
| HyDE | 生成假设性文档作为查询 |
| 上下文扩展 | 扩展检索结果的上下文窗口 |

### 重排序

| 方法 | 说明 |
| --- | --- |
| BGE-Reranker | 交叉编码器重排序 |
| 语义相似度 | 嵌入向量余弦相似度 |

## 模块依赖关系

```
MemoryManager (memory/manager.py)
    ├── ShortTermMemory (short_term.py)
    ├── LongTermMemory (long_term.py)
    │       └── ChromaDB (storage/chroma.py)
    ├── ConversationVersionManager (versioned.py)
    ├── Compaction (compaction.py)
    │       └── AgentLLM (core/llm.py)
    ├── Extraction (extraction.py)
    │       └── AgentLLM
    ├── Projection (projection.py)
    ├── Offload (offload.py)
    └── Reflection (reflection.py)

RAG Retriever (rag/retriever.py)
    ├── Embeddings (rag/embeddings.py)
    │       └── SentenceTransformers
    ├── BM25Index (rag/ranking.py)
    ├── ChromaDB (storage/chroma.py)
    ├── Loaders (rag/loaders/)
    ├── Chunking (rag/chunking.py)
    └── Reranker (rag/ranking.py)
            └── CrossEncoder
```
