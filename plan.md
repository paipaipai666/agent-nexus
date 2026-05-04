# AgentNexus — 企业级多智能体任务协同 CLI 工具

## 完整落地计划

> **目标**：用 13 周时间，从零构建一个工程级 Multi-Agent CLI 应用，所有依赖本地运行、零部署成本、`pip install` 即可使用，同步掌握 Agent 开发核心技能，具备在中国大陆应聘 Agent 开发工程师的竞争力。

------

## 一、项目定位

### 解决的核心问题

现实中的业务任务往往需要多个步骤、多种工具、中间还需要决策判断——单个 LLM 请求无法处理。AgentNexus 解决的是：**如何让复杂任务可靠地自动化执行完**，并且做到可观测、可评估、可部署。

与 Demo 级项目的本质区别：

| 维度       | Demo 项目        | AgentNexus                               |
| ---------- | ---------------- | ---------------------------------------- |
| Agent 出错 | 直接返回错误结果 | Critic Agent 评分，不达标重试            |
| 任务复杂度 | 单步、单工具     | 多步、多 Agent 并行协作                  |
| 可观测性   | 无               | Langfuse 全链路追踪                      |
| 评估体系   | 主观感受         | RAGAS 自动量化指标                       |
| 分发方式   | 手动拷贝代码     | pip install / 单文件可执行文件，开箱即用 |

### 典型使用场景

- **信息聚合研究**：输入"分析特斯拉最新财报并写投资简报"，自动完成搜索 → 分析 → 画图 → 生成报告全流程
- **数据分析**：上传 CSV，自动写 SQL/Python 分析，输出图表与结论
- **企业知识问答**：上传内部文档，精确回答需要理解上下文的复杂问题
- **自动化工作流**：定时任务 + 条件触发 + 多步骤执行

------

## 二、系统架构

```
用户在终端输入命令
        │
        ▼
┌───────────────────────────────────┐
│         CLI 层（Typer + Rich）     │
│  命令解析 / 流式渲染 / 交互确认    │
└───────────────┬───────────────────┘
                │
                ▼
┌───────────────────────────────────┐
│       Orchestrator Agent          │  ← LangGraph 状态机
│       任务规划 + 路由调度          │
└──────┬──────┬──────┬──────────────┘
       │      │      │
       ▼      ▼      ▼
  Research  Coder  Analyst      ← 专业 Agent
  搜索抓取  代码执行  分析报告
       │      │      │
       └──────┴──────┘
              │
              ▼
        Critic Agent             ← 质量评估 + 触发重试
              │
              ▼
┌───────────────────────────────────┐
│           本地基础设施层           │
│  ChromaDB  │  SQLite  │  日志文件 │
│  （向量库） │ （记忆）  │ （追踪）  │
└───────────────────────────────────┘
```

### 核心组件说明

**CLI 层**：用 Typer 定义命令结构，Rich 负责终端渲染——实时流式打印 Agent 思考步骤、进度条、彩色分级日志。用户体验对标 Claude Code / Aider。

**Orchestrator Agent**：接收用户任务，使用 LangGraph 状态机进行任务分解，动态路由到专业 Agent，处理异常与重试逻辑。

**Research Agent**：负责信息获取，工具包括 Web 搜索、网页抓取、RAG 知识库检索。

**Coder Agent**：负责代码生成与执行，在本地沙箱环境中运行 Python，支持数据处理、可视化、文件操作。

**Analyst Agent**：负责信息整合与报告生成，将多源数据综合为结构化 Markdown 输出，直接写入本地文件。

**Critic Agent**：对其他 Agent 的输出进行质量评分（相关性、准确性、完整性），低于阈值触发 Orchestrator 重新规划。

**RAG 系统**：文档摄取 pipeline → 向量化存储（本地 ChromaDB）→ 混合检索（稠密 + BM25）→ Reranker 精排。全部数据存于用户本地 `~/.agentnexus/` 目录。

**记忆系统**：短期记忆（内存中的滚动摘要）+ 长期记忆（本地 SQLite，用户偏好与任务历史持久化）。

**可观测性**：结构化日志写入本地 JSONL 文件，支持离线分析；同时提供 `nexus logs` 命令在终端可视化查看历史 trace。

------

## 三、技术栈

### Agent 框架

| 技术          | 用途         | 选择理由                                  |
| ------------- | ------------ | ----------------------------------------- |
| **LangGraph** | 主编排框架   | 状态机显式控制 Agent 流程，是面试核心考点 |
| **LangChain** | 工具链       | RAG、Tool 调用生态完善                    |
| **LiteLLM**   | LLM 统一接口 | 一套代码兼容所有模型，方便切换            |

