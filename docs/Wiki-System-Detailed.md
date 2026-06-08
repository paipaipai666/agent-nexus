> **[中文](Wiki-System-Detailed.md) | [English](Wiki-System-Detailed.en.md)**

# 📚 Wiki 知识系统（详细版）

## 概述

Wiki 系统实现了 **Karpathy 的 LLM Wiki 模式**——一种混合知识管理架构，将 RAG 检索的原始文档"编译"成结构化的 Wiki 页面，通过**机械验证**（非 LLM）确保每条陈述的可信度，并根据置信度自动路由查询：高置信度直接使用 Wiki，低置信度回退到 RAG 原始检索。

**核心创新**：
- **机械验证器**：用 Jaccard 相似度 + 余弦相似度验证 LLM 分配的合成级别，完全确定性、可复现
- **图传播引擎**：页面间的依赖关系形成有向图，置信度变化沿图传播（降级级联 + 恢复重验证）
- **校准系统**：人工标注样本 → 自动调整阈值 → 混淆矩阵评估

## 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        WikiService                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │ Ingestion│→│ Verifier │→│ Router   │→│ Store    │        │
│  │ (LLM)   │  │ (机械)   │  │ (规则树) │  │ (SQLite) │        │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │
│       │              │             │             │                │
│       ▼              ▼             ▼             ▼                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │ Propagation│ │ Linter   │  │Calibration│ │ ChromaDB │        │
│  │ Engine    │  │ (3 checks)│ │ (阈值调优)│ │ (向量索引)│        │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │
└─────────────────────────────────────────────────────────────────┘
         │                                              │
         ▼                                              ▼
┌──────────────────┐                    ┌──────────────────┐
│   RAG System     │◄──── fallback ────│  Query Router    │
│  (原始文档检索)   │                    │  (置信度路由)    │
└──────────────────┘                    └──────────────────┘
```

## 数据模型

**文件**：`agentnexus/wiki/models.py`

### 核心数据结构

#### WikiPage（Wiki 页面）

```python
@dataclass(slots=True)
class WikiPage:
    page_id: str                          # 唯一标识
    title: str                            # 页面标题
    page_type: str = "concept"            # entity | concept | overview | source_summary
    content: str = ""                     # 页面内容
    statements: list[WikiStatement]       # 陈述列表
    canonical_definitions: dict[str, CanonicalDefinition]  # 术语→规范定义
    confidence: str = "high"              # 置信度级别
    flags: list[str] = []                 # 传播标记，如 ["depends_on_degraded_page:xxx"]
    source_namespace: str = ""            # 绑定的 RAG 命名空间
    metadata: dict[str, Any]              # 元数据
    created_at: str = ""
    updated_at: str = ""
```

#### WikiStatement（陈述）

```python
@dataclass(slots=True)
class WikiStatement:
    statement_id: str                     # 唯一标识
    page_id: str                          # 所属页面
    text: str                             # 陈述文本
    synthesis_level: str = "synthesis"    # LLM 分配的合成级别
    source_chunk_ids: list[str]           # 来源 RAG chunk IDs
    canonical_term: str | None            # 关联的规范术语
    verified_synthesis_level: str | None  # 机械验证后的级别（None=未验证）
    metadata: dict[str, Any]
    created_at: str = ""
    updated_at: str = ""
```

#### CanonicalDefinition（规范定义）

```python
@dataclass(slots=True)
class CanonicalDefinition:
    definitions: list[DefinitionEntry]    # 多源定义列表
    consensus: str | None = None          # 共识定义（divergence >= 0.2 时为 None）
    divergence: float = 0.0               # 定义分歧度
    last_recalculated: str = ""           # 上次重算时间
