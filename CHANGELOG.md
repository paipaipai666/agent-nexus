# Changelog

All notable changes to AgentNexus will be documented in this file.

## [Unreleased]

### New Features

- **子代理可见性（命名 + 侧栏实时状态 + 详情页轨迹 + 单独中断）** — `subagent_run` 子代理从此对桌面端完全可见：① 工具新增可选 `name` 参数（agent 自拟显示名，留空按 `role-N` 自动生成，session 内重名自动 `-2` 后缀）；② 子代理事件经新增的 `SubagentBridge`（挂在共享 `ToolRegistry` 上，与 `CancelBridge` 同单槽位模式：后启动的 run 覆盖，跨会话并发归属与取消传播同既有取舍）+ `agentnexus/services/subagents.py` 的线程安全注册表（`SubagentEntry`/`SubagentRegistry`/`SubagentRunContext`）转发进父 run 的既有事件队列，以 `subagent_event` GUI 事件（`started/thinking/reasoning/token/tool_call/tool_result/retry/finished`，每条自带 `subagent_id`/`name`/`status`）经会话 WS 推送到桌面端，并随 `_put_event` 持久化进 timeline store；③ 桌面端 InfoPanel 新增 **Subagents** 区块（Bot 图标 + 名称 + 状态点/中文标签：思考中/调用工具中/已中断/已完成/失败，行内方块按钮单独中断，点击跳转详情页），实时态走 SessionManager 新增的 per-session `subagents` Map（REST `GET /api/session/{id}/subagents`  hydration + 活体事件，页面刷新后从 timeline 事件回放缓存轨迹）；④ 新增路由 `/chat/:sessionId/subagent/:subagentId`（`SubagentPage`）展示子代理完整运行轨迹（思考卡/推理流/工具调用卡片/流式答案/重试与终止摘要）+ 头部状态徽章 + 中断按钮；⑤ 后端 `ChatService.cancel_subagent` + WS `{type:"cancel_subagent"}` 消息按 `threading.Event` 协作式中断指定子代理（子 agent cancel_checker 组合父 run 取消桥 + 自身事件，`set_cancel_checker` 转发 LLM 客户端即时关流），被打断返回「子代理已被打断」载荷且**不再触发 explorer 兜底重试**（顺带修复父 run 取消时子代理空转一次重试的浪费）；⑥ `ChatService._put_event` 加锁——子代理事件从 lane-pool 线程并发入队（此前 seq 计数/队列写仅单线程假设，`todo_change` 线程竞争一并修复），且锁内只做 seq 分配 + 队列写入，SQLite 落盘移出临界区（慢 commit 不再阻塞其他生产者）；子代理 `token`/`reasoning` 逐 token 事件**只走实时流不落 timeline 库**（对齐 `_SKIP_TYPES` 对主 agent 流式 token 的同款取舍，避免每 token 一次 fsync 在高频流下拖垮事件线程、饿死流内取消检查——实测修复前子代理流式 answer 期间点中断会因 commit 卡顿迟迟不生效）。实测端到端（SiliconFlow 真实模型）：单个子代理 515 条事件全链路（started→tool_call→token→finished(completed)）+ REST/timeline 对账一致；mid-run `cancel_subagent` → `finished(interrupted)` 且父 run 正常完成。新增 `tests/unit/test_subagent_visibility.py`(5 用例)与 `desktop/src/__tests__/subagent-meta.test.tsx`(3 用例)；`make_subagent_run` 新增 `subagent_bridge` 可选参数，TUI/eval 无桥接时行为完全不变
- **计划模式（Plan Mode）** — 会话级只读调研 + 计划审批退出。开启后 agent 只能调用标注 `read_only=True` 的工具（新增 `ToolMeta.read_only` 字段，21 个内置只读工具已标注：file/search/memory/todo 读取/browser 浏览类/computer 读取/express_reaction；写工具不标一律拦截），写入/执行类调用在 `ReActAgent._execute_tool` 被硬门禁拦下（`PLAN_MODE_BLOCKED`，fail-closed：未注册工具、未标注 MCP 工具、duck-typed mock 注册表一律不放行），提示词同步注入计划模式说明 + 动态只读工具清单。`subagent_run` 不限制角色但把子代理 `allowed_tools` 物理裁成只读集（覆盖 LLM 自带白名单，防 fork 子代理绕过）。agent 完成调研后调用 `exit_plan_mode(plan=...)` 提交最终计划文档：完整 Markdown 经 `[PLAN_REVIEW]` 标记走现有 HITL 确认链路（审批在 run 线程执行，工具保持 `concurrency_safe=False` 落顺序组），桌面端渲染 `PlanReviewCard` 完整展示计划供批准/拒绝；批准后模式退出并 best-effort 落盘 `<workspace>/.agentnexus/plans/plan-<ts>.md`；**被拒绝时回合立即结束并暂停等待用户指示**（binding 拒绝侧信道 → `_on_tools_requested` 走 `ANSWER_READY` 快路径，模型不在同回合自行重规划；用户下一条消息给出修改意见后再修订重提），审批通道异常按拒绝同样暂停，均不改变计划模式状态。MCP 工具解析 SDK `annotations.readOnlyHint` 映射 `read_only`（fail-closed），标注只读的 MCP 工具计划模式可用。手动开关：桌面 HUD 行 `PlanModeToggle` + `POST /api/session/{id}/plan-mode`（GET session 响应带 `plan_mode`）；run 中途切换经 live binding 即时生效；`delete_session` 清理状态。新增 `agentnexus/agents/plan_mode.py`、`tools/providers/plan_mode_provider.py`、`tests/unit/test_plan_mode.py`（42 用例，覆盖拒绝后暂停/重提/长计划不截断/mock fail-closed/落盘失败仍退出等上游失败模式回归）与 `desktop/src/__tests__/planMode.test.tsx`。新聊天界面不预建会话（首条消息才创建），预会话窗口经 `planModeArm` 意图存根支持先武装计划模式、会话创建并在首条消息发送前应用，toggle 不再置灰
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