### LLM 模型

| 模型                 | 用途                           |
| -------------------- | ------------------------------ |
| **DeepSeek V3 / R1** | 主力模型，国内首选，性价比最高 |
| **通义千问 Max**     | 阿里云部署场景，企业常用       |
| **GPT-4o**           | 对比基准，展示多模型兼容性     |

### 存储与检索（全部本地）

| 技术          | 用途                | 说明                                                       |
| ------------- | ------------------- | ---------------------------------------------------------- |
| **ChromaDB**  | 向量数据库          | 纯本地，无需启动额外服务，数据存于 `~/.agentnexus/chroma/` |
| **SQLite**    | 长期记忆 + 任务历史 | 单文件数据库，零配置，随应用一起分发                       |
| **rank-bm25** | 稀疏检索            | 纯 Python 实现，无外部依赖                                 |

### CLI 层

| 技术               | 用途                                       |
| ------------------ | ------------------------------------------ |
| **Typer**          | CLI 命令结构定义，自动生成 `--help` 文档   |
| **Rich**           | 终端渲染：流式输出、进度条、表格、彩色日志 |
| **Prompt Toolkit** | 交互式多行输入、历史记录、自动补全         |

### 可观测性与评估

| 技术                  | 用途                                      |
| --------------------- | ----------------------------------------- |
| **结构化 JSONL 日志** | 本地 trace 文件，记录每个 span 的完整信息 |
| **RAGAS**             | RAG 质量自动评估（召回率、忠实度）        |
| **`nexus logs` 命令** | 终端内可视化查看历史任务 trace            |

### 打包与分发

| 技术                    | 用途                                                  |
| ----------------------- | ----------------------------------------------------- |
| **uv / pyproject.toml** | 现代 Python 包管理，`pip install agentnexus` 一键安装 |
| **PyInstaller**         | 打包为单文件可执行文件，用户无需安装 Python           |
| **GitHub Actions**      | push 触发 lint + 测试 + 打包产物上传 Release          |

------

## 四、学习路线（13 周）

### 第 1-2 周：LLM 核心 + 单 Agent

**本阶段学习内容**

- OpenAI / 通义千问 / DeepSeek API 使用
- Prompt Engineering 系统性方法（few-shot、CoT、结构化输出）
- Function Calling / Tool Use 机制
- ReAct 推理框架（Reasoning + Acting）
- Pydantic 结构化输出与数据校验

**项目交付物**

- 搭建项目骨架：Typer CLI 入口 + 配置管理（`~/.agentnexus/config.yaml` + Pydantic Settings）
- 实现带工具的单 Agent：能调用 Web 搜索 + 本地 Python 代码执行
- ReAct 循环：Think → Act → Observe → 终止条件判断
- Rich 流式渲染：Agent 每一步思考实时打印到终端，带颜色分级
- 单元测试框架：pytest + 对 LLM 输出的 mock 策略

**学习资源**

- LangChain 官方文档 Tool Use 章节
- DeepLearning.AI《Functions, Tools and Agents with LangChain》

------

### 第 3-5 周：知识检索系统（Grep + RAG 双路由）

**本阶段学习内容**

- ripgrep / Python `subprocess` 调用与结果解析
- Embedding 模型原理与选型（BGE、text-embedding-3-small）
- 向量数据库核心概念：ChromaDB 集合、索引、过滤
- 分块策略：语义分块 vs 固定窗口 vs 递归分块
- 混合检索：稠密向量 + 稀疏检索（BM25）
- 重排序 Reranker：BGE-Reranker / Cohere Rerank
- 检索路由策略：如何判断走哪条路径

**核心设计：双路由检索**

不是所有查询都需要语义检索，错误的工具选型会带来不必要的 embedding 开销和召回噪声：

```
用户查询
   │
   ▼
[路由判断]
   │
   ├── 代码 / 日志 / 结构化文本？  →  ripgrep 精确匹配  ──┐
   │                                                        │
   └── 非结构化文档 / 自然语言问题？ →  RAG 语义检索  ──────┤
                                                            │
                                        命中率 < 阈值？     │
                                        └── fallback 到另一路径
                                                            │
                                                            ▼
                                                     Reranker 精排
                                                            │
                                                            ▼
                                                     返回 top-k 结果
```