```

### 枚举类型

#### SynthesisLevel（合成级别）

| 级别 | 含义 | 验证方法 |
|------|------|----------|
| `direct_quote` | 直接引用，与单个来源高度重叠 | Jaccard ≥ 0.6 |
| `paraphrase` | 改写，与单个来源语义相似 | Jaccard ≥ 0.4 且 Cosine ≥ 0.7 |
| `cross_reference` | 多源交叉引用，每个来源已验证相关 | 每个来源 Cosine ≥ 0.35 |
| `synthesis` | 无单一来源，跨文档综合结论 | 无验证（保持原级） |

#### ConfidenceLevel（置信度级别）

| 级别 | 含义 | 查询路由 |
|------|------|----------|
| `high` | 80%+ 陈述为 direct_quote/paraphrase | 直接使用 Wiki |
| `medium` | 50%+ 为高可信 | Wiki + 来源 chunks |
| `low` | 含 synthesis 陈述 | Wiki + 免责声明 |
| `untrusted` | 含不可信陈述 | 回退到 RAG |

#### QueryDecision（查询决策）

| 决策 | 行为 |
|------|------|
| `use_wiki` | 直接使用 Wiki 答案 |
| `use_wiki_with_sources` | 使用 Wiki 并附带来源 chunk IDs |
| `use_wiki_with_disclaimer` | 使用 Wiki 并附加免责声明 |
| `fallback_to_rag` | 回退到 RAG 原始检索 |

## 机械验证器 (MechanicalVerifier)

**文件**：`agentnexus/wiki/verifier.py`

### 设计原则

**零 LLM 调用**——所有验证完全确定性、可复现。使用两种相似度度量：

1. **Jaccard 相似度**（字符串重叠）：计算两个文本的 token 集合交集/并集比
2. **余弦相似度**（向量距离）：使用嵌入模型计算语义相似度

### 验证流程

```
输入: statement + chunk_texts
         │
         ▼
┌─────────────────────────────┐
│ 1. 检查 source_chunk_ids    │
│    无来源 → 返回 SYNTHESIS  │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ 2. 根据 assigned level 分支 │
└──────┬──────┬──────┬────────┘
       │      │      │
       ▼      ▼      ▼
   SINGLE  MULTI  SYNTHESIS
       │      │      │
       ▼      ▼      ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│ Jaccard  │ │ 逐chunk  │ │ 直接返回 │
│ → Cosine │ │ Cosine   │ │ SYNTHESIS│
│ 验证     │ │ 验证     │ │          │
└──────────┘ └──────────┘ └──────────┘
```

### 单源验证 (`_verify_single_source`)

```python
def _verify_single_source(self, statement_text: str, chunk_text: str) -> str:
    jac = jaccard_similarity(statement_text, chunk_text)
    if jac >= self.jaccard_direct_quote:        # ≥ 0.6 → DIRECT_QUOTE
        return "direct_quote"
    if jac >= self.jaccard_paraphrase:           # ≥ 0.4 → 检查 cosine
        cos = cosine_similarity(statement_text, chunk_text)
        if cos >= self.cosine_paraphrase:        # ≥ 0.7 → PARAPHRASE
            return "paraphrase"
        return "cross_reference"                 # Jaccard 高但 Cosine 低
    cos = cosine_similarity(statement_text, chunk_text)
    if cos >= self.cosine_paraphrase:            # Cosine 高 → PARAPHRASE
        return "paraphrase"
    return "synthesis"                           # 都低 → SYNTHESIS
```

### 多源验证 (`_verify_multi_source`)

```python
def _verify_multi_source(self, statement_text, source_chunk_ids, chunk_texts):
    valid_chunks = []
    for chunk_id in source_chunk_ids:
        cos = cosine_similarity(statement_text, chunk_texts[chunk_id])
        if cos >= self.cosine_source:            # ≥ 0.35
            valid_chunks.append(chunk_id)

    if len(valid_chunks) == 0:   return "synthesis"
    if len(valid_chunks) == 1:   return _verify_single_source(...)  # 降级为单源
    return "cross_reference"                     # 2+ 有效来源
```

### 中文分词

验证器内置了混合中英文分词器：
- 中文字符逐字拆分作为独立 token
- 英文单词保持整体
- 标点符号被剥离

```python
def _tokenize(text: str) -> set[str]:
    # "Hello 世界!" → {"hello", "世", "界"}
```

## 置信度路由器 (ConfidenceRouter)

**文件**：`agentnexus/wiki/confidence.py`

### 设计原则

**规则树，非公式**——每条规则独立可审计、可修改。当积累足够查询反馈数据后，可替换为学习模型。

### 规则树（按优先级，首次匹配即返回）

```
输入: WikiPage
         │
         ▼
