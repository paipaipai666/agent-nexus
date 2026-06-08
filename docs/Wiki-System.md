> **[中文](Wiki-System.md) | [English](Wiki-System.en.md)**

# Wiki 系统

混合 Wiki + RAG 知识管理系统，实现 Karpathy 的 LLM Wiki 模式。通过机械验证确保 Wiki 内容与源文档的一致性，基于置信度路由决定查询响应策略。

## 系统架构

```
用户查询 / 文档摄取
    │
    ├── WikiService（编排层）
    │     ├── ingest_source() ──── 摄取源文档 → WikiPage
    │     │     ├── LLM 生成页面（statements + canonical definitions）
    │     │     ├── MechanicalVerifier 机械验证所有 statements
    │     │     ├── ConfidenceRouter 计算页面置信度
    │     │     ├── 存储到 SQLite（wiki_pages, wiki_statements, ...）
    │     │     └── 索引到 ChromaDB（语义搜索）
    │     │
    │     ├── query() ──── 置信度路由查询
    │     │     ├── ChromaDB 搜索匹配 Wiki 页面
    │     │     ├── ConfidenceRouter.route() 决定响应策略
    │     │     └── 四种决策：use_wiki / with_sources / with_disclaimer / RAG fallback
    │     │
    │     ├── run_lint() ──── 健康检查
    │     │     ├── ConsistencyChecker（定义冲突检测）
    │     │     ├── DriftDetector（语义漂移检测）
    │     │     └── CoverageChecker（覆盖率检测）
    │     │
    │     └── calibrate() ──── 阈值校准
    │
    ├── MechanicalVerifier（验证层）
    │     ├── jaccard_similarity() ──── 字符串重叠度（快速筛选）
    │     ├── cosine_similarity() ──── 语义相似度（嵌入向量）
    │     └── verify_statement() ──── 验证并修正 LLM 标注的 synthesis_level
    │
    ├── ConfidenceRouter（路由层）
    │     ├── compute_page_confidence() ──── 规则树计算页面置信度
    │     └── route() ──── 根据置信度决定查询路由
    │
    ├── PropagationEngine（传播层）
    │     ├── propagate_degradation() ──── 降级级联（min 继承）
    │     ├── propagate_recovery() ──── 恢复触发（重新验证，不自动恢复）
    │     └── on_chunk_update() ──── RAG → Wiki 反向触发
    │
    ├── WikiLinter（审查层）
    │     ├── ConsistencyChecker ──── 定义冲突检测
    │     ├── DriftDetector ──── 语义漂移检测
    │     └── CoverageChecker ──── 覆盖率检测
    │
    └── WikiStore（存储层）── SQLite（与 RAG catalog 共享数据库）
```

## 核心概念

### WikiPage

Wiki 页面，包含 statements 和 canonical definitions。

| 字段 | 类型 | 说明 |
|------|------|------|
| `page_id` | str | 唯一标识（`page_{uuid}`） |
| `title` | str | 页面标题 |
| `page_type` | str | 类型：`entity` / `concept` / `overview` / `source_summary` |
| `content` | str | 页面正文内容 |
| `statements` | list[WikiStatement] | 页面包含的声明列表 |
| `canonical_definitions` | dict[str, CanonicalDefinition] | 术语的规范化定义 |
| `confidence` | str | 页面级置信度：`high` / `medium` / `low` / `untrusted` |
| `flags` | list[str] | 传播标记，如 `depends_on_degraded_page:xxx` |
| `source_namespace` | str | 绑定的 RAG 命名空间 |

### WikiStatement

Wiki 页面中的单个声明/断言。

| 字段 | 类型 | 说明 |
|------|------|------|
| `statement_id` | str | 唯一标识（`stmt_{uuid}`） |
| `page_id` | str | 所属页面 ID |
| `text` | str | 声明文本 |
| `synthesis_level` | str | LLM 标注的合成级别（预验证） |
| `source_chunk_ids` | list[str] | 关联的 RAG chunk IDs |
| `canonical_term` | str \| None | 关联的规范术语 |
| `verified_synthesis_level` | str \| None | 机械验证后的合成级别（None = 未验证） |

