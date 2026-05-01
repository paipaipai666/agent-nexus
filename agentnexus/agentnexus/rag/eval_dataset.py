from agentnexus.rag.evaluator import EvalSample


KNOWLEDGE_BASE = [
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
    "逐步切分。语义分块（Semantic Chunking）基于嵌入相似度或自然段落边界，能最好地保留语义完整性。",

    "LangGraph 是一个用于构建有状态多智能体应用的 Python 框架。它的核心概念包括：StateGraph（"
    "状态图定义 Agent 流程）、Node（处理节点，执行具体逻辑）、Edge（边，定义节点间流转）和"
    "Conditional Edges（条件边，支持动态路由）。LangGraph 内置了 Checkpointer 机制实现状态持久化。",

    "ReAct（Reasoning + Acting）是 Google DeepMind 提出的 Agent 推理框架。Agent 在推理（"
    "Thought）和行动（Action）之间交替循环：先思考需要做什么，再调用工具执行，观察结果后继续"
    "推理，直到得出最终答案。这个模式让 Agent 能够处理需要多步推理和外部信息获取的复杂任务。",

    "向量量化（Vector Quantization）是降低向量存储和检索成本的技术。标量量化（Scalar Quantization）"
    "将 float32 压缩为 int8，内存减少 75% 且查询速度提升 3-4 倍，精度损失极小。乘积量化（Product "
    "Quantization）将向量分解为子向量分别量化，适用于超大规模索引（亿级以上）。",

    "Embedding 模型的核心原理是通过对比学习（Contrastive Learning）训练，使语义相近的文本在向量"
    "空间中距离接近。训练时使用正例对（相关文本）和负例对（不相关文本），优化目标是让正例对的相似度"
    "高于负例对。常用的损失函数包括 InfoNCE 和 Triplet Loss。",

    "Reranker（重排序器）采用 Cross-Encoder 架构，将查询和文档拼接后通过 Transformer 编码器进行"
    "深度语义交互计算。与 Bi-Encoder（如 BGE Embedding）相比，Cross-Encoder 精度高出 5-15%，"
    "但计算成本也高出 10-100 倍。因此通常用 Bi-Encoder 做粗排，Cross-Encoder 对 Top-K 候选精排。",

    "jieba 分词是 Python 中最流行的中文分词工具。它支持精确模式（最精确的切分）、全模式（所有"
    "可能的词）、搜索引擎模式（在精确基础上对长词再切分）和 paddle 模式（利用深度学习）。在 RAG "
    "系统中，jieba 用于 BM25 的中文分词，将查询和文档切分为有意义的词条。",
]


EVAL_SAMPLES = [
    EvalSample(
        question="Qdrant 使用什么索引算法？支持哪些距离度量？",
        ground_truth="Qdrant 使用 HNSW 索引算法，支持 COSINE、EUCLIDEAN 和 DOT 三种距离度量。",
        reference_contexts=["Qdrant 是一个高性能向量数据库，使用 HNSW"],
    ),
    EvalSample(
        question="BM25 算法中的 k1 和 b 参数分别控制什么？",
        ground_truth="k1 控制词频饱和度（常用 1.2-2.0），b 控制文档长度归一化程度（常用 0.75）。",
        reference_contexts=["BM25（Best Matching 25）是一种基于概率检索框架"],
    ),
    EvalSample(
        question="什么是混合检索？它的优势是什么？",
        ground_truth="混合检索融合稠密向量检索和稀疏关键词检索，常见融合算法包括 RRF 和加权融合，能将召回率提升 10-20%。",
        reference_contexts=["混合检索（Hybrid Search）是融合稠密向量检索"],
    ),
    EvalSample(
        question="RRF 融合算法的计算公式是什么？k 默认值是多少？",
        ground_truth="RRF 公式为 score(d) = SUM(1/(k+rank_i(d)))，k 默认值为 60。",
        reference_contexts=["RRF（Reciprocal Rank Fusion，倒数排名融合）"],
    ),
    EvalSample(
        question="BGE-small-zh-v1.5 的向量维度是多少？查询时需要添加什么前缀？",
        ground_truth="向量维度为 512，查询时需要添加前缀：为这个句子生成表示以用于检索相关文章：",
        reference_contexts=["bge-small-zh-v1.5 是轻量级中文版本，向量维度为 512"],
    ),
    EvalSample(
        question="RAGAS 提供哪四个核心评估指标？",
        ground_truth="Faithfulness（忠实度）、Answer Relevancy（答案相关性）、Context Precision（上下文精度）和 Context Recall（上下文召回率）。",
        reference_contexts=["RAGAS（Retrieval Augmented Generation Assessment）"],
    ),
    EvalSample(
        question="文档分块有哪三种常见策略？哪种最好？",
        ground_truth="三种策略：固定窗口分块、递归分块、语义分块。语义分块能最好地保留语义完整性。",
        reference_contexts=["文档分块（Chunking）是 RAG pipeline 的关键预处理步骤"],
    ),
    EvalSample(
        question="LangGraph 的核心概念有哪些？",
        ground_truth="StateGraph、Node、Edge 和 Conditional Edges。",
        reference_contexts=["LangGraph 是一个用于构建有状态多智能体应用的 Python 框架"],
    ),
    EvalSample(
        question="ReAct 框架的工作流程是什么？",
        ground_truth="Agent 在推理（Thought）和行动（Action）之间交替循环，先思考再执行，观察结果后继续推理。",
        reference_contexts=["ReAct（Reasoning + Acting）是 Google DeepMind 提出的"],
    ),
    EvalSample(
        question="标量量化的主要优势是什么？",
        ground_truth="将 float32 压缩为 int8，内存减少 75%，查询速度提升 3-4 倍，精度损失极小。",
        reference_contexts=["向量量化（Vector Quantization）是降低向量存储和检索成本"],
    ),
    EvalSample(
        question="Embedding 模型的核心训练方法是什么？",
        ground_truth="通过对比学习训练，使用正例对和负例对，优化 InfoNCE 或 Triplet Loss。",
        reference_contexts=["Embedding 模型的核心原理是通过对比学习"],
    ),
    EvalSample(
        question="Reranker 使用什么架构？与 Bi-Encoder 相比如何？",
        ground_truth="Cross-Encoder 架构，精度高出 5-15%，但计算成本高 10-100 倍，通常用于精排阶段。",
        reference_contexts=["Reranker（重排序器）采用 Cross-Encoder 架构"],
    ),
    EvalSample(
        question="ReAct 与 LangGraph 有什么关系？",
        ground_truth="LangGraph 是实现 ReAct 等 Agent 模式的框架，通过 StateGraph 定义 Agent 流程，支持条件路由和状态持久化。",
        reference_contexts=["LangGraph 是一个用于构建有状态多智能体应用", "ReAct（Reasoning + Acting）"],
    ),
    EvalSample(
        question="为什么混合检索比单一检索效果好？",
        ground_truth="因为稠密检索擅长语义匹配但忽略关键词，BM25 擅长关键词匹配但忽略语义，两者融合取长补短。",
        reference_contexts=["混合检索（Hybrid Search）是融合稠密向量检索"],
    ),
    EvalSample(
        question="jieba 分词有哪几种模式？在 RAG 中用于什么？",
        ground_truth="精确模式、全模式、搜索引擎模式和 paddle 模式。在 RAG 中用于 BM25 的中文分词。",
        reference_contexts=["jieba 分词是 Python 中最流行的中文分词工具"],
    ),
]