┌─────────────────────────────────────┐
│ 规则 1: 有 UNTRUSTED 陈述？         │
│   是 → UNTRUSTED → FALLBACK_TO_RAG │
└─────────────┬───────────────────────┘
              │ 否
              ▼
┌─────────────────────────────────────┐
│ 规则 2: 80%+ 为 HIGH_TRUST？        │
│   (direct_quote + paraphrase)       │
│   是 → HIGH → USE_WIKI             │
└─────────────┬───────────────────────┘
              │ 否
              ▼
┌─────────────────────────────────────┐
│ 规则 3: 50%+ 为 HIGH_TRUST？        │
│   是 → MEDIUM → USE_WIKI_WITH_SOURCES│
└─────────────┬───────────────────────┘
              │ 否
              ▼
┌─────────────────────────────────────┐
│ 规则 4: 有 SYNTHESIS 陈述？         │
│   是 → LOW → USE_WIKI_WITH_DISCLAIMER│
└─────────────┬───────────────────────┘
              │ 否
              ▼
        MEDIUM（默认）
```

### 查询路由决策

```python
def route(self, page: WikiPage) -> QueryDecision:
    if page.confidence == "untrusted":  return FALLBACK_TO_RAG
    if page.confidence == "high":       return USE_WIKI
    if page.confidence == "medium":     return USE_WIKI_WITH_SOURCES
    return USE_WIKI_WITH_DISCLAIMER     # "low"
```

## 图传播引擎 (PropagationEngine)

**文件**：`agentnexus/wiki/propagation.py`

### 设计原则

- **降级级联**：页面置信度下降时，沿依赖图向下传播（最小继承）
- **恢复重验证**：页面置信度上升时，不自动恢复依赖页，而是重新验证
- **深度限制**：传播深度默认限制为 3，防止链式反应

### 降级传播

```python
def propagate_degradation(self, page_id: str, depth: int = 0):
    if depth >= self.max_depth: return
    dependents = self.store.list_dependents(page_id)
    for dep_id in dependents:
        dep_page = self.store.get_page(dep_id)
        new_confidence = self.router.min_confidence(dep_page.confidence, page.confidence)
        if new_confidence != dep_page.confidence:
            self.store.update_page_confidence(dep_id, new_confidence,
                flag=f"depends_on_degraded_page:{page_id}")
            self.propagate_degradation(dep_id, depth + 1)  # 递归
```

### 恢复传播

```python
def propagate_recovery(self, page_id: str, depth: int = 0):
    if depth >= self.max_depth: return
    dependents = self.store.list_dependents(page_id)
    for dep_id in dependents:
        self._reverify_page(dep_id)  # 重新验证，不自动恢复
        self.propagate_recovery(dep_id, depth + 1)
```

### RAG → Wiki 反向触发

当 RAG chunk 被更新时，所有引用该 chunk 的 Wiki 陈述都会被重新验证：

```python
def on_chunk_update(self, chunk_ids: list[str]):
    affected_statements = self.store.find_statements_by_chunks(chunk_ids)
    for stmt in affected_statements:
        new_level = self.verifier.verify_statement(stmt, chunk_texts)
        if new_level != old_level:
            self.store.update_statement_synthesis_level(stmt.statement_id, new_level)
            if self.router.is_degradation(old_level, new_level):
                self.propagate_degradation(stmt.page_id)  # 降级
            else:
                self.propagate_recovery(stmt.page_id)      # 恢复
```

## Lint 系统

**文件**：`agentnexus/wiki/lint.py`

### 三项检查

| 检查器 | 检查内容 | 优先级 | SLA |
|--------|----------|--------|-----|
| `ConsistencyChecker` | 不同页面对同一术语的规范定义是否矛盾 | P1 (定义冲突) | 可配置 |
| `DriftDetector` | 陈述是否偏离其规范定义 | P2 (语义漂移) | 可配置 |
| `CoverageChecker` | RAG chunk 是否被任何 Wiki 陈述引用 | P3 (覆盖缺口) | 可配置 |

### 一致性检查

```python
class ConsistencyChecker:
    def check(self, store, source_namespace) -> list[ReviewItem]:
        # 收集所有页面的规范定义
        term_pages = {}  # term → [(page_id, consensus)]
        for page in pages:
            for term, canon_def in page.canonical_definitions.items():
                term_pages.setdefault(term, []).append((page.page_id, canon_def.consensus))

        # 检查同一术语在不同页面的定义是否矛盾
        for term, definitions in term_pages.items():
            for i, j in combinations(range(len(definitions)), 2):
                sim = cosine_similarity(def_a, def_b)
                if sim < 0.4:  # 低相似度 = 潜在矛盾
                    items.append(ReviewItem(...))
