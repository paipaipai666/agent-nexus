> **[中文](RAG-System.md) | [English](RAG-System.en.md)**

# 📚 RAG Retrieval System

## Retrieval Pipeline

```
User query
    │
    ▼
1. Query Enhancement
    ├── [Optional] Query rewrite: conversational→keyword (default on)
    ├── [Optional] Multi-query expansion: N=3 semantic variants (default on)
    └── [Optional] HyDE: hypothetical document embedding (default off)
    │
    ▼
2. Dual-path Retrieval
    ├── Dense: SentenceTransformer → ChromaDB HNSW cosine
    └── Sparse: jieba tokenization → BM25Okapi (in-memory, rebuilt per session)
    │
    ▼
3. RRF Fusion: score = Σ 1/(k + rank), k=60
    │
    ▼
4. Structure Score Boost
    ├── Code blocks (code-related keywords→+0.02)
    ├── List blocks (+0.015)
    └── Heading blocks (+0.01, depth-weighted)
    │
    ▼
5. [Optional] CrossEncoder Reranking: BGE-Reranker-v2-m3
    │
    ▼
6. Context Expansion: Include neighboring blocks of hits
```

## ChromaDB Dual Clients

> ⚠ Two independent `PersistentClient` instances pointing to the same persistence directory

| Purpose | Client Location | Collection Name | Caching |
|------|-----------|--------|------|
| RAG | `rag/chroma_client.py` | `"documents"` | Module-level singleton |
| LTM | `memory/long_term.py` | `"long_term_memories"` | Rebuilt each time |

## Document Ingestion

```
nexus kb add <path>
    │
    ▼
load_document(path) → loader selected by type
    PDF (PyMuPDF + OCR fallback)
    Markdown (by heading level)
    HTML (h1-h6)
    DOCX (XML paragraphs → Heading grouping)
    XLSX (each worksheet = section)
    JSON (recursive rendering)
    TXT (entire file)
    │
    ▼
chunk_structured_document()
    Strategy: FIXED / RECURSIVE / SEMANTIC
    Size: default 512 chars, overlap 50
    │
    ▼
[Optional] Contextual retrieval enhancement (LLM-generated context)
    │
    ▼
Persistence: SQLite catalog + ChromaDB upsert
```

Chunk metadata: `block_type`, `has_code`, `has_list`, `heading_depth`, `section_id` — used for filtering and structure boost.

## Query Enhancement

| Stage | Behavior | Default |
|------|------|------|
| Query rewrite | Conversational→keyword | On |
| Multi-query | N variants each retrieved then RRF fused | On (N=3) |
| HyDE | Hypothetical document encoding search (weight 0.8) | Off |