- **Shell 沙箱策略化 + 检索管道阶段化 + 记忆门面/schema 收拢 + 桌面 ChatPage 瘦身（行为保持重构第三批）** — ① `tools/shell.py`：五 backend（bubblewrap/seatbelt/windows-native/docker/local_unsafe）实现 `SandboxBackend` Protocol（`available()`+`execute()`）并注册 `_BACKENDS` 查表，原五分支 if/elif 收敛；`auto` 降级链改 `_AUTO_CHAIN` 单循环（原两段逐字复制 try/except 合一）；Win32 ctypes 收进 `_WindowsNativeBackend` 调用面；黑名单/超时保持 dispatch 前通用策略（多测试钉住该分层）；② RAG 检索：`retriever.search_knowledge_base` 抽 `_expand_rag_queries`/`_fuse_dense_candidates` 命名阶段（HyDE 0.5、RRF k=60、boost 顺序逐点不变，@trace_span 保留）；`evaluator._retrieve` dense/hybrid 双路径归一 [(text,score)] 后共享 `_filter_and_budget`；`eval/benchmarks/runner.retrieve_all` 删自写 RRF、复用 `ranking.reciprocal_rank_fusion`（逐点核对公式一致）；③ `services/chat.py`：`send_message`（~242 行）拆 `_prepare_run`/`_wire_agent_for_run`/`_teardown_run` + `_RunSetup` dataclass + `_bind_optional`（7 处 hasattr 守卫收敛）；布线段留在 try 作用域（外移会改变异常场景事件+teardown 语义）；事件顺序 run_finished→run_persisted→哨兵逐点保持；④ memory 门面纠偏：`MemoryManager` 删 4 个死私有转发（`_fire_compact`/`_write_transcript`/`_restore_files`/`_drain_to_ltm`）与零调用的 `ctx_max` property 对、4 个 noqa F401 重导出（测试已迁移直 import）；schema 属主收拢——`versioned.py` 新增 `session_exists`/`delete_session`/`latest_workspace_for_sessions` 公开入口，`reflection.py` 不再越权 `long_term._conn` 查 conversation_sessions，routes/chat 删 session 不再裸连 sqlite3，routes/version undo/redo 复制块提取 `_restore_stm_from_checkpoint` 并改走 `ShortTermMemory.restore()`（锁内、token_count 刻意保持 stale 与原直写语义等价），reflection 手写 embedding 转换改用 `embedding_to_list`；⑤ 桌面 ChatPage 1546→1074 行：ToolCard/ToolGroup/MessageBubble/HudAction 移 `components/chat/`（逐字搬运），ThinkEffortControl/PlanModeToggle/InfoPanel 删 sessionId prop 改内部 `useSession()`（消除 prop drilling 与 InfoPanel prop+context 混用）；斜杠命令注册表/buildCommandDefs 属编排保留原位
- **API 路由错误处理与依赖注入统一（行为保持重构）** — `server/routes/*` 全面收敛：① 新增 `server/deps.py` FastAPI Depends 工厂（`get_runtime`/`get_ltm`/`get_mcp_manager`/`get_mcp_manager_optional`/`get_wiki_service`），消灭每 handler 重复的 lazy-import service-locator（memory 9 处 LTM 导入、mcp None 守卫、wiki 8 处每请求 `new WikiService()`——后者缓存挂到 `AppRuntime.wiki_service`，首次请求惰性构建）；② 异常→状态码映射收敛到 `error_handlers` 的 `APIError`（routes 不再裸抛 `HTTPException`，状态码与 message 不变；桌面 `request()` 双形状解析 `detail || error.message` 兼容；knowledge.py 一处 dict-detail 保留原样）；③ 删除重复端点 `GET /api/memory/list`（与 `/long` 函数体逐字相同；桌面端用 `/long`，集成测试同步改指）；④ memory 列表端点保留手写 `limit`（`paginate` 响应 schema 不同，不切换）。**注意**：错误响应体由 `{detail}` 变为 `{error:{code,message}}`，桌面客户端已兼容，外部脚本若解析 `detail` 需适配
- **Desktop: v3 "Calm Confidence" 视觉语言重设计（更大方/更高级）** — 按 `designs/mockups/v3-redesign-test.html` 规范落地（配套 `v3-redesign-notes.md` 记录决策溯源）。调研 Apple HIG'25（层级靠 layout/grouping 而非装饰）、Material 3（tonal 表面、hairline 层级、"less is more"）、ChatGPT UI（无色相纪律、6/8/12/16/24 节奏、hover veil）、Anthropic（单一克制强调色、近平面阴影）、Fluent 2（built for focus）与 Linear craft（设计过的动效曲线）后落地 8 项决策：① 排版扛层级——正文 13→15px/1.6，全局最小字号 12px，砍掉 uppercase 微标签，空态标题 22→27px；② 近平面——`--card-highlight` 内高光退役（token 置零，全 app 内联引用零改动失效），卡片只留 bg+1px hairline，投影只给真正浮动的输入区/弹层；③ 单一强调色——accent 只出现在主按钮/激活态/代码关键字，语义色缩成状态小圆点；④ 高对比中性画布——亮色纯 `#ffffff`、暗色深 `#0a0a0a`（大气来自色阶跨度而非色温）；⑤ 动作分级——HUD 次要动作（时间线/撤销/重做/检查点）从带标签药丸退成 28px 静音图标按钮（label 进 tooltip）；⑥ 渐进披露——工具卡默认折叠（运行中自动展开），**连续工具调用合并为一张分组卡**（行间仅 1px 发丝线，N 个并行调用占 N 行而非 N 张卡，thread transform 分组）；⑦ 一条动效曲线 `cubic-bezier(0.16,1,0.3,1)`；⑧ 阅读几何——用户气泡非对称圆角 `18/18/6/18`、右贴边光学对齐。工具卡图标首字母占位符替换为**真实图标映射**（新增 `desktop/src/utils/toolIcons.ts`，43 个内置工具精确名→lucide 图标，MCP 动态工具→拼图，未知回退首字母）。Accent 预设新增**黑/白单色**（主题感知灰阶：亮底黑按钮/暗底白按钮自动反转，新增 `--on-accent` token 承载 accent 上的文字色，`.btn-primary`/发送键/Titlebar logo 已接入）。次级组件（Sidebar/Titlebar/StatusBar/CommandPalette/InfoPanel/ModelPicker/PlanModeToggle/PlanReviewCard）全部对齐：字号下限 12px、去 glow 阴影、去渐变、chip 统一 28px 无边框。`globals.css` token 全面换血但**变量名零变更**——所有页面/内联样式无改动自动继承。生产构建 + 149 单测全绿
- **Desktop: v2 UI 全面重设计（Linear 式精致暗色）** — 桌面端前端按 `designs/mockups/v2-ui.yaml` 规范重构。新应用外壳：36px 自定义 titlebar（breadcrumb / 搜索触发 / 主题切换 / 窗口控制）+ VS Code 式 48px Activity Bar（Chat、Skills、MCP、Memory、Knowledge、Wiki、Plugins、Stats、Health、Alerts、Audit、Eval、Settings 全部提升为一级导航）+ 随路由切换的 232px Context Sidebar（仅 Chat 区显示项目/会话）+ 24px mono 状态栏（含上下文用量条）。新增 Ctrl+K 全局命令面板（分组 Recent/Navigation/Actions、实时过滤、全键盘操作）与 Ctrl+B 侧边栏折叠（250ms 宽度动画）。双主题 token 体系重写（暗色近黑四级明度阶梯 #0a0b0d→#1e2127、1px 半透明边框、暗色卡片 inset 顶部高光、accent 微渐变主按钮；亮色完整对照；默认改为暗色，切换 200ms 动画）。圆角体系 8/12/999。字体：Anton 移除，Inter + Geist Mono，基础字号 13px 紧凑密度。旧 /settings/* 子路由全部提升为顶级路由（/stats、/skills 等），SettingsLayout 删除。Chat 页用户消息改为右对齐弱化气泡、工具卡/输入框/空状态按 v2 组件规格重制；Stats 页改 segmented 时间范围 + v2 KPI 卡；其余页面卡片统一切到 surface-2 + card-highligh…

