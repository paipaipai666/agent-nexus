# ruff: noqa: E501
"""RAG 评估数据集 — 60 题，覆盖事实/多跳/对比/负样本四种题型

题型分布:
   事实提取: 22 题 (37%) — 单文档直接提取
   多跳推理: 15 题 (25%) — 需综合 2+ 文档
   对比分析: 12 题 (20%) — 跨文档比较
   负样本:   11 题 (18%) — 答案不在知识库中

知识库: 12 篇核心技术文档 + 3 篇噪声文档
数据集版本: built-in-v1
"""

import json
from pathlib import Path

from agentnexus.rag.evaluator import EvalSample


def load_eval_dataset(path: str | Path) -> tuple[list[str], list[EvalSample], str]:
    """Load a JSONL evaluation dataset.

    Each non-metadata line must be a JSON object with keys:
      - question (required)
      - ground_truth (optional, empty str = negative sample)
      - reference_contexts (optional, list of str)

    Metadata lines (before samples):
      - knowledge_base (required, list of str)
      - dataset_version (optional, str; defaults to "external-unknown")

    Returns (kb, samples, version).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Eval dataset not found: {path}")

    kb: list[str] | None = None
    samples: list[EvalSample] = []
    version: str = "external-unknown"

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if "dataset_version" in entry:
                version = entry["dataset_version"]
            if "knowledge_base" in entry:
                kb = entry["knowledge_base"]
            if "question" in entry:
                samples.append(EvalSample(
                    question=entry["question"],
                    ground_truth=entry.get("ground_truth", ""),
                    reference_contexts=entry.get("reference_contexts", []),
                ))

    if kb is None:
        raise ValueError(
            "JSONL must include a 'knowledge_base' entry (list of str) "
            "before the first sample, or pass knowledge_base separately."
        )

    return kb, samples, version


KNOWLEDGE_BASE = [
    # ── 核心技术文档（12 篇）──────────────────────────────────────

    "Qdrant 是一个高性能向量数据库，使用 HNSW（分层可导航小世界图）索引实现近似最近邻搜索。"
    "它支持 COSINE、EUCLIDEAN 和 DOT 三种距离度量。Qdrant 的核心特性包括：向量量化（Scalar/"
    "Product Quantization）以降低内存占用、Payload 过滤实现结构化条件筛选、以及磁盘存储"
    "支持（Memmap）使内存受限环境下也能运行大规模索引。",

    "BM25（Best Matching 25）是一种基于概率检索框架的文本排名函数。它结合了词频（TF）和逆文档"
    "频率（IDF），对文档长度进行归一化。BM25 的核心公式为：score(D,Q) = SUM(IDF(qi) * ("
    "f(qi,D)*(k1+1)) / (f(qi,D)+k1*(1-b+b*|D|/avgdl)))，其中 k1 控制词频饱和度（常用 1.2-2.0），"
    "b 控制文档长度归一化程度（常用 0.75）。BM25 特别擅长处理精确关键词匹配查询。",

    "混合检索（Hybrid Search）是融合稠密向量检索和稀疏关键词检索的技术。常见的融合算法包括 RRF"
    "（Reciprocal Rank Fusion，倒数排名融合）和加权分数融合。RRF 公式为：score(d) = SUM(1/(k+rank_i(d)))"
    "，其中 k 默认值为 60。混合检索相比单独使用任一种方法，在大多数场景下能将召回率提升 10-20%。",

    "BGE（BAAI General Embedding）是北京智源人工智能研究院（BAAI）开源的嵌入模型系列。"
    "bge-small-zh-v1.5 是轻量级中文版本，向量维度为 512，模型大小约 95MB，在 C-MTEB 中文"
    "基准测试中检索任务得分 61.77。使用 BGE 进行检索时，查询文本需要添加指令前缀'为这个句子生成表示以用于检索相关文章：'。",

    "RAGAS（Retrieval Augmented Generation Assessment）是一个专门用于评估 RAG 系统的开源框架。"
    "它提供四个核心指标：Faithfulness（忠实度，生成内容是否基于检索到的上下文）、Answer Relevancy"
    "（答案相关性，回答是否切题）、Context Precision（上下文精度，相关文档是否排在检索结果前列）"
    "和 Context Recall（上下文召回率，是否检索到了所有相关文档）。",

    "文档分块（Chunking）是 RAG pipeline 的关键预处理步骤。固定窗口分块（Fixed Window）按字符数"
    "切分，简单但可能切断语义单元。递归分块（Recursive Splitting）使用分层分隔符（段落→句子→词）"
    "逐步切分。语义分块（Semantic Chunking）基于嵌入相似度或自然段落边界，能最好地保留语义完整性。"
    "分块大小直接影响检索粒度和上下文完整性，过小会导致信息碎片化，过大则引入噪声降低 Precision。",

    "LangGraph 是一个用于构建有状态多智能体应用的 Python 框架。它的核心概念包括：StateGraph（"
    "状态图定义 Agent 流程）、Node（处理节点，执行具体逻辑）、Edge（边，定义节点间流转）和"
    "Conditional Edges（条件边，支持动态路由）。LangGraph 内置了 Checkpointer 机制实现状态持久化，"
    "支持 SQLite 和 Postgres 后端。它广泛应用于构建 ReAct 模式的 Agent 系统。",

    "ReAct（Reasoning + Acting）是 Google DeepMind 提出的 Agent 推理框架。Agent 在推理（"
    "Thought）和行动（Action）之间交替循环：先思考需要做什么，再调用工具执行，观察结果后继续"
    "推理，直到得出最终答案。这个模式让 Agent 能够处理需要多步推理和外部信息获取的复杂任务。"
    "LangGraph 提供的 Conditional Edges 机制非常适合实现 ReAct 循环中的条件路由。",

    "向量量化（Vector Quantization）是降低向量存储和检索成本的技术。标量量化（Scalar Quantization）"
    "将 float32 压缩为 int8，内存减少 75% 且查询速度提升 3-4 倍，精度损失极小。乘积量化（Product "
    "Quantization）将向量分解为子向量分别量化，适用于超大规模索引（亿级以上）。这些技术在 Qdrant"
    "和许多向量数据库中得到了广泛应用。",

    "Embedding 模型的核心原理是通过对比学习（Contrastive Learning）训练，使语义相近的文本在向量"
    "空间中距离接近。训练时使用正例对（相关文本）和负例对（不相关文本），优化目标是让正例对的相似度"
    "高于负例对。常用的损失函数包括 InfoNCE 和 Triplet Loss。BGE 系列模型就采用了这种训练方法。",

    "Reranker（重排序器）采用 Cross-Encoder 架构，将查询和文档拼接后通过 Transformer 编码器进行"
    "深度语义交互计算。与 Bi-Encoder（如 BGE Embedding）相比，Cross-Encoder 精度高出 5-15%，"
    "但计算成本也高出 10-100 倍。因此通常用 Bi-Encoder 做粗排，Cross-Encoder 对 Top-K 候选精排。",

    "jieba 分词是 Python 中最流行的中文分词工具。它支持精确模式（最精确的切分）、全模式（所有"
    "可能的词）、搜索引擎模式（在精确基础上对长词再切分）和 paddle 模式（利用深度学习）。在 RAG "
    "系统中，jieba 用于 BM25 的中文分词，将查询和文档切分为有意义的词条。BM25 结合 jieba 分词后"
    "可以显著提升中文检索的准确性。",

    # ── 噪声文档（3 篇）────────────────────────────────────────

    "Milvus 是 Zilliz 公司开源的云原生向量数据库，基于 Facebook 开源的 Faiss 库构建。它采用"
    "ANN（近似最近邻）索引，支持 IVF_FLAT、IVF_SQ8、IVF_PQ、HNSW 等多种索引类型。Milvus 2.0 "
    "重构为云原生架构，采用存算分离设计，支持水平扩展和混合查询。它提供 Python、Java、Go、Node.js 等"
    "多语言 SDK，并集成了 Attu 图形化管理工具。Milvus 与 Qdrant 是市场上最活跃的两个开源向量数据库。",

    "2024-01-15 10:23:45 INFO [main] Starting application v2.4.1\n"
    "2024-01-15 10:23:46 DEBUG [auth] User authentication successful, session_id=abc123\n"
    "2024-01-15 10:24:02 WARN [db] Connection pool reaching limit (48/50)\n"
    "2024-01-15 10:24:15 ERROR [api] Request timeout for /api/v1/search, elapsed=3200ms\n"
    "2024-01-15 10:24:20 INFO [cache] Cache miss for key 'user_prefs_8821', reloading from DB\n"
    "2024-01-15 10:24:31 DEBUG [search] Lucene index optimized, segments=12->8\n"
    "2024-01-15 10:25:00 INFO [main] Health check passed, memory=2.1GB/4.0GB",

    "TensorFlow 2.0 使用 Keras 作为高级 API，支持 eager execution 模式简化调试。模型部署"
    "可以通过 TensorFlow Serving、TensorFlow Lite（移动端）和 TensorFlow.js（浏览器）实现。"
    "分布式训练使用 tf.distribute.Strategy，支持 MirroredStrategy（单机多卡）和"
    "MultiWorkerMirroredStrategy（多机多卡）。2024 年推出的 Gemma 开源模型系列基于"
    "与 Gemini 相同的研究和技术构建，提供 2B 和 7B 两种参数规模。",
]

DATASET_VERSION = "built-in-v1"


# ═══════════════════════════════════════════════════════════════════
# 评估样本
# ═══════════════════════════════════════════════════════════════════

EVAL_SAMPLES = [
    # ── 事实提取题（13 题）──────────────────────────────────────

    EvalSample(
        question="Qdrant 使用什么索引算法？支持哪些距离度量？",
        ground_truth="Qdrant 使用 HNSW 索引算法，支持 COSINE、EUCLIDEAN 和 DOT 三种距离度量。",
        reference_contexts=["Qdrant 是一个高性能向量数据库，使用 HNSW", "它支持 COSINE、EUCLIDEAN 和 DOT 三种距离度量"],
    ),
    EvalSample(
        question="BM25 算法中的 k1 和 b 参数分别控制什么？常用值是多少？",
        ground_truth="k1 控制词频饱和度（常用 1.2-2.0），b 控制文档长度归一化程度（常用 0.75）。",
        reference_contexts=["BM25（Best Matching 25）是一种基于概率检索框架", "k1 控制词频饱和度", "b 控制文档长度归一化"],
    ),
    EvalSample(
        question="什么是混合检索？RRF 公式中的 k 默认值是多少？",
        ground_truth="混合检索融合稠密向量检索和稀疏关键词检索，RRF 公式中 k 默认值为 60，能将召回率提升 10-20%。",
        reference_contexts=["混合检索（Hybrid Search）是融合稠密向量检索", "RRF 公式为：score(d) = SUM(1/(k+rank_i(d)))"],
    ),
    EvalSample(
        question="BGE-small-zh-v1.5 的向量维度是多少？模型大小大约多少？",
        ground_truth="向量维度为 512，模型大小约 95MB。",
        reference_contexts=["bge-small-zh-v1.5 是轻量级中文版本，向量维度为 512", "模型大小约 95MB"],
    ),
    EvalSample(
        question="RAGAS 提供哪四个核心评估指标？各指标衡量什么？",
        ground_truth="Faithfulness（忠实度，生成内容是否基于上下文）、Answer Relevancy（答案相关性，回答是否切题）、Context Precision（上下文精度，相关文档是否排在前面）、Context Recall（上下文召回率，是否检索到所有相关文档）。",
        reference_contexts=["RAGAS（Retrieval Augmented Generation Assessment）", "四个核心指标：Faithfulness", "Answer Relevancy", "Context Precision", "Context Recall"],
    ),
    EvalSample(
        question="文档分块有哪三种策略？语义分块相比固定窗口分块有什么优势？",
        ground_truth="三种策略：固定窗口分块、递归分块、语义分块。语义分块基于嵌入相似度或自然段落边界，能最好地保留语义完整性，而固定窗口切分可能切断语义单元。",
        reference_contexts=["文档分块（Chunking）是 RAG pipeline 的关键预处理步骤",
                           "固定窗口分块（Fixed Window）按字符数切分",
                           "语义分块（Semantic Chunking）基于嵌入相似度"],
    ),
    EvalSample(
        question="LangGraph 的核心概念有哪些？",
        ground_truth="StateGraph（状态图定义流程）、Node（处理节点）、Edge（边，定义流转）、Conditional Edges（条件边，支持动态路由）。",
        reference_contexts=["LangGraph 是一个用于构建有状态多智能体应用",
                           "StateGraph（状态图定义 Agent 流程）",
                           "Conditional Edges（条件边，支持动态路由）"],
    ),
    EvalSample(
        question="ReAct 框架中的 Agent 如何工作？在什么步骤之间交替循环？",
        ground_truth="Agent 在推理（Thought）和行动（Action）之间交替循环：先思考需要做什么，再调用工具执行，观察结果后继续推理，直到得出最终答案。",
        reference_contexts=["ReAct（Reasoning + Acting）是 Google DeepMind 提出的",
                           "Agent 在推理（Thought）和行动（Action）之间交替循环"],
    ),
    EvalSample(
        question="标量量化的主要优势是什么？能将内存减少多少？",
        ground_truth="将 float32 压缩为 int8，内存减少 75%，查询速度提升 3-4 倍，精度损失极小。",
        reference_contexts=["向量量化（Vector Quantization）是降低向量存储和检索成本",
                           "标量量化将 float32 压缩为 int8，内存减少 75%"],
    ),
    EvalSample(
        question="Embedding 模型使用什么训练方法？常用的损失函数有哪些？",
        ground_truth="通过对比学习（Contrastive Learning）训练，使用正例对和负例对，常用损失函数包括 InfoNCE 和 Triplet Loss。",
        reference_contexts=["Embedding 模型的核心原理是通过对比学习", "对比学习（Contrastive Learning）", "InfoNCE 和 Triplet Loss"],
    ),
    EvalSample(
        question="Reranker 使用什么架构？与 Bi-Encoder 相比精度和成本如何？",
        ground_truth="采用 Cross-Encoder 架构，精度高出 5-15%，但计算成本高 10-100 倍。通常用 Bi-Encoder 粗排，Cross-Encoder 对 Top-K 候选精排。",
        reference_contexts=["Reranker（重排序器）采用 Cross-Encoder 架构",
                           "与 Bi-Encoder（如 BGE Embedding）相比，Cross-Encoder 精度高出 5-15%"],
    ),
    EvalSample(
        question="jieba 分词有哪几种模式？在 RAG 系统中具体用于什么场景？",
        ground_truth="精确模式、全模式、搜索引擎模式和 paddle 模式。在 RAG 中用于 BM25 的中文分词，将查询和文档切分为有意义的词条以提升中文检索准确性。",
        reference_contexts=["jieba 分词是 Python 中最流行的中文分词工具", "在 RAG 系统中，jieba 用于 BM25 的中文分词"],
    ),
    EvalSample(
        question="分块大小对检索有什么影响？过小和过大会分别导致什么问题？",
        ground_truth="分块过小会导致信息碎片化，过大会引入噪声降低 Precision。分块大小直接影响检索粒度和上下文完整性。",
        reference_contexts=["分块大小直接影响检索粒度和上下文完整性", "过小会导致信息碎片化", "过大则引入噪声降低 Precision"],
    ),

    # ── 多跳推理题（8 题）──────────────────────────────────────

    EvalSample(
        question="Qdrant 支持向量量化吗？如果是，支持哪两种量化方式？",
        ground_truth="支持。标量量化（Scalar Quantization）将 float32 压缩为 int8；乘积量化（Product Quantization）将向量分解为子向量分别量化，适用于超大规模索引。",
        reference_contexts=["Qdrant 的核心特性包括：向量量化（Scalar/Product Quantization）",
                           "标量量化（Scalar Quantization）将 float32 压缩为 int8",
                           "乘积量化（Product Quantization）将向量分解为子向量分别量化"],
    ),
    EvalSample(
        question="LangGraph 为什么适合实现 ReAct 模式？具体用了什么机制？",
        ground_truth="LangGraph 的 Conditional Edges 机制非常适合实现 ReAct 循环中的条件路由——根据当前状态动态决定下一步执行哪个 Node，这正好对应 ReAct 的 Thought→Action→Observation 循环。此外内置的 Checkpointer 实现状态持久化，支持长链路 Agent 任务的断点恢复。",
        reference_contexts=["LangGraph 是一个用于构建有状态多智能体应用的 Python 框架",
                           "Conditional Edges（条件边，支持动态路由）",
                           "ReAct（Reasoning + Acting）是 Google DeepMind 提出的",
                           "Agent 在推理（Thought）和行动（Action）之间交替循环"],
    ),
    EvalSample(
        question="在 RAG 系统中，BM25 结合 jieba 分词后为什么能提升中文检索准确性？",
        ground_truth="jieba 负责将中文查询和文档切分为有意义的词条（支持精确模式和搜索引擎模式），BM25 基于这些词条计算词频和逆文档频率进行排名。没有 jieba 分词，BM25 无法处理中文（中文没有空格分隔），两者结合才能实现中文关键词的精确匹配检索。",
        reference_contexts=["BM25（Best Matching 25）是一种基于概率检索框架的文本排名函数",
                           "jieba 用于 BM25 的中文分词，将查询和文档切分为有意义的词条",
                           "BM25 结合 jieba 分词后可以显著提升中文检索的准确性"],
    ),
    EvalSample(
        question="混合检索中的 RRF 融合算法如何综合稠密和稀疏两种检索结果？为什么 k 设为 60？",
        ground_truth="RRF 对每种检索结果按排名计算倒数分数 score=1/(k+rank)，然后对每个文档的两组分数求和。k=60 是经验值，作用是平滑不同检索方法之间的排名差异——k 越大，排名差异的影响越小，越不容易被极端排名带偏。混合检索通常能将召回率提升 10-20%。",
        reference_contexts=["混合检索（Hybrid Search）是融合稠密向量检索和稀疏关键词检索",
                           "RRF 公式为：score(d) = SUM(1/(k+rank_i(d)))",
                           "k 默认值为 60"],
    ),
    EvalSample(
        question="BGE Embedding 模型和 Reranker 的 Cross-Encoder 在 RAG pipeline 中分别扮演什么角色？为什么这样分工？",
        ground_truth="BGE Embedding（Bi-Encoder）负责粗排：将查询和文档分别编码为向量后用余弦相似度快速召回候选集；Reranker（Cross-Encoder）负责精排：将查询和候选文档拼接后做深度语义交互，精度高 5-15%。这样分工是因为 Cross-Encoder 计算成本高 10-100 倍，直接对全量文档做精排不现实。",
        reference_contexts=["BGE（BAAI General Embedding）是北京智源人工智能研究院（BAAI）开源的嵌入模型系列",
                           "Reranker（重排序器）采用 Cross-Encoder 架构",
                           "与 Bi-Encoder（如 BGE Embedding）相比，Cross-Encoder 精度高出 5-15%",
                           "计算成本也高出 10-100 倍"],
    ),
    EvalSample(
        question="文档分块策略的选择如何影响 RAGAS Context Precision 和 Context Recall 这两个指标？",
        ground_truth="分块策略直接影响检索精度和召回率：固定窗口分块可能切断语义单元，导致检索到的块虽然包含关键词但语义不完整，降低 Precision 和 Faithfulness；语义分块保留语义完整性，能提升检索结果的相关性，从而提高 Context Precision。分块过大引入噪声降低 Precision，过小则信息碎片化降低 Recall。",
        reference_contexts=["文档分块（Chunking）是 RAG pipeline 的关键预处理步骤",
                           "固定窗口分块（Fixed Window）按字符数切分，简单但可能切断语义单元",
                           "语义分块（Semantic Chunking）基于嵌入相似度或自然段落边界",
                           "RAGAS 提供四个核心指标", "Context Precision（上下文精度）", "Context Recall（上下文召回率）"],
    ),
    EvalSample(
        question="Embedding 模型的对比学习和 Reranker 的 Cross-Encoder 都涉及语义匹配，它们在方法论上有什么根本不同？",
        ground_truth="Embedding 使用 Bi-Encoder 架构，将查询和文档分别独立编码为向量，通过向量空间中的距离度量（余弦相似度）计算相关性——这是一种非交互式方法。Reranker 的 Cross-Encoder 将查询和文档拼接后整体输入 Transformer，让它们进行深度交互计算——这是一种交互式方法。Cross-Encoder 精度更高但计算成本也远高于 Bi-Encoder。",
        reference_contexts=["Embedding 模型的核心原理是通过对比学习（Contrastive Learning）",
                           "Reranker（重排序器）采用 Cross-Encoder 架构",
                           "将查询和文档拼接后通过 Transformer 编码器进行深度语义交互计算",
                           "与 Bi-Encoder（如 BGE Embedding）相比"],
    ),
    EvalSample(
        question="在 Qdrant 中启用标量量化后，对 Embedding 模型的 512 维向量存储有什么实际影响？",
        ground_truth="对于 BGE-small-zh-v1.5 的 512 维 float32 向量，每向量原始占 512×4=2048 字节。标量量化压缩为 int8 后，内存减少 75% 至约 512 字节，查询速度提升 3-4 倍，同时精度损失极小。这在大规模知识库场景下可以显著降低 Qdrant 的存储成本。",
        reference_contexts=["bge-small-zh-v1.5 是轻量级中文版本，向量维度为 512",
                           "标量量化（Scalar Quantization）将 float32 压缩为 int8，内存减少 75%",
                           "Qdrant 的核心特性包括：向量量化"],
    ),

    # ── 对比分析题（5 题）──────────────────────────────────────

    EvalSample(
        question="固定窗口分块、递归分块和语义分块三种策略各有什么优缺点？在什么场景下应该选择哪一种？",
        ground_truth="固定窗口：实现简单、速度快，但可能切断语义单元，适合格式规整的文档。递归分块：使用分层分隔符逐步切分，比固定窗口更能保留段落和句子边界，适合结构化的自然语言文档。语义分块：基于嵌入相似度，最能保留语义完整性，但计算成本最高，适合对检索质量要求高的场景。",
        reference_contexts=["固定窗口分块（Fixed Window）按字符数切分",
                           "递归分块（Recursive Splitting）使用分层分隔符",
                           "语义分块（Semantic Chunking）基于嵌入相似度"],
    ),
    EvalSample(
        question="BM25 的稀疏检索和 BGE Embedding 的稠密检索在处理中文查询时各有什么优势和局限？",
        ground_truth="BM25（稀疏检索）：擅长精确关键词匹配，结合 jieba 分词后能准确匹配中文关键词，但对同义词和语义相近的表达不敏感。BGE Embedding（稠密检索）：通过向量空间中的语义相似度检索，能理解同义词和语义相近的表达，但可能忽略关键词的精确匹配。两者混合使用（Hybrid Search）能取长补短，通常将召回率提升 10-20%。",
        reference_contexts=["BM25 特别擅长处理精确关键词匹配查询",
                           "BGE（BAAI General Embedding）是北京智源人工智能研究院（BAAI）开源的嵌入模型系列",
                           "混合检索（Hybrid Search）是融合稠密向量检索和稀疏关键词检索的技术"],
    ),
    EvalSample(
        question="ReAct 模式和 LangGraph 的 StateGraph 在 Agent 设计上分别侧重什么？它们如何互补？",
        ground_truth="ReAct 侧重 Agent 的单步推理范式：Thought→Action→Observation 循环，定义了 Agent 一次推理-行动的基本单元。LangGraph 的 StateGraph 侧重整体流程编排：通过 Node、Edge、Conditional Edges 定义多 Agent 的协作流程和状态管理。两者互补：ReAct 定义了单个 Agent 内部如何思考，LangGraph 定义了多个 Agent 之间如何协作。",
        reference_contexts=["ReAct（Reasoning + Acting）是 Google DeepMind 提出的 Agent 推理框架",
                           "LangGraph 是一个用于构建有状态多智能体应用的 Python 框架",
                           "StateGraph（状态图定义 Agent 流程）"],
    ),
    EvalSample(
        question="RAGAS 的 Faithfulness 和 Context Precision 两个指标分别测什么？为什么两个指标分开比合并更有诊断价值？",
        ground_truth="Faithfulness 测生成答案是否忠实于检索到的上下文——如果 LLM 自己编造信息，Faithfulness 低。Context Precision 测检索回来的文档中相关文档的比例——如果检索召回了大量噪声，Precision 低。两者分开的价值在于：可以区分问题出在检索阶段还是生成阶段。如果 Precision 低但 Faithfulness 高，说明检索有问题但 LLM 很谨慎；如果两者都低，可能是系统性问题。",
        reference_contexts=["RAGAS（Retrieval Augmented Generation Assessment）是一个专门用于评估 RAG 系统",
                           "Faithfulness（忠实度，生成内容是否基于检索到的上下文）",
                           "Context Precision（上下文精度，相关文档是否排在检索结果前列）"],
    ),
    EvalSample(
        question="标量量化和乘积量化都用于降低向量存储成本，它们的核心区别和适用场景是什么？",
        ground_truth="标量量化（SQ）：将每个维度的 float32 独立压缩为 int8，实现简单，内存减少 75%，精度损失极小，适合中等规模索引。乘积量化（PQ）：将向量分解为多个子向量分别量化码本，压缩率更高但精度损失也更大，适合亿级以上的超大规模索引。Qdrant 同时支持这两种量化方式。",
        reference_contexts=["标量量化（Scalar Quantization）将 float32 压缩为 int8，内存减少 75%",
                           "乘积量化（Product Quantization）将向量分解为子向量分别量化",
                           "适用于超大规模索引（亿级以上）"],
    ),

    # ── 负样本题（4 题）─ 答案不在知识库中 ─────────────────────────

    EvalSample(
        question="LangGraph 的作者是谁？他在哪个公司研发的这个框架？",
        ground_truth="",
        reference_contexts=[],
    ),
    EvalSample(
        question="Qdrant 和 Pinecone 在性能上有什么差异？哪个更适合生产环境？",
        ground_truth="",
        reference_contexts=[],
    ),
    EvalSample(
        question="RAGAS 评估框架的最新版本是多少？什么时候发布的？",
        ground_truth="",
        reference_contexts=[],
    ),
    EvalSample(
        question="BGE Embedding 模型在训练时使用了多大的数据集？包含多少种语言？",
        ground_truth="",
        reference_contexts=[],
    ),

    # ═══════════════════════════════════════════════════════════════════
    # 新增试题（v1 扩充，从 30 增至 60 题）
    # ═══════════════════════════════════════════════════════════════════

    # ── 新增事实提取题（9 题）──────────────────────────────────────

    EvalSample(
        question="Qdrant 的 Memmap 支持有什么作用？",
        ground_truth="Qdrant 的磁盘存储支持（Memmap）使内存受限环境下也能运行大规模索引。",
        reference_contexts=["磁盘存储支持（Memmap）使内存受限环境下也能运行大规模索引"],
    ),
    EvalSample(
        question="BM25 结合了哪两种频率计算？",
        ground_truth="BM25 结合了词频（TF）和逆文档频率（IDF），并对文档长度进行归一化。",
        reference_contexts=["BM25 结合了词频（TF）和逆文档频率（IDF）", "对文档长度进行归一化"],
    ),
    EvalSample(
        question="混合检索常见的融合算法有哪些？能将召回率提升多少？",
        ground_truth="常见的融合算法包括 RRF（倒数排名融合）和加权分数融合。混合检索相比单独使用任一种方法，在大多数场景下能将召回率提升 10-20%。",
        reference_contexts=["常见的融合算法包括 RRF（倒数排名融合）和加权分数融合",
                           "混合检索相比单独使用任一种方法，在大多数场景下能将召回率提升 10-20%"],
    ),
    EvalSample(
        question="BGE-small-zh-v1.5 在 C-MTEB 中文基准测试中检索任务得分是多少？",
        ground_truth="在 C-MTEB 中文基准测试中检索任务得分 61.77。",
        reference_contexts=["在 C-MTEB 中文基准测试中检索任务得分 61.77"],
    ),
    EvalSample(
        question="使用 BGE 进行检索时，查询文本需要添加什么指令前缀？为什么？",
        ground_truth="查询文本需要添加指令前缀'为这个句子生成表示以用于检索相关文章：'。这是因为 BGE 模型在训练时使用了这个前缀来区分查询和文档的表示空间。",
        reference_contexts=["查询文本需要添加指令前缀'为这个句子生成表示以用于检索相关文章：'"],
    ),
    EvalSample(
        question="LangGraph 内置了什么机制实现状态持久化？支持哪些后端？",
        ground_truth="LangGraph 内置了 Checkpointer 机制实现状态持久化，支持 SQLite 和 Postgres 后端。",
        reference_contexts=["LangGraph 内置了 Checkpointer 机制实现状态持久化",
                           "支持 SQLite 和 Postgres 后端"],
    ),
    EvalSample(
        question="Reranker 在 RAG pipeline 中通常用于什么阶段？",
        ground_truth="通常用 Bi-Encoder 做粗排，Cross-Encoder（Reranker）对 Top-K 候选精排。",
        reference_contexts=["通常用 Bi-Encoder 做粗排，Cross-Encoder 对 Top-K 候选精排"],
    ),
    EvalSample(
        question="Embedding 模型训练中正例对和负例对分别指什么？",
        ground_truth="训练时使用正例对（相关文本）和负例对（不相关文本），优化目标是让正例对的相似度高于负例对。",
        reference_contexts=["训练时使用正例对（相关文本）和负例对（不相关文本）"],
    ),
    EvalSample(
        question="Qdrant 的 Payload 过滤实现了什么功能？",
        ground_truth="Qdrant 的 Payload 过滤实现结构化条件筛选，结合向量检索进行精确过滤。",
        reference_contexts=["Payload 过滤实现结构化条件筛选"],
    ),

    # ── 新增多跳推理题（7 题）──────────────────────────────────────

    EvalSample(
        question="在 RAG pipeline 中，文档分块策略和 Embedding 模型如何共同影响检索质量？",
        ground_truth="分块策略决定检索粒度和上下文完整性：语义分块保留语义完整性利于 Embedding 捕捉语义关联，固定窗口可能切断语义单元导致 Embedding 难以找到相关片段。分块过小导致信息碎片化降低召回率，过大引入噪声降低精度。Embedding 模型（如 BGE）通过向量相似度检索，对完整性好的分块能更准确地匹配语义。",
        reference_contexts=["文档分块（Chunking）是 RAG pipeline 的关键预处理步骤",
                           "语义分块（Semantic Chunking）基于嵌入相似度或自然段落边界，能最好地保留语义完整性",
                           "分块大小直接影响检索粒度和上下文完整性",
                           "Embedding 模型的核心原理是通过对比学习",
                           "BGE 系列模型就采用了这种训练方法"],
    ),
    EvalSample(
        question="LangGraph 的状态持久化机制与 ReAct 的推理循环如何配合实现长链路 Agent？",
        ground_truth="LangGraph 的 Checkpointer 支持 SQLite/Postgres 持久化 Agent 状态，允许在任意 Node 中断后恢复。ReAct 的 Thought→Action→Observation 循环定义了 Agent 的推理单元。两者配合：Checkpointer 在每次 ReAct 循环后保存状态快照，当 Agent 在长链路任务中中断时，可以从最近 Checkpoint 恢复，无需重新执行前面的 ReAct 循环。",
        reference_contexts=["LangGraph 内置了 Checkpointer 机制实现状态持久化",
                           "支持 SQLite 和 Postgres 后端",
                           "ReAct（Reasoning + Acting）是 Google DeepMind 提出的 Agent 推理框架",
                           "Agent 在推理（Thought）和行动（Action）之间交替循环",
                           "LangGraph 提供的 Conditional Edges 机制非常适合实现 ReAct 循环中的条件路由"],
    ),
    EvalSample(
        question="为什么说 Reranker 的 Cross-Encoder 和 BGE 的 Bi-Encoder 构成 RAG 的两阶段检索？",
        ground_truth="BGE（Bi-Encoder）先对全量文档做粗排：将查询和文档分别编码为向量，用余弦相似度快速召回候选集。Reranker（Cross-Encoder）对 Top-K 候选做精排：将查询和文档拼接后通过 Transformer 深度交互计算。因为 Cross-Encoder 计算成本高 10-100 倍，直接对全量文档做精排不现实，所以两阶段结合是 RAG 的标准实践。",
        reference_contexts=["BGE（BAAI General Embedding）是北京智源人工智能研究院开源的嵌入模型系列",
                           "Reranker（重排序器）采用 Cross-Encoder 架构",
                           "与 Bi-Encoder（如 BGE Embedding）相比，Cross-Encoder 精度高出 5-15%",
                           "计算成本也高出 10-100 倍",
                           "通常用 Bi-Encoder 做粗排，Cross-Encoder 对 Top-K 候选精排"],
    ),
    EvalSample(
        question="Qdrant 的 HNSW 索引和向量量化技术如何共同优化检索性能？",
        ground_truth="HNSW 是近似最近邻搜索算法，通过分层可导航小世界图加速检索。向量量化（标量量化/PQ）降低每个向量的内存占用：标量量化将 float32 压缩为 int8，内存减少 75%。两者结合：HNSW 减少搜索空间，量化降低单向量存储成本，使大规模索引在内存受限环境下也能高效运行。Qdrant 同时支持这两种技术。",
        reference_contexts=["Qdrant 是一个高性能向量数据库，使用 HNSW（分层可导航小世界图）索引",
                           "Qdrant 的核心特性包括：向量量化（Scalar/Product Quantization）",
                           "标量量化（Scalar Quantization）将 float32 压缩为 int8，内存减少 75%",
                           "磁盘存储支持（Memmap）使内存受限环境下也能运行大规模索引"],
    ),
    EvalSample(
        question="RAGAS 的 Faithfulness 和 Answer Relevancy 两个指标与文档分块有何关联？",
        ground_truth="Faithfulness 测生成答案是否忠实于检索到的上下文：分块过大会引入噪声，LLM 可能从无关内容中编造信息，降低 Faithfulness。Answer Relevancy 测回答是否切题：分块过小导致信息碎片化，LLM 缺乏完整上下文支撑回答，答非所问。语义分块保留语义完整性，能同时改善这两个指标。",
        reference_contexts=["RAGAS（Retrieval Augmented Generation Assessment）是一个专门用于评估 RAG 系统的开源框架",
                           "Faithfulness（忠实度，生成内容是否基于检索到的上下文）",
                           "Answer Relevancy（答案相关性，回答是否切题）",
                           "语义分块（Semantic Chunking）基于嵌入相似度或自然段落边界，能最好地保留语义完整性",
                           "分块大小直接影响检索粒度和上下文完整性"],
    ),
    EvalSample(
        question="在 RAG 系统中，jieba 分词的搜索引擎模式和精确模式分别在什么场景下更合适？",
        ground_truth="精确模式最精确的切分，适合对检索精度要求高的场景，确保匹配到完整关键词。搜索引擎模式在精确基础上对长词再切分，能提高召回率但可能引入噪声。在 BM25 中，精确模式生成的词条更准确匹配文档，搜索引擎模式适合处理包含复合词的用户查询。结合 jieba 分词后 BM25 能显著提升中文检索准确性。",
        reference_contexts=["jieba 分词是 Python 中最流行的中文分词工具",
                           "支持精确模式（最精确的切分）、全模式、搜索引擎模式和 paddle 模式",
                           "在 RAG 系统中，jieba 用于 BM25 的中文分词",
                           "BM25 结合 jieba 分词后可以显著提升中文检索的准确性"],
    ),
    EvalSample(
        question="RRF 融合算法如何处理稠密检索和稀疏检索的排名差异？为什么 k 设为 60 而不是更小或更大？",
        ground_truth="RRF 对每种检索结果按排名计算倒数分数 score=1/(k+rank)，然后对每个文档的两组分数求和。k=60 是经验值：k 越小，排名靠前的文档权重越大，容易偏向某一检索方法的头部结果；k 越大，排名差异的影响越小，融合结果更平滑。60 是平衡点，既能保留各自方法的优势排名，又不会被单一方法的极端排名带偏。",
        reference_contexts=["RRF（Reciprocal Rank Fusion，倒数排名融合）",
                           "RRF 公式为：score(d) = SUM(1/(k+rank_i(d)))",
                           "k 默认值为 60",
                           "混合检索相比单独使用任一种方法，在大多数场景下能将召回率提升 10-20%"],
    ),

    # ── 新增对比分析题（7 题）──────────────────────────────────────

    EvalSample(
        question="Qdrant 和 Milvus 在索引类型和架构设计上有什么主要区别？",
        ground_truth="Qdrant 使用 HNSW 索引，支持向量量化和 Payload 过滤，有 Memmap 磁盘存储支持。Milvus 基于 Faiss 库构建，支持 IVF_FLAT、IVF_SQ8、IVF_PQ、HNSW 等多种索引类型。架构上 Milvus 2.0 采用云原生存算分离设计、支持水平扩展；Qdrant 更加轻量，两者是市场上最活跃的两个开源向量数据库。",
        reference_contexts=["Qdrant 是一个高性能向量数据库，使用 HNSW（分层可导航小世界图）索引",
                           "Qdrant 的核心特性包括：向量量化、Payload 过滤、磁盘存储支持（Memmap）",
                           "Milvus 是 Zilliz 公司开源的云原生向量数据库，基于 Facebook 开源的 Faiss 库构建",
                           "支持 IVF_FLAT、IVF_SQ8、IVF_PQ、HNSW 等多种索引类型",
                           "Milvus 2.0 重构为云原生架构，采用存算分离设计，支持水平扩展",
                           "Milvus 与 Qdrant 是市场上最活跃的两个开源向量数据库"],
    ),
    EvalSample(
        question="RAGAS 和 Qdrant 在 RAG 系统中分别解决什么不同层面的问题？",
        ground_truth="RAGAS 是评估框架，提供 Faithfulness、Answer Relevancy、Context Precision、Context Recall 四个指标来衡量 RAG 系统的检索和生成质量。Qdrant 是向量数据库，负责存储 Embedding 向量并提供高效的 ANN 检索能力。两者解决不同环节的问题：Qdrant 是 RAG pipeline 的存储检索基础设施，RAGAS 是评估整个 pipeline 质量的度量工具。",
        reference_contexts=["RAGAS（Retrieval Augmented Generation Assessment）是一个专门用于评估 RAG 系统的开源框架",
                           "四个核心指标：Faithfulness、Answer Relevancy、Context Precision、Context Recall",
                           "Qdrant 是一个高性能向量数据库，使用 HNSW 索引"],
    ),
    EvalSample(
        question="标量量化和乘积量化在压缩率、精度损失和适用场景上如何权衡？",
        ground_truth="标量量化（SQ）将每个维度的 float32 独立压缩为 int8，内存减少 75%，精度损失极小，实现简单，适合中等规模索引。乘积量化（PQ）将向量分解为子向量分别量化码本，压缩率更高但精度损失也更大，适合亿级以上的超大规模索引。Qdrant 同时支持这两种量化方式。",
        reference_contexts=["标量量化（Scalar Quantization）将 float32 压缩为 int8，内存减少 75%",
                           "乘积量化（Product Quantization）将向量分解为子向量分别量化",
                           "适用于超大规模索引（亿级以上）"],
    ),
    EvalSample(
        question="Bi-Encoder（如 BGE）和 Cross-Encoder（如 Reranker）在语义匹配方法上有什么根本不同？",
        ground_truth="Bi-Encoder 将查询和文档分别独立编码为向量，通过余弦相似度计算相关性——非交互式方法，速度快但无法捕捉查询-文档间的细粒度交互。Cross-Encoder 将查询和文档拼接后输入 Transformer 进行深度交互计算——交互式方法，精度高 5-15% 但计算成本高 10-100 倍。因此在 RAG 中两者分工：Bi-Encoder 粗排、Cross-Encoder 精排。",
        reference_contexts=["BGE 是北京智源人工智能研究院开源的嵌入模型系列",
                           "Reranker（重排序器）采用 Cross-Encoder 架构，将查询和文档拼接后输入",
                           "与 Bi-Encoder 相比，Cross-Encoder 精度高出 5-15%，计算成本也高出 10-100 倍",
                           "通常用 Bi-Encoder 做粗排，Cross-Encoder 对 Top-K 候选精排"],
    ),
    EvalSample(
        question="ReAct 模式的 Thought-Action 循环和 LangGraph 的状态机在设计哲学上有什么不同？",
        ground_truth="ReAct 定义 Agent 的单步推理范式：每个步骤交替进行思考和行动，聚焦于 Agent 如何做决策和推理。LangGraph 的 StateGraph 定义整体的流程编排：通过 Node 定义处理单元、Edge 定义流转、Conditional Edges 定义动态路由，聚焦于多 Agent 如何协作和状态如何管理。ReAct 是 Agent 内部的推理机制，LangGraph 是 Agent 之间的编排框架。",
        reference_contexts=["ReAct（Reasoning + Acting）是 Google DeepMind 提出的 Agent 推理框架",
                           "Agent 在推理（Thought）和行动（Action）之间交替循环",
                           "LangGraph 是一个用于构建有状态多智能体应用的 Python 框架",
                           "核心概念包括：StateGraph、Node、Edge、Conditional Edges"],
    ),
    EvalSample(
        question="固定窗口分块按字符数切分和语义分块按嵌入相似度切分，在实现复杂度和检索效果上各有什么利弊？",
        ground_truth="固定窗口：实现简单、速度快，O(n) 时间复杂度，但可能切断语义单元，导致检索到包含关键词但语义不完整的片段，降低 Faithfulness 和 Context Precision。语义分块：计算成本高（需 Embedding 模型编码），但能最好地保留语义完整性，检索结果更相关，Context Precision 和 Recall 都更好。选择取决于场景：快速原型用固定窗口，生产高质量检索用语义分块。",
        reference_contexts=["固定窗口分块（Fixed Window）按字符数切分，简单但可能切断语义单元",
                           "语义分块（Semantic Chunking）基于嵌入相似度或自然段落边界，能最好地保留语义完整性",
                           "分块过小会导致信息碎片化，过大则引入噪声降低 Precision",
                           "分块大小直接影响检索粒度和上下文完整性"],
    ),
    EvalSample(
        question="BM25 的稀疏检索和混合检索中的 RRF 融合在处理同义词时的能力有什么本质区别？",
        ground_truth="BM25 基于精确关键词匹配，无法处理同义词——如果查询用'向量数据库'但文档写的是'Embedding 存储系统'，BM25 无法匹配。混合检索通过融合稠密向量检索（如 BGE Embedding）来弥补这一缺陷：稠密检索在向量空间中可以找到语义相近的表达。RRF 融合将两者排名结合，既保留 BM25 的精确匹配能力，又获得稠密检索的语义理解能力。",
        reference_contexts=["BM25 特别擅长处理精确关键词匹配查询",
                           "混合检索（Hybrid Search）是融合稠密向量检索和稀疏关键词检索的技术",
                           "RRF 公式为：score(d) = SUM(1/(k+rank_i(d)))",
                           "BGE 通过对比学习训练，使语义相近的文本在向量空间中距离接近"],
    ),

    # ── 新增负样本题（7 题）─ 答案不在知识库中 ──────────────────────

    EvalSample(
        question="TensorFlow 2.0 和 PyTorch 在分布式训练方面有什么具体差异？",
        ground_truth="",
        reference_contexts=[],
    ),
    EvalSample(
        question="Qdrant 企业版的价格是多少？支持哪些云平台？",
        ground_truth="",
        reference_contexts=[],
    ),
    EvalSample(
        question="Google 最新发布的 Gemma 2 模型在哪些基准测试上超过了 GPT-4？",
        ground_truth="",
        reference_contexts=[],
    ),
    EvalSample(
        question="这个知识库有多少篇中文文档？作者是谁？",
        ground_truth="",
        reference_contexts=[],
    ),
    EvalSample(
        question="ReAct 模式和 OpenAI 的 Function Calling 哪种方案更适合构建生产级 Agent？",
        ground_truth="",
        reference_contexts=[],
    ),
    EvalSample(
        question="Milvus 和 Pinecone 在 Serverless 架构上谁更有优势？",
        ground_truth="",
        reference_contexts=[],
    ),
    EvalSample(
        question="LangChain 和 LangGraph 在构建 Agent 时各有什么优缺点？",
        ground_truth="",
        reference_contexts=[],
    ),
]