```

### 自动降级

超时未处理的审查项会自动降级：
- **P1 超时** → 页面标记为 `untrusted`
- **P2 超时** → 回退到规范定义
- **P3 超时** → 归档

## 校准系统 (Calibration)

**文件**：`agentnexus/wiki/calibration.py`

### 设计原则

**一次性工程校准**，不是训练。使用人工标注样本调整验证器阈值。

### 校准流程

```
人工标注样本 → 评估当前阈值 → 分析混淆矩阵 → 建议调整 → 重新评估
     │                                                       │
     └──────────── 最多 3 轮，score < 0.1 提前停止 ──────────┘
```

### 混淆矩阵评估

```python
class ConfusionMatrix:
    def false_degradation_rate(self) -> float:
        """错误降级率：实际高可信但被预测为低可信"""

    def miss_rate(self) -> float:
        """漏检率：实际低可信但被预测为高可信"""
```

### 阈值调整规则

| 场景 | 调整 |
|------|------|
| direct_quote 被误判为 paraphrase > 30% | 降低 `jaccard_direct_quote` 0.1 |
| paraphrase 被降级为 cross_reference/synthesis > 30% | 降低 `cosine_paraphrase` 0.1 |
| cross_reference 被降级为 synthesis > 30% | 降低 `cosine_source` 0.05 |
| direct_quote 误报率 > 20% | 提高 `jaccard_direct_quote` 0.05 |

### 默认阈值

```python
DEFAULT_THRESHOLDS = {
    "jaccard_direct_quote": 0.6,
    "jaccard_paraphrase": 0.4,
    "cosine_paraphrase": 0.7,
    "cosine_source": 0.35,
}
```

## 存储层 (WikiStore)

**文件**：`agentnexus/wiki/store.py`

### 数据库表

| 表 | 用途 |
|------|------|
| `wiki_pages` | Wiki 页面 |
| `wiki_statements` | 陈述 |
| `wiki_canonical_definitions` | 规范定义 |
| `wiki_dependency_graph` | 依赖关系图 |
| `wiki_review_queue` | 审查队列 |
| `wiki_calibration` | 校准记录 |

### 线程安全

使用 `threading.RLock` 保护所有写操作，SQLite 使用 WAL 模式。

## WikiService 主服务

**文件**：`agentnexus/wiki/wiki_service.py`

### 查询流程

```python
def query(self, question, source_namespace, rag_namespace, force_rag):
    if force_rag:
        return self._rag_fallback(question, namespace)

    wiki_pages = self.search_wiki_pages(question, source_namespace)
    if not wiki_pages:
        return self._rag_fallback(question, namespace)

    best_page = wiki_pages[0]
    decision = self.router.route(best_page)

    if decision == FALLBACK_TO_RAG:
        return self._rag_fallback(question, namespace)

    # 根据 decision 构建结果
    result = WikiQueryResult(used_wiki=True, decision=decision, ...)
    if decision in (USE_WIKI_WITH_SOURCES, USE_WIKI_WITH_DISCLAIMER):
        result.source_chunks = self.router.get_source_chunks(best_page)
    if decision == USE_WIKI_WITH_DISCLAIMER:
        result.disclaimer = self.router.build_disclaimer(best_page)
    return result
```

### 摄入流程

```python
def ingest_source(self, source_text, source_uri, source_namespace, ...):
    page = self._generate_wiki_page(source_text, ...)  # LLM 生成
    self._verify_page_statements(page)                  # 机械验证
    page.confidence = self.router.compute_page_confidence(page)  # 计算置信度
    self.store.upsert_page(page)                        # 存储
    self._index_page_in_chroma(page)                    # ChromaDB 索引
    return page