### Bug Fixes

- **run 提前结束：todo 批次 + 计划旁白被当成最终答案（启发式终止误判）** — 事故（2026-10-07 session `7774923c1358`）：模型在 `todo_add` 批次旁输出计划性文本（"…then dispatch sub-agents to explore in parallel."，163 字符），d99d823f 引入的 terminal 快路（`interpret_native`：可见文本 ≥30 字符 + 批内全是记账工具 → 文本即最终答案、跳过下一 LLM round）将其误判为答案，run 在第 5 步收尾，子代理从未启动、任务未完成。修复：**删除启发式**（`_BOOKKEEPING_TOOLS`/`_TERMINAL_TEXT_MIN_CHARS`/`terminal_answer` 全链路移除，同响应文本一律是旁白），改为**显式终止信号 + 提示词纪律双保险**（pi/opencode 收敛做法）：仅当本批含 `todo_update` 且清单非空全部 done 且同响应携带非空可见文本时，批处理后直接以该文本收尾（模型显式关闭全部 todo = 显式收尾声明；要求本批含 todo_update 防历史残留误触发）；`react.txt`/`react_think.txt` 新增收尾契约（最后一个 todo 标记 done 的那条响应必须含完整最终答案，未完成不得提前全标 done）。`test_decisions.py`/`test_memory_context.py` 同步更新；`test_e2e_react_agent.py` 新增 `TestTodoTerminateSignal` 4 用例（计划旁白不提前结束 / 全 done+答案文本同响应直接收尾 / 空文本不收尾 / 部分 done 不收尾）
- **Checkpoint 面板恒 "No checkpoints" + 撤销/重做全失效（session 错配）** — `/api/version/*` 与 `/api/session/{id}/checkpoints` 路由用的是 `runtime.version_manager`——`AppRuntime.build(profile="server")` 时绑死在一个随机生成的一次性 `server_xxxx` session 上（每次重启换新），而真实对话轮次全部 commit 进 `ChatService._get_version_manager(session_id)` 的**按 session 隔离**的 manager，路由永远查空链（本机 memory.db 实证：前端会话 10 个 checkpoint、构建期会话 0 个）。修复：① 六个 version 路由（status/log/jump/undo/redo/reset）+ session checkpoints 路由全部改按 `session_id` 取 per-session manager（`session_id` 为必填参数，桌面端 `api.ts` 各调用点透传当前会话 id）；`/api/version/compact` 同步修同类错配——原用构建期 `runtime.memory_manager`，改为 `chat._get_or_create_memory(session_id)`（body 加 `session_id` 字段，顺带修掉原 `custom_instructions` 只声明为 query 参数导致前端 JSON body 恒被忽略的暗坑）；② 旧 undo/redo 里"从 stm_snapshot 恢复 `runtime.memory_manager`"的死代码删除（journal 式 checkpoint 的 snapshot 恒为空，且摸的是构建期 memory）；③ 新增 `POST /api/version/jump?session_id=&cp_id=` + `ConversationVersionManager.jump_to()`——checkpoint 面板列表项可点击回跳到任意历史点（后端跳转、前端重载历史；向后走祖先链、被跳过节点进 redo 栈可步进恢复，向前走 redo 栈则整栈清空等同新 commit）；④ 撤销真正生效：`get_messages()` 改为只读到 HEAD checkpoint 的 `message_count`（journal 是 append-only，undo 后隐藏尾部行；NULL message_count 的旧 checkpoint 不截断），`/api/memory/short/history` 优先用进程内 live manager（重启后 HEAD 截断依然成立，HEAD 持久化在 `conversation_sessions`）；⑤ `commit_with_messages` 在 undo 后的 HEAD 上提交时先删除被放弃的 journal 尾部（线性链语义，否则新 turn 的 message_count 把已 undo 的旧消息重新暴露给模型）。新增 `tests/unit/test_checkpoint_head.py`（5 例：undo 截断、双向 jump、foreign/未知 checkpoint 拒绝、NULL mc 不截断、新 commit 清 redo+截尾）；TestClient 端到端冒烟（真实库只读断言 + scratch session 变更断言）全过
- **WS 断开 mid-run 事件队列无界堆积(`services/chat.py`)** — `_async_run_events`/`_run_events` 原是无界 `asyncio.Queue()`/`queue.Queue()`:客户端断开(`stream_events` 遇 `WebSocketDisconnect` 即 return)后,run 继续产生的事件持续灌进**无人抽取的队列**,直到该 session 下一次开新 run 才 `pop`——长 run(子代理任务)期间内存无界增长且 CPU 空闲(纯 append),与"内存爆满但 CPU 正常"的画像吻合。修复:① 两队列创建均加 `maxsize=_EVENT_QUEUE_MAX`(10_000)上限;② `_put_event`/`confirm_tool_call` 改 `put_nowait` + **drop-oldest** 策略(消费者死掉/过慢时淘汰最旧事件保最新,seq 游标让重连端可感知缺口并回退 REST 历史),淘汰计数 debug 日志;③ **run 终止哨兵(None)必须送达**:先清空积压再压入,防止活消费者挂死;同步队列的阻塞 `put` 同步改非阻塞(原来加 maxsize 会阻塞 agent 循环,一并修掉)。回归测试 `tests/test_event_queue_bounded.py`(4 例:无消费者时队列严格有界且最新事件存活、满时 put 不阻塞、同步/异步哨兵必达)
- **`nexus serve` 内存暴涨(36.8 GB private)——reranker 每调用重建 2.3GB + 联网失败丢弃** — 实锤链路:kb_search 守卫 `if retriever._reranker is None: load_reranker()`,而 `CrossEncoder('BAAI/bge-reranker-v2-m3')` 在 **tokenizer 步骤访问 huggingface.co 超时(离线/GFW, WinError 10060)**——此时 2.3GB fp32 权重已加载进内存,整个构造抛异常后丢弃,`_reranker` 仍为 None →**每次搜索重载一遍**;reranker 从未生效过(静默降级)。并发子代理下多路 2.3GB 分配/释放使 PyTorch arena 碎片化,私有内存水位只涨不还(探针:`experiments/probe_wave_repro.py` 3 并发波次复现 GB 级 ratchet;对照:无子代理任务 15 分钟仅 +5MB)。修复:① `load_reranker` 成功结果**进程级缓存**(双检锁,仿 embedding 单例模式)+ **失败结果 TTL 缓存 300s**(网络坏时不再每次搜索 churn 多 GB);② 优先用本地 HF snapshot + `local_files_only` 离线加载(仿 `embeddings._resolve_local_model_path`),tokenizer 不再联网,墙内 reranker 真正可用;③ `kb_service.search_kb` 改用 `_get_retriever()` **进程级单例**(原每调用全量重建 retriever:所有 chunk 文本入 RAM + BM25 重建——这是独立于本案的第二个内存隐患,`/api/kb/*` 高频调用下同理会打满)。回归测试 `tests/test_kb_retriever_reuse.py`(5 例:API 路径 2 次调用共享 1 retriever/1 reranker 加载、失败 TTL 不重试、工具路径单例对照、embedding 单例对照)——修复前这些断言为 2/2/2(每调用重建),修复后钉死 1/1/1。子代理框架本身经运行时探针排除(`experiments/probe_subagent_leak.py`:30 顺序 + 16 并行尝试,线程全归还、内存零趋势增长)
- **中断/失败 turn 的聊天答案墙** — `TurnRuntime._build_interrupted_answer()` 原为调试向文本（状态/原因/原始请求 + 最近 20 条 journal 记录，每条 300 字，合计可达 ~6KB），经 checkpoint answer → `get_messages_with_answers()` 回填为 `[最终答案]` 进入聊天历史：聊天气泡显示大段原始 tool 记录，且该合成行被喂回 prompt 重建，把 tool dump 噪声与 token 开销灌进模型上下文。修复：答案改为一行用户向摘要（`已中断（原因）（本轮已记录 N 项活动）` / `执行失败：原因 + 详情(500字截断)` / `Agent 未能得出最终答案`），journal 明细保留在 `TurnRecord.journal`（Timeline/日志不受影响，TUI 本就只截 32 字进侧边时间线）。更新钉住旧格式的单测 `test_turn_runtime.py::test_cancel_generates_compact_summary_with_reason_and_activity_count`（断言 journal dump 与重复 question 不再出现）
- **用户气泡莫名其妙换行** — 用户气泡 `max-w-[85%]` 写在一个 shrink-to-fit 的 flex 列（`flex flex-col items-end`，宽度由内容推导）里，百分比 max-width 对"宽度由自身内容决定"的包含块形成循环引用：浏览器先把列宽定为内容的自然宽度，再把气泡压到它的 85%——每条消息都被挤到自身文字宽度的 85%，不该折行的也折（截图中 "Do you like me?" 在远未达上限时折成两行即此因）。修复：中间列加 `w-full`（宽度锚定聊天列，百分比有了确定参照），气泡加 `w-fit`（仍收缩到内容、右对齐、`max-w-[85%]` 上限语义不变：≈612px）
- **历史会话三条异常修复（答案丢失/空思考卡/express_reaction 工具卡）** — 定位（复现数据：截图 session 的 journal 与 checkpoints 对比）：①`TurnRuntime.persist_snapshot` 用 **journal 行数当 STM 下标** 切本轮新消息（`all_msgs[journal_count:]`）——两者仅在 STM 恰为 journal 镜像时相等，重启后 STM 为空/注入上下文行时按计数硬切，turn 的 `[思考过程]`/`[最终答案]` 整块丢出持久化历史（checkpoints 有答案、journal 无消息，重启后"首次正常、历史消失"）；②JSON 重试路径无守卫追加空 `assistant` 行 → 历史渲染两张空白思考卡；③历史变换不识别 `express_reaction` 工具行 → 渲染工具卡（实时路径由服务端改写为 user_reaction，永不出卡）。修复：`persist_snapshot` 改为**恒等边界切片**（turn 启动记录边界消息，持久化时按 role+content 反向扫描定位，压缩回退 min(count,len)，顺带去重 turn-start 已提交的用户行、过滤空内容行）；JSON 重试路径加非空守卫；新增 `ConversationVersionManager.get_messages_with_answers()`——turn 缺 `[最终答案]` 时从 checkpoints 按问题回填合成（`/short/history` 端点启用，**已在用户真实受损 session 上验证答案恢复**）；桌面端历史变换提取为纯函数 `transformHistoryMessages`（`utils/historyTransform.ts`）：空行跳过、express_reaction 解析参数把 emoji 挂回用户消息（与实时渲染一致）。新增 `tests/unit/test_history_persistence.py`（4 用例：首轮持久化、重启后第二轮、空行过滤、checkpoint 回填）与 `desktop/src/__tests__/history-transform.test.ts`（6 用例）
- **桌面端用户消息气泡吞换行** — `ChatPage.tsx` 用户气泡裸渲染 `{msg.content}`，默认 `white-space: normal` 把 textarea 输入的多行内容（粘贴代码/日志/多段提问）折叠成一段、缩进全丢，显示与实发内容不一致。补 `whitespace-pre-wrap break-words`（与同文件工具卡/思考卡的既有处理对齐）；`max-w-[85%]` 限宽本身保留（行业惯例，≈612px 阅读宽度合理）
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