### CanonicalDefinition

术语的多源规范化定义。

| 字段 | 类型 | 说明 |
|------|------|------|
| `definitions` | list[DefinitionEntry] | 来自不同源的定义列表 |
| `consensus` | str \| None | 共识定义（divergence >= 0.2 时为 None） |
| `divergence` | float | 定义间的分歧度 |
| `last_recalculated` | str | 最近重算时间 |

### SynthesisLevel（合成级别）

描述 Wiki 声明与其源 chunk 的关系。

| 级别 | 信任排名 | 说明 |
|------|----------|------|
| `direct_quote` | 3 | 与单个源 chunk 高 Jaccard 重叠（直接引用） |
| `paraphrase` | 2 | 与单个源 chunk 高余弦相似度（转述） |
| `cross_reference` | 1 | 多个源 chunk，每个均验证相关 |
| `synthesis` | 0 | 无单一源；跨文档综合结论 |

### ConfidenceLevel（置信度）

页面级和声明级的置信度等级。

| 等级 | 路由决策 | 说明 |
|------|----------|------|
| `high` | `use_wiki` | 80%+ 声明为 direct_quote 或 paraphrase |
| `medium` | `use_wiki_with_sources` | 50%+ 声明为高信任级别 |
| `low` | `use_wiki_with_disclaimer` | 包含 synthesis 声明 |
| `untrusted` | `fallback_to_rag` | 任一声明为 untrusted |

### QueryDecision（查询决策）

置信度路由器对查询的决策。

| 决策 | 行为 |
|------|------|
| `use_wiki` | 直接使用 Wiki 答案 |
| `use_wiki_with_sources` | 使用 Wiki 答案 + 附带源 chunk 引用 |
| `use_wiki_with_disclaimer` | 使用 Wiki 答案 + 附加免责声明 |
| `fallback_to_rag` | 退回到纯 RAG 搜索 |

## 机械验证流程

`MechanicalVerifier` 通过确定性检查验证 LLM 标注的 synthesis_level，不依赖 LLM 调用。

```
WikiStatement（LLM 标注）
    │
    ├── 无 source_chunk_ids → 返回 synthesis
    │
    ├── direct_quote / paraphrase（单源验证）
    │     ├── Jaccard >= jaccard_direct_quote (0.6) → direct_quote
    │     ├── Jaccard >= jaccard_paraphrase (0.4)
    │     │     └── cosine >= cosine_paraphrase (0.7) → paraphrase
    │     │     └── cosine < 0.7 → cross_reference（共享词汇但语义不同）
    │     ├── Jaccard < 0.4, cosine >= cosine_paraphrase → paraphrase
    │     └── 两者都低 → synthesis
    │
    ├── cross_reference（多源验证）
    │     ├── 对每个 source chunk 计算 cosine similarity
    │     ├── cosine >= cosine_source (0.35) → 保留该 chunk
    │     ├── 0 个有效 chunk → synthesis
    │     ├── 1 个有效 chunk → 重新走单源验证流程
    │     └── 2+ 个有效 chunk → cross_reference
    │
    └── synthesis → 直接返回 synthesis（无需验证）
```

**重要**：余弦相似度阈值针对特定嵌入模型校准（默认 BAAI/bge-small-zh-v1.5）。更换嵌入模型需重新运行校准。

## 置信度路由

`ConfidenceRouter` 使用规则树（非公式），每条规则可独立审计。

### 页面置信度计算规则

按顺序评估，首条匹配即返回：

1. 任一声明为 untrusted → 页面 untrusted
2. 80%+ 声明为 direct_quote 或 paraphrase → high
3. 50%+ 声明为高信任 → medium
4. 包含 synthesis 声明 → low
5. 默认 → medium

### 查询路由规则

