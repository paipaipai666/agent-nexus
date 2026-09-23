> **[中文](Storage-Detailed.md) | [English](Storage-Detailed.en.md)**

# 💾 Storage 模块（详细版）

## 概述

- **Storage** 模块：存储抽象层，封装 ChromaDB 向量存储和 SQLite 关系型存储

## Storage 模块

### 核心文件

| 文件 | 职责 |
| --- | --- |
| `chroma.py` | ChromaDB 向量存储客户端 |

### ChromaDB 客户端

```python
def insert_documents(texts, metadatas, ids, namespace)
def upsert_documents(texts, metadatas, ids, namespace)
def search(query, limit, namespace) -> list[SearchResult]
def delete(ids, namespace)
def resolve_collection_name(namespace) -> str
```

### 命名空间隔离

| 命名空间 | 用途 |
| --- | --- |
| `default` | 默认知识库 |
| `wiki` | Wiki 系统 |
| `memory` | 长期记忆 |
| 自定义 | 用户创建的知识库 |

## 模块依赖关系

```
Storage (storage/)
    └── ChromaDB Client (chroma.py)
         └── chromadb 库
```