| 场景                    | 优先策略              | 理由                               |
| ----------------------- | --------------------- | ---------------------------------- |
| 知识库是代码 / 配置文件 | ripgrep               | 精确匹配快、准，无向量化开销       |
| 知识库是 PDF / 文章     | RAG 语义检索          | 用户措辞与文档措辞不同，需语义理解 |
| 不确定 / 命中率低       | 先 Grep，fallback RAG | 兼顾速度与召回率                   |

**项目交付物**

- 文档摄取 pipeline：PDF/Markdown/代码 → 清洗 → 分块 → 向量化 → 入 ChromaDB（数据存本地）
- 检索路由器：根据知识库类型和查询特征自动选择 Grep 或 RAG，命中率低于阈值时 fallback
- ripgrep 工具封装：调用本地 `rg` 命令，结果解析为统一的 `Document` 格式
- RAG 检索接口：混合检索（稠密 + BM25）+ Rerank 两阶段，与 Grep 结果共享同一接口
- CLI 命令：`nexus kb add ./docs/` 一键摄取，`nexus kb list` 查看知识库状态
- RAG 评估：接入 RAGAS，量化 faithfulness / answer_relevancy / context_recall
- 写一份评估报告，对比 Grep / RAG / 双路由三种策略在不同类型知识库上的指标差异

**关键工程点**

```python
# 双路由检索示例结构
async def retrieve(query: str, kb_type: KBType, top_k: int = 5) -> list[Document]:
    if kb_type == KBType.CODE or kb_type == KBType.STRUCTURED:
        results = grep_search(query, top_k=top_k)
        if len(results) >= top_k * 0.5:   # 命中率足够，直接返回
            return results
        # 命中率不足，fallback 到语义检索
    
    dense_results = await chroma_client.search(query_vector=embed(query), limit=top_k * 2)
    sparse_results = bm25_index.search(query, top_k=top_k * 2)
    merged = reciprocal_rank_fusion(dense_results, sparse_results)
    return reranker.rerank(query, merged, top_n=top_k)
```

------

### 第 6 周：记忆系统

**本阶段学习内容**

- 会话窗口管理与 token 压缩策略
- 摘要式记忆 vs 向量检索式记忆的适用场景
- SQLite 数据库设计（轻量、单文件、零配置）
- 本地记忆的隐私边界设计

**项目交付物**

- 短期记忆：会话超过 token 阈值时自动调用 LLM 生成滚动摘要，存于内存
- 长期记忆：任务完成后将关键信息写入本地 SQLite（`~/.agentnexus/memory.db`），包含用户偏好、实体信息、历史结论
- 记忆注入：每次对话开始前，从 SQLite 检索相关长期记忆并注入 system prompt
- CLI 命令：`nexus memory list` 查看记忆、`nexus memory clear` 清空、`nexus memory add` 手动添加

------

### 第 7-9 周：Multi-Agent 编排

**本阶段学习内容**

- LangGraph 核心概念：StateGraph、Node、Edge、条件路由
- 并行分支与 Fan-out / Fan-in 模式
- Human-in-the-loop：断点（interrupt）与审批流
- Agent 间通信协议设计
- 错误处理与重试机制

**项目交付物**

用 LangGraph 实现完整的多 Agent 状态机：

```
START
  │
  ▼
[plan_task]          ← Orchestrator 分解任务
  │
  ├──────────────────┐
  ▼                  ▼
[research]        [code_execute]    ← 并行执行
  │                  │
  └────────┬─────────┘
           ▼
       [analyze]      ← 合并结果
           │
           ▼
       [critique]     ← 质量评估
           │
    ┌──────┴──────┐
    ▼             ▼
[approved]    [retry]  ← 条件路由
    │             │
    ▼             └──→ [plan_task]
  END
```

- 并行子图：Research + Coder 并行，通过 `Send()` API 实现 map-reduce
- Critic 评分阈值：低于 7 分触发重试，最多重试 3 次
- Human-in-the-loop：高风险操作（文件写入、外部 API 调用）暂停，在终端打印确认提示等待用户输入
- 状态持久化：使用 LangGraph SQLiteCheckpointer 将状态存入本地 SQLite，支持任务中断后恢复

------

### 第 10-11 周：可观测性 + 评估体系

**本阶段学习内容**

- 结构化日志设计：JSONL 格式、span 层级、trace_id 关联
- LLM-as-Judge 评估方法与 prompt 设计
- 数据集管理与回归测试设计
- Token 成本统计与优化思路

**项目交付物**