| 页面置信度 | 路由决策 |
|-----------|----------|
| `untrusted` | `fallback_to_rag` |
| `high` | `use_wiki` |
| `medium` | `use_wiki_with_sources` |
| `low` | `use_wiki_with_disclaimer` |

### 免责声明生成

当路由决策为 `use_wiki_with_disclaimer` 时，自动生成声明：

> "This answer is based on synthesized wiki content. X/Y statements are cross-document syntheses without direct source verification. Use 'nexus wiki query --rag-fallback' for source-grounded answers."

## 信任传播

`PropagationEngine` 管理 Wiki 页面间的信任传播，传播深度限制为 3 层。

### 降级传播

当页面置信度下降时，级联到依赖页面。使用 min 继承：`dependent_confidence = min(own, source)`。

```
源页面（降级）
    ├── 依赖页面 A → min(conf_A, conf_source) → 更新置信度 + 添加 flag
    │     ├── A 的依赖页面 → 递归（depth < max_depth）
    │     └── ...
    └── 依赖页面 B → ...
```

### 恢复传播

当页面置信度上升时，触发重新验证（不自动恢复）。依赖页面可能在降级期间积累了自身问题。

### RAG → Wiki 反向触发

当 RAG chunk 更新时，自动重新验证引用这些 chunk 的所有 Wiki 声明。

```
RAG chunk 更新
    ├── find_statements_by_chunks() → 找到受影响的声明
    ├── 逐条重新验证
    ├── 检测退化/恢复方向
    │     ├── 退化 → propagate_degradation()
    │     └── 恢复 → propagate_recovery()
    └── 重算受影响页面的置信度
```

## Lint 与审查队列

WikiLinter 运行三项健康检查，发现问题后生成 ReviewItem 进入审查队列。

### 检查类型

| 检查 | 优先级 | SLA | 说明 |
|------|--------|-----|------|
| 一致性（Consistency） | P1 | 可配置天数 | 检测不同页面中同一术语的定义冲突（cosine < 0.4） |
| 语义漂移（Drift） | P2 | 可配置天数 | 检测声明偏离其规范定义（cosine < 阈值） |
| 覆盖率（Coverage） | P3 | 可配置天数 | 检测未被任何 Wiki 声明引用的 RAG chunk |

### ReviewItem 生命周期

```
创建（pending）
    ├── 手动解决 → resolved
    └── 超期未处理 → auto_degraded
         ├── P1（定义冲突）→ 页面标记为 untrusted
         ├── P2（语义漂移）→ 回退到规范定义
         └── P3（覆盖率）→ 归档
```

### 审查队列查询

审查队列按优先级排序（P1 > P2 > P3），同优先级按创建时间排序。

## 校准工作流

`calibration.py` 实现阈值校准，用于调整 MechanicalVerifier 的阈值参数。

### 校准流程

1. 收集人工标注样本（`CalibrationSample`）
2. 运行评估（`evaluate_thresholds`）生成混淆矩阵
3. 分析混淆矩阵，建议阈值调整
4. 重复最多 3 轮，直到分数 < 0.1 或达到最大轮数
5. 保存最佳阈值和混淆矩阵到数据库

### 校准指标

| 指标 | 说明 |
|------|------|
| `false_degradation_rate` | 错误降级率：高信任级别被错误降级 |
| `miss_rate` | 遗漏率：应降级但未降级 |

### 重新校准触发条件

Wiki 页面数增长超过上次校准样本数的配置百分比时，建议重新校准。

## RAG 集成

Wiki 系统与 RAG 系统深度集成：

| 集成点 | 方向 | 说明 |
|--------|------|------|
| 源文档 → Wiki | RAG → Wiki | RAG 摄取文档后触发 Wiki 页面生成 |
| Wiki 查询 → RAG | Wiki → RAG | 置信度不足时退回到纯 RAG 搜索 |
| RAG chunk 更新 → Wiki | RAG → Wiki | chunk 更新触发相关 Wiki 声明重新验证 |
| ChromaDB 共享 | 双向 | Wiki 页面索引到 ChromaDB 的 `wiki` 命名空间 |

