> **[中文](RAG-System.md) | [English](RAG-System.en.md)**

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

## RAG 评估系统

### 运行评估

```bash
# 快速模式：4 个代表性配置，~3 分钟
nexus eval run --quick --parallel --jobs 4 --verbose

# 完整模式：12 个配置（3 策略 × 2 chunk size × 2 检索方式），~15 分钟
nexus eval run --parallel --jobs 4 --verbose

# CI 模式：阈值不通过则 exit(1)
nexus eval run --quick --ci

# 导出报告
nexus eval run --quick --output report.json --format json

# 对比两次评估结果
nexus eval compare --baseline old.json --candidate new.json

# 查看历史报告
nexus eval history

# 使用自定义数据集
nexus eval run --dataset my_eval.jsonl --parallel --jobs 4
```

### 评估指标

#### 生成质量（Judge LLM 打分）

| 指标 | 含义 | balanced 阈值 |
|------|------|---------------|
| `faithfulness` | 回答是否忠于检索上下文（不编造） | ≥ 0.80 |
| `answer_relevancy` | 回答是否切题（不依赖 ground_truth） | ≥ 0.75 |
| `answer_correctness` | 回答与 ground_truth 的一致性 | ≥ 0.70 |
| `citation_precision` | 回答中事实可映射到检索结果的比例 | ≥ 0.60 |

#### 检索质量（文本匹配/Embedding）

| 指标 | 含义 | balanced 阈值 |
|------|------|---------------|
| `hit_rate@k` | top-k 中是否至少命中一个参考上下文 | ≥ 0.85 |
| `mrr@k` | 第一个命中的排名倒数（越靠前越好） | ≥ 0.70 |
| `context_precision` | 检索结果中相关 chunk 的比例 | ≥ 0.70 |
| `context_recall` | 参考上下文被检索覆盖的比例 | ≥ 0.70 |
| `context_relevancy` | 检索结果与查询的 Embedding 相似度（非关键词重叠） | ≥ 0.60 |

#### 召回器 vs 重排器分离

| 指标 | 衡量对象 | 说明 |
|------|----------|------|
| `retriever_recall@50` | 召回器（粗排） | top-50 候选中覆盖参考上下文的比例 |
| `reranker_mrr@10` | 重排器（精排） | top-10 中第一个命中的排名倒数 |

分离后可以精确定位问题出在 Retriever 还是 Reranker。

#### 拒答与幻觉（负样本）

| 指标 | 含义 | balanced 阈值 |
|------|------|---------------|
| `rejection_rate` | 负样本中正确拒绝的比例 | ≥ 0.75 |
| `hallucination_rate` | 负样本中产生幻觉的比例 | 越低越好 |

拒答检测使用 Judge LLM 三分类（REJECT / ANSWER / HALLUCINATE），而非关键词匹配。

#### 端到端成功率

| 指标 | 含义 | balanced 阈值 |
|------|------|---------------|
| `task_success_rate` | faithfulness≥0.8 且 correctness≥0.7 且 relevancy≥0.75 的比例 | ≥ 0.65 |

产品负责人可以直接看这一个数字判断用户体验。

### 阈值配置

内置三档阈值，适配不同场景：

| Profile | 适用场景 | faithfulness | task_success_rate |
|---------|----------|--------------|-------------------|
| `strict` | 企业知识库、合规要求高 | 0.95 | 0.80 |
| `balanced` | 通用产品（默认） | 0.80 | 0.65 |
| `relaxed` | 客服 FAQ、容错率高 | 0.70 | 0.50 |

```python
from agentnexus.rag.evaluator import THRESHOLD_PROFILES
run.check_passed(thresholds=THRESHOLD_PROFILES["strict"])
```

### 评估数据集

内置 60 题评估集（`eval_dataset.py`），覆盖：

| 题型 | 数量 | 说明 |
|------|------|------|
| 事实提取 | 22 题 | 单文档直接提取 |
| 多跳推理 | 15 题 | 需综合 2+ 文档 |
| 对比分析 | 12 题 | 跨文档比较 |
| 负样本 | 11 题 | 答案不在知识库中 |

支持自定义 JSONL 数据集（`--dataset` 参数）。

### 性能参考

| 场景 | workers | 耗时 |
|------|---------|------|
| 快速模式（4 配置） | 4 并发 | ~3 分钟 |
| 完整模式（12 配置） | 4 并发 | ~10 分钟 |
| 单配置 | 串行 | ~2 分钟 |

主要瓶颈：每个正样本 3 次 LLM 调用（生成 + 质量评分 + 引用精度），每个负样本 2 次（生成 + 拒答判断）。总 LLM 调用约 169 次/配置。