- 本地 trace 系统：每个 Agent 节点写入结构化 JSONL 日志（`~/.agentnexus/traces/`），记录输入、输出、延迟、token 消耗
- `nexus logs` 命令：用 Rich 在终端渲染历史任务的 trace 树，支持按任务 ID / 时间过滤
- `nexus eval` 命令：对内置评估数据集跑 RAGAS 评估，输出指标报告到终端和本地 JSON 文件
- 自动评估 pipeline：GitHub Actions 在 PR 合并时自动运行 `nexus eval`，指标回归则标记失败
- Token 成本统计：`nexus stats` 命令展示历史任务的 token 消耗与估算费用

**关键指标体系**

| 指标         | 说明                      | 目标值   |
| ------------ | ------------------------- | -------- |
| 任务完成率   | 成功完成的任务比例        | > 85%    |
| RAG 召回 F1  | 知识检索准确率            | > 0.80   |
| 平均重试次数 | Critic 触发重试的平均次数 | < 1.5    |
| P95 延迟     | 95% 任务的完成时间        | < 30s    |
| 每任务成本   | 平均 token 费用           | 持续优化 |

------

### 第 12-13 周：工程化 + 打包分发

**本阶段学习内容**

- Python 包工程：`pyproject.toml`、`uv`、入口点（entry points）定义
- 异步编程：asyncio + 流式输出在 CLI 中的实践
- 配置管理：首次运行引导、`nexus init` 交互式配置向导
- PyInstaller 打包：生成单文件可执行文件，处理依赖打包问题
- GitHub Actions：CI 流水线 + Release 自动发布

**项目交付物**

- 完整 CLI 命令体系：

  ```
  nexus run "分析特斯拉财报并写简报"   # 执行任务nexus chat                            # 进入交互对话模式nexus kb add ./docs/                  # 添加知识库文档nexus kb list                         # 查看知识库nexus memory list                     # 查看长期记忆nexus logs [--task-id xxx]            # 查看历史 tracenexus eval                            # 运行评估数据集nexus stats                           # 查看 token 消耗统计nexus config                          # 修改配置（模型、API Key 等）nexus init                            # 首次初始化引导
  ```

- `pip install agentnexus` 可用：发布到 PyPI（或 GitHub Packages），README 有一键安装说明

- 单文件可执行文件：通过 GitHub Actions 自动打包 macOS / Linux 版本，上传到 Release

- CI/CD：push 触发 ruff lint + pytest + eval 数据集回归，全绿才能合并

**关键工程点**

```toml
# pyproject.toml
[project.scripts]
nexus = "agentnexus.cli:app"   # pip install 后直接使用 nexus 命令

[project.optional-dependencies]
dev = ["pytest", "ruff", "ragas"]
```

------

## 五、项目目录结构

```
agentnexus/
├── agentnexus/
│   ├── cli/
│   │   ├── __init__.py          # Typer app 入口
│   │   ├── run.py               # nexus run 命令
│   │   ├── chat.py              # nexus chat 命令
│   │   ├── kb.py                # nexus kb 命令组
│   │   ├── logs.py              # nexus logs 命令
│   │   ├── eval.py              # nexus eval 命令
│   │   └── render.py            # Rich 渲染工具函数
│   ├── agents/
│   │   ├── orchestrator.py      # LangGraph 主状态机
│   │   ├── research_agent.py
│   │   ├── coder_agent.py
│   │   ├── analyst_agent.py
│   │   └── critic_agent.py
│   ├── tools/
│   │   ├── web_search.py
│   │   ├── code_executor.py     # 本地沙箱执行
│   │   ├── file_ops.py
│   │   └── api_caller.py
│   ├── rag/
│   │   ├── ingestion.py         # 文档摄取 pipeline
│   │   ├── retriever.py         # 双路由检索：Grep / RAG + fallback + Rerank
│   │   └── evaluator.py         # RAGAS 评估
│   ├── memory/
│   │   ├── short_term.py        # 内存滚动摘要
│   │   └── long_term.py         # SQLite 长期记忆
│   ├── observability/
│   │   ├── tracer.py            # JSONL 结构化日志写入
│   │   └── stats.py             # Token 成本统计
│   └── core/
│       ├── config.py            # Pydantic Settings + ~/.agentnexus/config.yaml
│       └── llm.py               # LiteLLM 统一封装
├── tests/
│   ├── unit/
│   ├── integration/
│   └── evals/                   # 评估数据集（JSONL）
├── .github/
│   └── workflows/
│       ├── ci.yml               # lint + test + eval
│       └── release.yml          # 打包 + 发布 PyPI + Release
├── pyproject.toml
└── README.md
```