## 数据库表

Wiki 系统使用与 `KnowledgeBaseCatalog` 共享的 SQLite 数据库（`rag_catalog.db`），通过 schema migration v2 添加以下表：

| 表名 | 用途 |
|------|------|
| `wiki_pages` | Wiki 页面 |
| `wiki_statements` | Wiki 声明（外键关联 wiki_pages） |
| `wiki_canonical_definitions` | 规范化定义（复合主键 page_id + term） |
| `wiki_dependency_graph` | 页面依赖关系图 |
| `wiki_review_queue` | 审查队列 |
| `wiki_calibration` | 校准历史记录 |

## CLI 命令参考

### wiki init

初始化 Wiki 并绑定到 RAG 命名空间。

```bash
nexus wiki init <namespace>
```

### wiki ingest

摄取源文档到 Wiki。

```bash
nexus wiki ingest <source_path> --namespace <ns> --type <page_type>
```

- `--namespace` / `-n`：RAG 命名空间（默认 `default`）
- `--type` / `-t`：页面类型（`entity` / `concept` / `overview` / `source_summary`）

### wiki query

基于置信度路由查询 Wiki。

```bash
nexus wiki query "<question>" --namespace <ns> --rag-fallback
```

- `--namespace` / `-n`：RAG 命名空间
- `--rag-fallback` / `-r`：强制使用 RAG 回退
- `--top-k` / `-k`：结果数量（默认 5）

### wiki lint

运行 Wiki 健康检查。

```bash
nexus wiki lint --namespace <ns> --enqueue/--no-enqueue
```

- `--enqueue`：将问题添加到审查队列（默认开启）

### wiki review list

列出审查队列项。

```bash
nexus wiki review list --status pending --limit 20
```

### wiki review resolve

解决审查项。

```bash
nexus wiki review resolve <item_id>
```

### wiki review process

处理超期审查项（自动降级）。

```bash
nexus wiki review process
```

### wiki stats

显示 Wiki 健康统计。

```bash
nexus wiki stats --namespace <ns>
```

### wiki calibrate

使用人工标注样本运行阈值校准。

```bash
nexus wiki calibrate <sample_file.json>
```

样本文件格式：
```json
[
  {
    "statement_id": "stmt_001",
    "text": "声明文本...",
    "source_chunk_ids": ["chunk_001"],
    "source_texts": ["源 chunk 文本..."],
    "human_label": "direct_quote"
  }
]
```

### wiki full-check

运行完整健康检查（统计 + lint）。

```bash
nexus wiki full-check --namespace <ns>
```

## API 端点参考

基础路径：`/api/wiki`

| 方法 | 端点 | 说明 |
|------|------|------|
| `GET` | `/stats?namespace=<ns>` | 获取 Wiki 健康统计 |
| `GET` | `/pages?namespace=<ns>&limit=100` | 列出所有 Wiki 页面 |
| `GET` | `/pages/{page_id}` | 获取单个页面详情（含 statements） |
| `DELETE` | `/pages/{page_id}` | 删除 Wiki 页面 |
| `POST` | `/query` | 查询 Wiki（body: `{question, namespace, force_rag}`） |
| `POST` | `/ingest` | 摄取文本到 Wiki（body: `{source_text, source_uri, namespace, page_type}`） |
| `POST` | `/ingest/file` | 上传文件摄取到 Wiki（multipart form） |
| `POST` | `/lint?namespace=<ns>` | 运行健康检查 |
| `GET` | `/review?status=<status>&limit=50` | 列出审查队列 |
| `POST` | `/review/resolve` | 解决审查项（body: `{item_id}`） |
| `POST` | `/review/process` | 处理超期审查项 |
| `GET` | `/calibration` | 获取最新校准状态 |

## 相关文档

- [RAG 系统](RAG-System.md) - Wiki 系统的底层 RAG 基础设施
- [Memory 系统](Memory-System.md) - 对话记忆管理
- [Configuration](Configuration.md) - Wiki 相关配置项
