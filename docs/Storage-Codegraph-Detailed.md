> **[中文](Storage-Codegraph-Detailed.md) | [English](Storage-Codegraph-Detailed.en.md)**

# 💾 Storage + CodeGraph 模块（详细版）

## 概述

- **Storage** 模块：存储抽象层，封装 ChromaDB 向量存储和 SQLite 关系型存储
- **CodeGraph** 模块：代码知识图谱，基于 AST 解析的语义搜索

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

## CodeGraph 模块

### 架构

```
源代码文件
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│                    AST Parser                                │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ Python   │  │TypeScript│  │ Go       │  │ 更多...  │   │
│  │ 解析器   │  │ 解析器   │  │ 解析器   │  │          │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│                    CodeGraph Store                           │
│                                                              │
│  实体 (函数/类/方法) + 关系 (调用/继承/导入)                 │
│  存储: SQLite                                                │
│  索引: 语义搜索 (ChromaDB)                                   │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Query API                                 │
│                                                              │
│  - search: 语义搜索代码实体                                  │
│  - callers: 查找调用者                                       │
│  - callees: 查找调用目标                                     │
│  - inherits: 查看继承树                                      │
│  - imports: 查看导入关系                                     │
│  - context: 获取实体完整上下文                               │
└─────────────────────────────────────────────────────────────┘
```

### 核心文件

| 文件 | 职责 |
| --- | --- |
| `parsers/` | AST 解析器（Python, TypeScript, Go 等） |
| `store.py` | 图谱存储（SQLite） |
| `queries.py` | 查询 API |
| `updater.py` | 图谱构建和更新 |

### 实体类型

| 类型 | 说明 |
| --- | --- |
| `function` | 函数 |
| `class` | 类 |
| `method` | 方法 |
| `module` | 模块 |
| `variable` | 变量 |

### 关系类型

| 类型 | 说明 |
| --- | --- |
| `calls` | 调用关系 |
| `inherits` | 继承关系 |
| `imports` | 导入关系 |
| `defines` | 定义关系 |
| `references` | 引用关系 |

### 查询 API

```python
def search(query, kind=None, limit=10) -> list[Entity]
def callers(symbol, depth=1) -> list[Entity]
def callees(symbol, depth=1) -> list[Entity]
def inherits(cls) -> list[Entity]
def imports(module) -> list[Entity]
def context(symbol) -> EntityContext
def stats() -> dict
def verify(fix=False) -> list[Issue]
```

## 模块依赖关系

```
Storage (storage/)
    └── ChromaDB Client (chroma.py)
         └── chromadb 库

CodeGraph (codegraph/)
    ├── Parsers (parsers/)
    │       ├── Python (AST)
    │       ├── TypeScript (AST)
    │       └── Go (AST)
    ├── Store (store.py)
    │       └── SQLite
    ├── Queries (queries.py)
    ├── Updater (updater.py)
    └── Embeddings (rag/embeddings.py)  # 语义搜索用
```