------

## 六、关键工程实践

### 不要用高层封装写核心逻辑

用 LangGraph 显式写出状态机的每一条边和每个节点，而不是依赖 `AgentExecutor` 之类的封装。面试时你能清晰解释 Agent 在任何状态下的行为，这比"我用封装类跑起来了"高出一个维度。

### 测试策略

```
单元测试（mock LLM）  →  集成测试（真实 LLM，小数据集）  →  评估测试（RAGAS 指标）
```

- 单元测试：mock 掉所有 LLM 调用，测试业务逻辑
- 集成测试：用真实 LLM 跑 10-20 个固定 case，断言输出格式正确
- 评估测试：每次 PR 跑完整评估数据集，指标回归则阻塞合并

### Prompt 工程规范

- 所有 prompt 以 `.txt` / `.jinja2` 文件形式存在 `agentnexus/prompts/` 目录，不硬编码在代码里，纳入 Git 版本管理
- 每次修改 prompt 后，必须对比新旧版本在评估数据集上的指标变化（通过 `nexus eval` 命令）
- System prompt 和 user prompt 分离，便于复用和测试

### 错误处理规范

```python
# Agent 节点统一错误处理模式
async def research_node(state: AgentState) -> AgentState:
    try:
        result = await research_agent.run(state.current_task)
        return state.update(research_result=result, retry_count=0)
    except ToolExecutionError as e:
        if state.retry_count < MAX_RETRIES:
            return state.update(retry_count=state.retry_count + 1, error=str(e))
        return state.update(status="failed", error=str(e))
```

------

## 七、求职策略

### 面试高频考点对应关系

| 面试问题                      | 项目中的对应实现                                            |
| ----------------------------- | ----------------------------------------------------------- |
| 如何设计 Multi-Agent 协作？   | LangGraph 状态机 + Orchestrator 路由逻辑                    |
| RAG 效果不好怎么优化？        | 混合检索 + Rerank + RAGAS 量化评估的优化闭环                |
| Agent 出错了怎么处理？        | Critic Agent 评分 + 条件重试 + 最大重试次数限制             |
| 如何保证 Agent 系统的可靠性？ | 结构化 JSONL trace + 自动评估 pipeline + 指标回归拦截       |
| 如何分发给别人用？            | pyproject.toml 打包发布 PyPI + PyInstaller 单文件可执行文件 |

### 项目包装建议

- **README 必须有**：架构图、功能演示 GIF（终端录屏用 Terminalizer）、`pip install agentnexus` 一键安装、评估指标数据
- **录演示视频**：3 分钟展示 `nexus run` 处理复杂任务的完整终端流程，发到 B 站作为背书
- **写技术博客**：至少 2 篇，推荐题目：「我如何用 LangGraph 设计 Multi-Agent 记忆系统」「RAG 召回率从 62% 到 88% 的优化过程」
- **量化成果**：简历上写"RAG 召回 F1 从 0.62 提升至 0.88"，远比"实现了 RAG 系统"有说服力
- **trace 日志截图**：将 `nexus logs` 的终端渲染效果截图放进面试 PPT，直观展示可观测性工程能力

### 目标公司参考

**AI 原生公司**：月之暗面（Kimi）、百川智能、智谱 AI、面壁智能、MiniMax

**大厂 AI 部门**：字节跳动豆包、阿里通义、腾讯混元、百度文心、华为盘古

**Agent 应用创业公司**：扣子（字节）、Dify 生态企业、各垂直行业 AI Agent 创业公司

### 薪资参考范围（2025 年）

| 级别              | 经验     | 薪资范围  |
| ----------------- | -------- | --------- |
| 初级 Agent 工程师 | 0-1 年   | 20-35k/月 |
| 中级 Agent 工程师 | 1-3 年   | 35-60k/月 |
| 高级 Agent 工程师 | 3 年以上 | 60k+/月   |

> 有扎实的工程项目背书（完整 GitHub + 博客 + 演示视频），0 经验拿到 25-30k 是完全可能的。

------

## 八、每周检查清单

完成每个阶段后，用以下标准检验自己：

- [ ] 代码有单元测试，核心逻辑覆盖率 > 70%
- [ ] 有 README 描述本阶段新增的功能
- [ ] 至少跑通 1 个端到端的真实场景
- [ ] 能用自己的话向别人解释这周写的核心代码
- [ ] 新增功能在本地 trace 日志中有对应记录，`nexus logs` 可以看到