```

## 数据流：完整查询流程

```
用户提问: "什么是 AgentNexus 的 ReAct 模式?"
         │
         ▼
┌─────────────────────────────────────┐
│ 1. WikiService.query()              │
│    source_namespace = "agentnexus"  │
└─────────────┬───────────────────────┘
              │
              ▼
┌─────────────────────────────────────┐
│ 2. search_wiki_pages()              │
│    ChromaDB 语义搜索 → 3 个候选页面  │
└─────────────┬───────────────────────┘
              │
              ▼
┌─────────────────────────────────────┐
│ 3. ConfidenceRouter.route()         │
│    页面置信度 = high                 │
│    决策 = USE_WIKI                  │
└─────────────┬───────────────────────┘
              │
              ▼
┌─────────────────────────────────────┐
│ 4. 返回 WikiQueryResult             │
│    answer = "ReAct 是一种..."       │
│    used_wiki = True                 │
│    confidence = "high"              │
└─────────────────────────────────────┘
```

## 数据流：摄入流程

```
源文档: "ReAct 模式详解.md"
         │
         ▼
┌─────────────────────────────────────┐
│ 1. WikiService.ingest_source()      │
│    LLM 生成 WikiPage               │
│    - 提取关键概念和实体              │
│    - 创建 statements + synthesis    │
│    - 定义 canonical_definitions     │
└─────────────┬───────────────────────┘
              │
              ▼
┌─────────────────────────────────────┐
│ 2. MechanicalVerifier 验证          │
│    对每个 statement:                 │
│    - Jaccard 相似度检查              │
│    - Cosine 相似度检查               │
│    - 修正 synthesis_level           │
└─────────────┬───────────────────────┘
              │
              ▼
┌─────────────────────────────────────┐
│ 3. ConfidenceRouter 计算置信度       │
│    80% high-trust → confidence=high │
└─────────────┬───────────────────────┘
              │
              ▼
┌─────────────────────────────────────┐
│ 4. WikiStore 存储                   │
│    - upsert_page()                  │
│    - upsert_statement()             │
│    - upsert_canonical_definition()  │
└─────────────┬───────────────────────┘
              │
              ▼
┌─────────────────────────────────────┐
│ 5. ChromaDB 索引                    │
│    - 语义搜索用的向量索引            │
└─────────────────────────────────────┘
```

## 与其他模块的关系

| 模块 | 关系 |
|------|------|
| `rag` | Wiki 从 RAG chunk 摄入来源，查询时可回退到 RAG |
| `storage` | 使用 ChromaDB 进行语义搜索 |
| `core/config` | 读取 Wiki 相关配置（阈值、SLA、命名空间等） |
| `rag/embeddings` | 使用嵌入模型计算余弦相似度 |
| `rag/store` | 通过 `KnowledgeBaseCatalog` 获取 RAG chunk 文本 |
| `cli` | 提供 `nexus wiki` 命令组 |
| `server` | 提供 `/api/wiki` REST 路由 |

## 配置项

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `wiki_enabled` | `False` | 是否启用 Wiki 系统 |
| `wiki_namespace` | `"wiki"` | Wiki 在 ChromaDB 中的命名空间 |
| `wiki_jaccard_direct_quote` | `0.6` | direct_quote 的 Jaccard 阈值 |
| `wiki_jaccard_paraphrase` | `0.4` | paraphrase 的 Jaccard 阈值 |
| `wiki_cosine_paraphrase` | `0.7` | paraphrase 的余弦阈值 |
| `wiki_cosine_source` | `0.35` | cross_reference 来源的余弦阈值 |
| `wiki_drift_threshold` | `0.5` | 语义漂移检测阈值 |
| `wiki_propagation_max_depth` | `3` | 传播最大深度 |
| `wiki_calibration_retrigger_pct` | `0.5` | Wiki 规模增长多少百分比后重新校准 |
| `wiki_review_sla_p1_days` | `7` | P1 审查 SLA（天） |
| `wiki_review_sla_p2_days` | `14` | P2 审查 SLA（天） |
| `wiki_review_sla_p3_days` | `30` | P3 审查 SLA（天） |
