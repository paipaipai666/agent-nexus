# Changelog

All notable changes to AgentNexus will be documented in this file.

## [Unreleased]

### New Features

- **基准 CI 门禁 + API 接入(D 工程债清零)** — ① `nexus eval benchmark gate -s <suite> --min <v>`:对该套件最新 benchmark 报告(自动排除手工基线文件)逐数据集校验指标下限,不达标 exit 1,接入 CI 一行即可(`gate -s multihop --min 0.66`,当前 ndcg@10=0.7002 通过);② API:`GET /api/eval/benchmark/suites`(套件列表)、`POST /api/eval/benchmark/run`(检索评测,纯本地零 LLM,safe-expose,支持 suite/mode/datasets/limit/embedding_model)、`GET /api/eval/benchmark/reports`(报告列表带 ndcg 摘要),路由挂在 eval_routes,与自建任务 `/suites` 命名空间隔离。新增 `tests/unit/test_benchmark_gate.py`(7 用例:通过/失败/基线排除/显式路径/缺失指标/API 套件列表/非法 mode)
- **MultiHop-RAG 多跳检索基准** — 新套件 `nexus eval benchmark run --suite multihop`(yixuantt/MultiHopRAG,HF 版 609 篇 evidence-connected 语料 + 2556 多跳查询,inference/comparison/temporal/null 四题型,evidence 以 article url 为 doc-id 直通 qrels 精确匹配,零 LLM)。**首个检索真正参与的端到端协议**,系统组件贡献首次被量化:`dense` ndcg@10 0.608 / hits@10 0.961;`hybrid`(生产 RRF+dense+BM25 栈)**ndcg@10 0.700(+15%)/ hits@10 0.994 / recall@100 0.998**——BM25 融合把多跳 evidence 检索质量提升 9.2pp,生产栈价值直接证据。null_query(301 题,空 qrels)为后续拒答判分留位。新增 `agentnexus/eval/benchmarks/multihop_loader.py`;`load_multihop(spec=None, offline=)` 对齐 suite loader 调用约定
- **RGB judge 解耦（`run-rgb -g` / `-j <报告>`）** — 生成与判分可分离执行：`--generate-only` 只生成存盘（noise 任务 correct/judge_score 留空，rejection 任务规则判分照常），judge 留待 `--judge-only` 读取既往报告重判（rejection 任务为规则判分、无需重判），输出新 judge 的 accuracy 及与原始 judge 的一致率（agreement)。支撑错峰跑免费档（生成趁 Agnes 空闲、判分等 deepseek 低谷半价时段）与多 judge 校准对拍；另支持 `-e` 按任务切 embedding（英文任务用 bge-small-en-v1.5）。judge 逻辑提取为 `judge_noise_answer()` 供 e2e 主循环与 rejudge 共用；实测同 judge 重判 50 题 agreement 0.90（judge 噪声基线）。e2e 主循环改线程池并发（每题处理提取 `_process_rgb_query`,workers = clamp(round(rpm/3), 1, 6)，共享 RateLimiter 保证聚合仍 ≤rpm)——免费档高峰期单调用 10-20s latency 下，1200 题全量从串行 5-7h 压到 ~1.5h(RPM 顶格）；新增 4 个单测
- **RGB 端到端基准（噪声鲁棒/负样本拒答）** — 新增 `nexus eval benchmark run-rgb`(chen700564/RGB,AAAI 2024)：官方协议落地——每题按噪声率采样 5 篇文档（passage_num=5）进独立语料、临时 ChromaDB 隔离检索、官方中英指令模板生成、noise 任务 LLM judge 判正确性（复用 evaluator correctness prompt,≥0.5 算对）、rejection 任务官方关键词规则判拒答（零 LLM 调用）；按噪声率分档（0-0.5/0.5-0.8/0.8+）报告 accuracy。LLM 调用走单实例 token-bucket 限速器（默认 18 RPM）+ 429 指数退避（免费档 Agnes RPM 20 共享池）。实测 50 题冒烟（agnes-3.0-flash 生成 + agnes-2.5-flash judge）:accuracy 0.64（低噪声 1.00 / 中 0.95 / 高噪声 0.32），曲线与论文一致，judge 人工抽检 3 条判分合理；单题错误隔离、空生成/空 judge 计 error 不计 correct。新增 `agentnexus/eval/benchmarks/{rgb_loader,ratelimit,e2e}.py` 与 `tests/unit/test_benchmark_rgb_e2e.py`(9 用例），全套 21 用例通过
- **公开基准评测轨道（BEIR）** — 新增 `nexus eval benchmark list/run`：套件注册表 + BEIR 原始数据加载器（zip 下载/缓存/`--offline`）+ 文档级检索 runner + TREC 口径指标（NDCG@10 线性增益、二值 MAP，与 pytrec_eval 交叉验证一致）。`beir-lite` 套件（NFCorpus/SciFact/ArguAna）默认落地：与自建 60 题轨道并行，`dense` 模式对齐 BEIR 官方协议（文档级索引、NDCG@10 主指标、可与 leaderboard.mteb.org 对拍），`hybrid` 模式（RRF over dense+BM25）并列展示不混口径；doc-id 直通 qrels 精确匹配；零 LLM 调用、零新增依赖（BM25 惰性构建，dense 不加载 jieba）；`--ci` 报告写入 `traces/evals/benchmark-*.json`；`-e` 临时切换 embedding 模型。新增 `agentnexus/eval/benchmarks/`（schema/beir/suites/runner/metrics）与 `tests/unit/test_benchmark_*.py`（12 用例，含 ir_measures 交叉验证与端到端假 embedding 管道测试）
- **Hooks 系统全面升级（4 里程碑）** — ① 命令钩子执行器：config.yaml / `~/.agentnexus/hooks.yaml` / 项目级三层声明（Codex/Claude Code 收敛契约：stdin JSON、exit 2 阻断、stdout JSON update 合并），擦洗环境不传机密，超时杀整棵进程树（Windows taskkill /T）；项目级钩子 hash 指纹信任审查（`nexus hooks trust approve` / `POST /api/hooks/trust`），`hook_trust: bypass` 可放行。② 用户交互事件：`user_prompt_submit`（改写/拒绝输入）、`agent_stop`（否决最终答案强制继续，连续封顶 2 次，FSM 新增 STOP_VETOED 转移）、`permission_request`（Tool Gateway HITL gate 决策输入，deny 永远生效、allow 需 `hitl_hooks_may_approve`，审计记 `hitl_decision`）、`notification`。③ 护栏：`_MUTABLE_HOOKS` 只读强制（误改 warning 点名 changed keys）、异常首次 warning 后续计数、`AbortCode` 枚举 + `to_feedback()` 标准化阻断反馈（模型必然可见）、`PAYLOAD_SCHEMAS` 全 37 事件 payload 校验（`hook_schema_check`）。④ 可靠性：per-hook 超时（`register(timeout=)`）、`{traces_dir}/hooks.jsonl` journal（`GET /api/hooks` 可查）、trace 子 span、插件代码挂钩（`plugin.yaml entrypoint: hooks.py`，`plugins_allow_code` 显式 opt-in）
- 新增 `agentnexus/core/hook_schemas.py` / `hook_executor.py` / `hook_sources.py`、`agentnexus/cli/hooks.py`（list/trust/test）、`agentnexus/server/routes/hooks.py`、`docs/Hooks.md`（+en）、4 个测试套件 49 个用例
- **提示词系统五件套** — ① AGENTS.md 层级发现（Codex 语义：`~/.agentnexus/AGENTS.md` + 根→cwd 链，深层覆盖浅层，单文件 8k/总量 20k 预算，不读 CLAUDE.md）；② 系统上下文注入 `== 运行环境 ==` 块（OS/工作目录(会话 workspace 感知)/终端/本地时间精确到分钟带时区）；③ 消除工具描述双轨——NATIVE_TOOLS 策略只发 native schema，JSON 类策略保留文本清单作为唯一工具面，策略降级时自动重建消息块补回清单；④ 用户定制面 `append_system_prompt`（config.yaml，注入系统上下文末尾，高于平台默认准则、低于安全约束）；⑤ `react.txt`/`react_think.txt` 新增 `== 身份 ==` 与 `== 输出契约 ==` 段（Persona 未配置时的默认身份；语言跟随用户、简洁优先、最终答案自包含）
- **提示词 section 化与增量重建** — 上下文块改为命名 section 字典（`build_react_sections`/`diff_sections`/`assemble_react_messages`），消息按稳定→易变分组（rules/memory/conversation/static/tools/volatile）；重建时只重渲染含变更 section 的组，未变更组字节级一致，provider 前缀缓存命中到第一个变更点；每次重建记录 `prompt sections rebuilt: <变更名>` 调试日志
- 新增 `agentnexus/agents/runtime_context.py`（环境块 + AGENTS.md 发现）与 `tests/unit/test_runtime_context.py`（13 个行为测试）
- **`express_reaction`：模型对用户提问的表情反馈（娱乐功能）** — 新增 `agentnexus/tools/user_reaction.py` 与 `ReactionToolProvider`（`enable_user_reaction` 默认 `false`，关闭时工具不注册、模型不可见）。模型在 ReAct 循环内**自主决定**是否对用户提问表达反应，8 种枚举（👍👎🤩😔🤔😑😲🥱）加一句可选吐槽（50 字截断），不调用即无反应。GUI 侧不出现工具卡片：`server/routes/chat.py` 新增 `_GUI_EVENT_OVERRIDES` 映射表，`tool_start` 改写为 `user_reaction` 事件、`tool_done` 跳过，桌面端将表情渲染在用户消息气泡下方（`SessionManager.tsx` 事件监听 / `ChatPage.tsx` 气泡渲染），TUI 同步支持（`ChatMessage.attach_reaction`，TOOL_START/TOOL_DONE 特判跳过卡片）

### Changed

- **Desktop: v2 UI 全面重设计（Linear 式精致暗色）** — 桌面端前端按 `designs/mockups/v2-ui.yaml` 规范重构。新应用外壳：36px 自定义 titlebar（breadcrumb / 搜索触发 / 主题切换 / 窗口控制）+ VS Code 式 48px Activity Bar（Chat、Skills、MCP、Memory、Knowledge、Wiki、Plugins、Stats、Health、Alerts、Audit、Eval、Settings 全部提升为一级导航）+ 随路由切换的 232px Context Sidebar（仅 Chat 区显示项目/会话）+ 24px mono 状态栏（含上下文用量条）。新增 Ctrl+K 全局命令面板（分组 Recent/Navigation/Actions、实时过滤、全键盘操作）与 Ctrl+B 侧边栏折叠（250ms 宽度动画）。双主题 token 体系重写（暗色近黑四级明度阶梯 #0a0b0d→#1e2127、1px 半透明边框、暗色卡片 inset 顶部高光、accent 微渐变主按钮；亮色完整对照；默认改为暗色，切换 200ms 动画）。圆角体系 8/12/999。字体：Anton 移除，Inter + Geist Mono，基础字号 13px 紧凑密度。旧 /settings/* 子路由全部提升为顶级路由（/stats、/skills 等），SettingsLayout 删除。Chat 页用户消息改为右对齐弱化气泡、工具卡/输入框/空状态按 v2 组件规格重制；Stats 页改 segmented 时间范围 + v2 KPI 卡；其余页面卡片统一切到 surface-2 + card-highlight。

### Bug Fixes

- **WS 断线重连（R8）三处错位修复 + 桌面流式草稿 stale-read 回归修复** — 验证脚本 `experiments/verify_reconnect_resume.py` 坐实三处真实缺陷：①服务端 `_token_buffers/_token_cursors` 与 route 跳过计数混入 reasoning delta，桌面 `lastCursor` 只数 content token——断线重连时 resumeFrom 语义错位，队列只剩未发尾部时**过跳过丢内容**（R2c/T2c 型），队列未消费时**绝对偏移对队列相对计数**导致重复下发；②`reconnect_snapshot` 把 run 的 token 计数覆盖桌面消息 id 计数器，计数器回退产生 **React duplicate key**；③buffer 跨 step 累积，snapshot 把早前 step 的原文+reasoning 灌进当前草稿（重复+reasoning 显示为答案正文）。修复：`AgentEvent` 新增 per-run `tok_seq`（绝对序号，与队列消费进度解耦）作为 token 跳过键，buffer/cursor 只累计 content token，TOOL_START 时记录 `_token_step_base` 使 snapshot 内容限定当前 step（新增 `ChatService.get_run_token_snapshot` 收敛两处快照读取）；桌面端删除 msgCounter 覆盖、token 处理器跳过 `tok_seq <= lastCursor` 的 snapshot 已覆盖区间。同期修复本轮 step 重构引入的 **stale-read 回归**：token/reasoning 处理器的分支判断误用渲染驱动的 `sessionsRef`（滞后一渲染），第二个 token 重建草稿丢失前者——恢复同步 `stepAnswerIds` ref 并在全部 step 清除点重置，reasoning-order 测试补强内容完整性断言（多 token 累积、交错流、工具流）。新增 `tests/unit/test_reconnect_resume.py`（7 用例）与 `desktop/src/__tests__/reconnect-resume.test.tsx`（4 用例）
- **桌面端流式"答案先于思考展示"结构性修复（step 双桶模型）** — 根因：后端合法地先发正文 delta 再发推理 delta（`core/llm.py` 单 chunk 内 content 先于 reasoning_content 分发；交错型模型跨 chunk 交错），而桌面端 `SessionManager` 把思考卡与答案草稿 append 进同一扁平 `messages[]` 竞争位置，靠每个事件类型各自的 splice 补丁维持顺序（git 历史已三次同类补丁：4dcf7e1 / 7dd7634 / reasoning splice），`ANSWER_THOUGHT` 又被 `has_reasoning` 抑制无法兜底。重构为 Codex `TurnItem` 轻量版：新增 `SessionState.step = { process, answer }` 双通道暂存区，`commitStep()` 成为 step→history 唯一提交入口并固定 `[...process, answer]` 顺序——处理器只写 step 不再排序，新事件类型从构造上不可能再引入排序 bug；context `messages` 改为 committed+step 展平视图（消费端零改动），`setMessages`（历史重载）同步清空 step。同步修复：`answer` 空错误分支遗留未刷新 buffer、`error` 事件不打断 step 直接插卡等边界。对齐 Zed（`AgentMessageContent::Thinking` chunk 枚举）/ Codex（有序 `TurnItem` 列表）"顺序内生于数据模型"的业界做法。新增 `desktop/src/__tests__/reasoning-order.test.tsx`（7 用例：同 chunk 逆序、交错流、工具流提交与草稿丢弃、tool_call 截断、error 中断刷 step、历史重载清 step、正序对照）
- **ArguAna ndcg@10 对拍 -29% 定责:非系统排序问题,是评测口径差异** — 通过三层实验钉死:① Chroma HNSW 默认 vs 暴力全量余弦排序 ndcg 完全一致(0.4290 vs 0.4288,recall/precision/hits 全对齐),检索栈召回与排序无罪;② `hnsw:ef_search` 200/500 与默认零差异;③ BGE 查询指令("Represent this sentence...")非因(0.4344,+0.6pp)。剩余 gap(0.429 vs MTEB 官方 0.603)定位在 **leaderboard 评测口径**:同 revision 模型下 embedding 向量仍系统性不同(MTEB 用 HF 数据集 revision + 其评测器编码路径,我们用 BEIR 官方 zip),对拍协议必须锁定"数据 revision + 查询 prompt + 编码路径"三要素。教训沉淀:与 leaderboard 比分数前先比 ranked list 的召回/精确,一致却 ndcg 差=口径,不一致才是系统问题。bge-reranker-v2-m3 CPU 重排 12k pairs >5h 未完成——cross-encoder 在纯 CPU 部署不现实,rerank 收益验证留待 GPU。顺手产物:`scripts/arguana_rerank.py`(复用 chroma 临时索引、支持子集限速)
- **AgentLLM litellm fallback 重试路径必炸(Provider NOT provided)** — `self.model` 携带项目自研 provider 前缀(`zhipu/glm-4.7-flash`、`agnes/agnes-3.0-flash`、`deepseek-ai/...`)，直连 provider 失败后落入 litellm fallback 时原样传递：litellm 的 provider 前缀体系与之不兼容(`zhipu/` 命中残缺路由），直接 `BadRequestError: LLM Provider NOT provided`，瞬态故障(429/超时）的重试被变成确定性致命错误，judge 重试路径形同虚设。修复：fallback 统一改写为 `openai/<裸模型名>` + `api_base`（项目所有端点均为 OpenAI 兼容），`_litellm_can_route` 判定同步基于改写后名称。实测 `openai/glm-4.7-flash` + bigmodel api_base 真实路由可达（96s 返回，免费档 thinking 耗时）；新增 `tests/unit/test_llm_litellm_fallback.py`(7 用例：前缀改写矩阵 + zhipu/agnes 双端点 fallback 断言）
- **OpenAI 兼容端点模型名被误截断** — `OpenAIProvider` 对含 `/` 的模型名无条件 `split("/", 1)[1]`, 把 SiliconFlow 的 `deepseek-ai/DeepSeek-V4-Flash` 截成 `DeepSeek-V4-Flash` 发给端点 (400 Model does not exist)。改为白名单化剥离: 仅已知 litellm provider 前缀 (`deepseek/`, `openai/`, `zhipu/` 等) 才剥离; 命名空间式模型名 (SiliconFlow/Groq/OpenRouter 等) 保留全名。
- **流式 content 混入 think 标签残留** — SiliconFlow 部署的 DeepSeek-V4 在流式下 reasoning_content 正确分离, 但 delta.content 残留 `</think>` 片段 (如 `42</think>42`), 污染 ReAct JSON 解析。`OpenAIProvider` 累加 content 时剥离 think 标签。
- **Eval runner 工具集为空** — `ReActAgentRunner` 构造 `ToolRegistry()` 后未调 `register_all_tools`, eval 下 agent 拿到空工具集, tool_use 类 task 被系统性低估 (regression suite 实测 6/10 → 修复后工具真实执行)。现与 `AppRuntime` 对齐注册全部内置 provider 工具 (non_interactive)。
- **HumanEval 评分器 markdown 围栏 SyntaxError** — `HumanEvalEvaluator.evaluate()` 直接执行候选代码, 对 LLM 输出里几乎必然存在的 ` ```python ``` ` 围栏零容错, 真实模型成绩会被系统性压到 0。执行前现剥离围栏。官方 openai/human-eval 164 题 oracle 验证: gold 164/164 (100%), 语义破坏注入 24/24 检出、0 误报 (`experiments/run_humaneval_oracle.py`)

- **Eval runner 真实 transcript 时间轴** — `ReActAgentRunner` 此前在 eval 中直接调用 `agent.run()` 而没有建立 trace 上下文, agent 循环的 span 埋点全部不生效, `_collect_transcript` 只能用 `i * 2.0` 编造时间戳重建 transcript。现在 runner 会为每个 trial 建立真实 trace 上下文（已有上游 trace 时不劫持），transcript 直接来自真实 spans（墙钟时间、`latency_ms`、真实 input/output），并将 agent 埋点名 `plan_node` 映射为 grader 协议名 `llm`。无 trace 可用时的兜底重建不再编造时间戳，显式标注 `reconstructed: true`。trial metadata 新增 `trace_id` 供 replay/审计回溯。

### Testing

- 新增 `tests/unit/test_eval_runner_transcript.py`（5 个行为测试：真实时钟、grader 协议映射、trace_id 记录、不劫持上游 trace、兜底不编造时间）
- 新增 `experiments/e2e_eval_smoke.py` — mock 模型端到端验证 runner→trace→grader→report 全链路（含正反两个 grader 判定）
- 新增 `tests/unit/test_user_reaction.py`（15 用例：枚举校验、吐槽截断与空白归一、开关默认关闭 / 开启注册 / 配置读取异常降级、GUI 事件改写含 JSON 字符串参数与普通工具不受影响）

## [0.2.16] - 2026-09-10

### Changed

- **Desktop: Codex-style per-session projects** — the sidebar now groups chat sessions under project folders; a project is chosen when a chat is created and stays fixed for that chat's lifetime. Removed the global workspace switcher (`PUT /api/config/workspace` + `os.chdir`, `PUT /api/session/{id}/workspace` rebind) — sessions carry their own workspace folders, so switching projects never disturbs other sessions. Electron persists `{ projects, lastProject }` in `userData/workspace.json` (legacy single-workspace format is migrated).
### New Features

- **Desktop: multi-provider model switching** — configure multiple named LLM providers (name/model_id/base_url/api_key/timeout) in Settings → Model Providers, then switch the active model live from a picker in the chat input's HUD row. Switching hot-reconfigures the shared `AgentLLM` (no restart); the flat `llm_*` settings remain the default/fallback profile. New endpoints: `GET/PUT /api/config/llm/providers`, `POST /api/config/llm/active`. The model display moved from the status bar into the chat input area.

### Bug Fixes

- Fixed `/api/memory/short/history` resolving sessions against the process cwd — it now looks up the latest session across workspaces and adopts the session's stored workspace instead of re-registering it under the server cwd

## [0.2.15] - 2026-08-28

### New Features

- **Context-aware memory extraction and recall** — memory items carry an optional one-sentence context (scene/evidence); embeddings concatenate content+context; conflict check distinguishes same-scene contradictions from different-scene coexisting preferences; `memory_save` accepts `context`, `memory_search` renders it when present
- **Tool-selection keyword ordering** — more specific keywords match first (first-match-wins)

### Bug Fixes

- Fixed memory conflict detection — substring matching treated every "不矛盾" answer as a conflict, incorrectly superseding memories; now uses exact match
- Fixed stale security fragment assertions after prompt localization (`Security Fragment` → `安全原则`)

### Refactoring

- Split `ReActAgentRunner`/`TranscriptCollector` out of `graders.py` into `evaluation/runner.py`
- **Split `MemoryManager` monolith** (~620 → ~330 lines) — `compaction_engine.py` owns the 5-layer compaction pyramid and its state; `extraction_pipeline.py` owns the two-level extraction gate; manager is now a facade with attribute forwarding for backward compatibility
- Modernized typing annotations (`Optional[X]` → `X | None`) across fsm, react_types, tracer, runtime routes, eval CLI
- Removed pass-through `BM25Index` wrapper in `rag/retriever.py` — callers use `rag.ranking.BM25Index` directly
### Documentation

- **CLI help text standardized to Chinese** (CLI-018) — all `help=` strings and command docstrings across 19 CLI modules; proper nouns stay in English
- Backfilled CHANGELOG for v0.2.1–v0.2.14

### Tests

- Fixed pre-existing perf benchmark failures — fixtures predated CircuitBreaker; projection benchmarks now call the real `project_mild`/`project_aggressive` APIs

## [0.2.14] - 2026-06-21

### New Features

- **会话统计信息持久化** — session statistics persisted to database
- **会话级别运行时状态统计** — session-level runtime state stats support

### Bug Fixes

- Fixed missing `TOOL_DONE` events in batch tool execution (react_runtime)
- Fixed `_lookup_registry` mutating the static capabilities registry

### CI/CD

- Hardened CI/CD pipeline: caching, security audit, release safety, concurrency control, release version validation
- Removed pip-audit dependency audit job; skip editable installs in pip-audit; suppress chromadb CVE (no trust_remote_code)
- Fixed smoke test to use `version` command; UTF-8 encoding for pyproject.toml read on Windows

## [0.2.13] - 2026-06-20

### New Features

- **Tool system hardening** — structured errors, concurrent dispatch, and description boundaries

### Refactoring

- P2 structural improvements — split God Objects, extracted subpackages

### Bug Fixes

- Addressed 7 CRITICAL, 15 HIGH, 18 MEDIUM issues from code review, plus all LOW issues with added observability test coverage
- CORS now allows localhost on any port via regex

### Tests

- Added 160 tests for wiki and skills/router modules
- Adapted 7 integration/security tests to review fixes; mocked `_call_via_litellm` in stream fallback test

## [0.2.12] - 2026-06-18

### Refactoring

- 会话隔离的短时记忆管理 — session-isolated STM management in chat

### Bug Fixes

- Fixed tool cards stuck at "running" by bypassing journal parsing
- Fixed thinking content order — reset reasoning message ID on tool call

## [0.2.11] - 2026-06-18

### Bug Fixes

- Fixed TypeScript errors in desktop test files

## [0.2.10] - 2026-06-18

### New Features

- **Multi-session parallel execution** — concurrent agent runs across sessions
- **ChatService session isolation** — constructor supports per-session isolation

### Bug Fixes

- Embeddings prefer local cache to avoid HuggingFace timeouts
- Fixed history messages lost after session switch
- Fixed streaming output lost when switching sessions during an agent answer
- Fixed empty "New session" card appearing in sidebar on every startup
- Fixed streaming content loss after page navigation and sidebar not refreshing for new sessions
- Mocked `_recursive_split` in chunking tests to avoid langchain import timeout in CI

## [0.2.9] - 2026-06-15

### Performance

- **GPU embedding FP16 half-precision** — 2x throughput for RAG embedding

### New Features

- **STM 会话隔离与多会话并发支持** — session-isolated short-term memory
- **会话预览功能** — session preview with database migration
- **Per-session version manager** — independent conversation version manager per session
- **WebSocket 连接管理改进** — chat WebSocket connection management with acknowledgment bridging
- **`display_only` metadata** — agent display-only metadata support and final answer handling
- **Memory thread safety** — locks and atomic commits in memory system

### Bug Fixes

- Fixed thought content lost after session switch (GUI)
- Fixed streaming content lost on page switch (session)
- Fixed infinite repeated API refresh on new chat page
- Sidebar session card timestamp now only updates on user questions
- Fixed tool execution argument mapping in ReAct agent
- Fixed import order in memory version manager; fixed reranker loading in RAG retriever tests

### Refactoring

- Reworked `kb_search` tool to optimize retriever initialization

## [0.2.8] - 2026-06-11

### New Features

- **Wiki 回填命令** — CLI wiki backfill command

### Security

- Hardened path traversal defense: restored symlink detection, reliable normpath-based detection, consistent `resolve(strict=False)` for Windows 8.3 name compatibility, type-safe `Path.home` mock; skipped symlink test on Windows

### Tests

- Fixed all 149 pre-existing unit test failures
- Fixed integration and security test failures, async tests, trace_manager isolation, DoS test timeout, HuggingFace timeout
- Made cwd resilient across all modules and tests; fixed cwd isolation and Windows path normalization

### CI/CD

- Install rag/server/tui optional dependencies for test collection; fixed ruff lint and import sorting

### Documentation

- Updated README documentation links and test statistics

## [0.2.7] - 2026-06-09

### CI/CD

- Use glob pattern for chmod on renamed backend binaries

## [0.2.6] - 2026-06-09

### CI/CD

- Rename backend binaries with platform suffix to avoid release upload conflict

## [0.2.5] - 2026-06-09

### Bug Fixes

- Added homepage, author, description to desktop package for deb packaging

## [0.2.4] - 2026-06-09

### CI/CD

- Split build steps, per-platform electron-builder, chmod staged binary

## [0.2.3] - 2026-06-09

### CI/CD

- Stage backend binary to `desktop/backend/` before electron-builder
- Split build steps with diagnostics and fail-fast disabled

## [0.2.2] - 2026-06-09

### CI/CD

- Use bash shell for ls command on Windows runner

## [0.2.1] - 2026-06-09

### CI/CD

- Moved extraResources to platform blocks; fixed .github gitignore

## [0.2.0] - 2026-06-04

### 🧪 Evaluation Framework Overhaul — Anthropic Methodology Compliance

全面升级评估体系，使其符合 Anthropic "Demystifying evals for AI agents" 方法论框架。

### New Features

- **Unified Task/Trial/Grader abstractions** — `EvalTask`, `TrialResult`, `GraderConfig` dataclasses for standardized evaluation inputs
- **Evaluation Harness** — end-to-end `EvalHarness` that runs tasks concurrently, records all steps, grades outputs, and aggregates results
- **pass@k / pass^k statistics** — precise binomial estimator for multi-trial evaluation metrics
- **Composite Graders** — weighted, binary, and hybrid scoring modes combining multiple graders per task
- **8 built-in grader types** — transcript, tool_calls, state_check, static_analysis, llm_rubric, trajectory, hallucination, coherence
- **Capability vs Regression eval separation** — `EvalSuite` with `eval_type` field and `SuiteThresholds`
- **Baseline management** — save, load, compare baselines for regression detection
- **YAML task dataset format** — declarative task definitions with graders, reference solutions, and metadata
- **Eval dataset management** — `EvalDataset` class with load, filter, validate, stats
- **CLI eval task commands** — `nexus eval task list/show/validate/run`, `nexus eval suite list/run/show`
- **CLI baseline commands** — `nexus eval suite baseline list/save/compare`
- **Server API endpoints** — REST API for tasks, suites, baselines at `/api/eval/`
- **Desktop GUI Eval page** — full evaluation dashboard with tasks, suites, results, and baselines tabs
- **CI/CD eval gate** — `eval-gate` job in CI pipeline with dataset validation
- **Bootstrap confidence intervals** — for all LLM-judged metrics
- **Eval saturation monitoring** — automatic suggestions when evals are saturated

### Bug Fixes

- Fixed `TrajectoryGraderAdapter` — was calling non-existent `evaluate_transcript()`, now calls `_evaluate_one()` directly
- Fixed `HallucinationGraderAdapter` — was calling non-existent `detect()`, now calls `_evaluate_one()` directly
- Fixed `CoherenceGraderAdapter` — was calling non-existent `evaluate()`, now calls `_evaluate_one()` directly
- Implemented `CodeExecutionGrader` — runs test assertions in isolated subprocess
- Implemented `ReActAgentRunner` — real agent runner that integrates with ReActAgent
- Implemented `TranscriptCollector` — collects spans from TraceManager
- Added environment isolation (setup/teardown) to `EvalHarness`
- Fixed broken `eval_cmd.py` import
- Fixed syntax error in `graders.py` (Chinese text with unescaped braces)

### Files Added

- `agentnexus/evaluation/task.py` — Task/Suite/GraderConfig models + YAML loader
- `agentnexus/evaluation/trial.py` — TrialResult/TaskReport/GraderScore models
- `agentnexus/evaluation/graders.py` — Grader interface hierarchy (9 types + composite + agent runner + transcript collector)
- `agentnexus/evaluation/harness.py` — EvalHarness + SuiteReport + environment isolation
- `agentnexus/evaluation/statistics.py` — pass@k, pass^k, bootstrap CI, consistency, saturation
- `agentnexus/evaluation/baseline.py` — BaselineManager + RegressionReport
- `agentnexus/evaluation/dataset.py` — EvalDataset + JSONL migration
- `agentnexus/eval_tasks/` — 65 YAML task definitions across 6 suites (coding, tool_use, reasoning, conversation, rag, regression)
- `agentnexus/cli/eval/task.py` — CLI commands for task/suite/baseline management
- `agentnexus/cli/eval/transcript.py` — CLI commands for transcript viewing and analysis
- `scripts/migrate_eval_tasks.py` — Migration script for JSONL → YAML conversion
- `desktop/src/pages/EvalPage.tsx` — Desktop GUI evaluation page

### Files Modified

- `agentnexus/evaluation/__init__.py` — exports all new public API
- `agentnexus/cli/eval_cmd.py` — fixed broken import
- `agentnexus/cli/eval/__init__.py` — added task module registration
- `agentnexus/services/eval.py` — complete rewrite with task/suite/baseline support
- `agentnexus/server/routes/eval_routes.py` — 12 new REST endpoints
- `desktop/src/services/api.ts` — eval API client methods
- `desktop/src/App.tsx` — added /eval route
- `desktop/src/components/layout/Sidebar.tsx` — added Eval nav item
- `.github/workflows/ci.yml` — added eval-gate job

## [0.1.0] - 2026-06-02

### 🎉 Initial Release

First public release of AgentNexus — a production-grade, fully local ReAct single-agent CLI tool.

### Highlights

- **FSM-driven safety loop** — 16 states, 25 deterministic transitions govern the agent reasoning cycle
- **7-layer tool governance** — RBAC, schema validation, rate limiting, timeout, risk assessment, HITL, audit logging
- **213 security tests** — covering code execution, injection, sandbox escape, privilege escalation, and more
- **Fully local storage** — ChromaDB (vectors) + SQLite (relational) + JSONL (traces), nothing leaves your device
- **4-tier sandbox degradation** — E2B → bubblewrap/Seatbelt → Docker → local fallback

### Core Features

- ReAct agent with 3-tier LLM strategy degradation
- 17 built-in tools with full governance
- MCP integration (stdio/HTTP) with governance fusion
- Short-term memory (STM compression pyramid) + long-term memory (SQLite + ChromaDB)
- Knowledge base RAG with hybrid retrieval (dense + sparse + RBF + rerank)
- Code knowledge graph with semantic search
- Skill system with TF-IDF + learned reranker routing
- Sub-agent delegation for isolated task execution
- Observability: JSONL trace, token cost statistics, audit logs
- 8 built-in evaluators (agent, trajectory, hallucination, RAG, code, coherence, tool selection)

### Interfaces

- **CLI/TUI** — Terminal UI built with Typer + Rich + Textual
- **Desktop** — Electron + React 19 + TypeScript application
- **API Server** — FastAPI server with auth and rate limiting

### Platform Support

- Python 3.11, 3.12, 3.13
- Windows, Linux, macOS
- CI matrix: Ubuntu + Windows × Python 3.11/3.12/3.13
- Cross-platform binaries via PyInstaller
