# ReAct FSM 精简重设计：提案与执行计划

> 状态：**未改任何产品代码**。截至此刻磁盘上只有新增文件：
> `docs/fsm-redesign-proposal.md`、`tests/unit/test_fsm_constraints.py`、
> `docs/eval-improvement-plan.md`（另一件事）、`coverage.xml`（跑测试带的）。
> 正文顺序：§0 全盘计划 → §1~§6 现状与设计 → §7 已拍板决策 → §8~§9 跑飞兜底调研与方案。

---

## 0. 全盘计划（从头到尾）

### 0.1 我们到底走到哪了

| 阶段 | 做了什么 | 产物 / 证据 |
|---|---|---|
| A 调研 | 读完 Codex / OpenCode / Gemini CLI / Aider 的主循环源码 | 结论：**四家都是 `while` 变体，没有显式状态机**；OpenCode 是"步函数返回状态码 + 外层 while"最接近一半的抽象 |
| B 取证 | 写 10 个测试验证"FSM 框住选择空间"的三个约束 | `tests/unit/test_fsm_constraints.py`：**8 passed / 2 xfailed** |
| C 审计 | 写 AST 脚本静态扫转移表 | `loop s/audit_fsm.py`：33 行转移仅 1 个状态有兜底行，**3 个真实可达崩溃缺口** |
| D 归因 | 定位根因 | 不是"状态机错了"，是**转移表不是全函数** + **状态承载了本该是函数做的事** |
| E 设计 | 出目标形态 + 三决策 + 跑飞兜底调研 | 本文档 §2~§9 |
| **F 实施** | **还没开始** | 见 0.3 |

### 0.2 一个重要的顺序判断：先别去补那三个窟窿

直觉上应该先修 bug 再重构，但这里的正好相反——**三个缺口在目标形态里会自动消失**：

| 缺口 | 为什么重设计后没有 |
|---|---|
| `DEGRADE + LLM_PARAMS_READY` | 决策 1 删掉 THOUGHT_MISSING → 触发路径没了；且 `DEGRADE` 状态本身被 `RECOVER` 吸收 |
| `DEGRADE + ABORT` | 同上 |
| `PREPARE_LLM_CALL + DEGRADED` | `PREPARE_LLM_CALL` 被 `AWAIT_MODEL` 吸收，`DEGRADED` 事件被 `ROUND_READY` 取代 |

所以：**跳过"先补 3 行止血"**，直接进入 Step 1。只在"暂时不想动结构"的前提下才需要先补表（那时只需加 2 行 + totality 测试）。

### 0.3 执行步骤（每步都可验证、可回滚）

| # | 步骤 | 改什么 | 行为变化 | 怎么验 | 回滚 |
|---|---|---|---|---|---|
| **1** | 抽 `interpret()` / `recover()` 纯函数 | 新增 `agents/decisions.py`；现有 handler 改为调用它 | **无** | 新单测 + 全量回归全绿 | 删文件 + 恢复 handler（git 直接回） |
| **2** | 在纯函数层落地三项决策 | `decisions.py`；同时删 `THOUGHT_MISSING` 相关、`max_steps` 软化 | **有**（有意为之） | 决策相关的新单测 + `test_fsm_constraints.py` 里 2 个 xfail 应转为正常删除（缺口路径不存在） | 同上 |
| **3** | 换转移表 + 新 handler | `react_transitions.py`（33→11 行）、`react_types.py`（删 9 状态、合并事件）、`re_act_agent.py`（handler 重写） | 有 | e2e 门禁套件全绿：`test_e2e_react_agent`、`test_native_content_tool_call`、`test_todo_double_answer`、`test_subagent_*`、`test_batch_tool_events`、`test_intermediate_state` | 上一 commit 回滚；建议单独一个 commit 便于 revert |
| **4** | 跑飞兜底 L1/L2/L3 | `drift_detector` 告警接线、`max_steps` 软提示、token 预算提醒 | 有 | 新单测模拟连续相同工具调用 → 断言发出用户可见提示 | 同上，**与 1~3 无依赖，可提前或并行做** |
| **5** | 清理 + 固化 | 删旧状态/旧 handler、`react_runtime.py` 中不适用的分支、更新 `docs/Agents-Detailed.md`、**把 AST 审计固化成 `tests/unit/test_fsm_table_totality.py`** | 无 | totality 测试通过 | 同上 |

关键：**Step 4 独立于状态机改造**，你想先做也可以。

### 0.4 每步的验收标准（DoD）

1. 全量回归绿：`cd D:/code/AgentNexus && "D:/python3.13/python.exe" -m pytest tests/unit tests/integration tests/regression -q`
2. AST 审计无缺口（脚本已入库 `scripts/audit_fsm.py`）：
   `cd D:/code/AgentNexus && "D:/python3.13/python.exe" scripts/audit_fsm.py`，
   看 `[1]` 段应为 `0 gap(s)`（重设计后；今天是 `3 gap(s)`）
3. 没有新的 `FSMError` 触发路径：grep 确认没有 handler 返回所在状态没定义的事件
4. TUI 事件流无变化：`test_gui_event_mapping` / `test_realtime_events` / `test_ws_agent_stream` 全绿（这三件套是事件名的看门狗）

### 0.5 还没定的小事（实施前随手拍一下）

- `hard_stop_on_max_steps` 的默认值定 False 还是 True（我建议 False，评测/CI 显式打开）
- 收尾提示的中文文案（可直接抄 OpenCode `MAX_STEPS_PROMPT` 翻译改写）
- 事件旧名（`TOOLS_FOUND` 等）是否保留 alias 一个发布周期——考虑到 TUI/WS/trace 有 8 个测试文件引用，**建议保留**
- 闭环告警阈值：沿用现有 3 次，还是对齐 Gemini 的 5 次

### 0.6 已知必须接受的风险

- 取消硬性 max_steps 后，理论上存在"模型不听劝 + 不触发闭环 + 无人取消 → 本轮不结束"
  ，兜底只有用户取消（已可用）和 `hard_stop_on_max_steps` 开关
- 允许 tools+text 后，"文本 = 最终答案"的旧假设要全部重审：判定改为"无工具且无 JSON 协议指令才是答案"
- `react_runtime.py` 里针对单脉冲 sequential / batch 的两条路径（`execute_pending_tool` vs
  `execute_pending_tools_batch`）要不要合并成一条，Step 3 时会撞上，需现场决定

---

## 1. 现状与问题

```
33 行转移 / 15 个状态 / 32 个事件 / 只有 EMIT_ANSWER 一个状态有 event=None 兜底
```

真实可达的崩溃缺口（`fsm.py:89` 抛 `FSMError`，整轮报废）：

| 缺口 | 来源 | 触发方式 |
|---|---|---|
| `PREPARE_LLM_CALL + DEGRADED` | `_on_retries_left` (`re_act_agent.py:830-833`) | JSON_MODE 下同一条消息连续 2 次解析不出来 |
| `DEGRADE + LLM_PARAMS_READY` | `_on_degraded` (`:854-878`) | 三次"只调工具不说话" |
| `DEGRADE + ABORT` | `_on_degraded` (`:856-859`) | 降级超过 3 次 |

根因是结构性的，不是某行写错：

1. **状态承载了本该是函数做的事**。`SELECT_STRATEGY`、`CHECK_EMPTY`、`JSON_PARSE`、`DEGRADE`、`ERROR_ABORT` 都只有一个出口——它们是流水线的一步，不是决策点。
2. **状态 × 事件不是一个全函数**。handler 在落点状态发出该状态没定义的事件就直接抛错，且没有任何便宜手段提前发现（需要专门写 AST 审计）。
3. **策略档位被编码成了拓扑**。原生工具 / JSON / 纯文本三条协议路径各占一组状态，本质上是同一个"解释模型输出"动作的三种实现。

## 2. 目标形态：6 个状态，10 行转移

### 状态

| 状态 | 含义 | 吸收掉的旧状态 |
|---|---|---|
| `INIT` | 建上下文、定协议档位 | INIT, SELECT_STRATEGY |
| `AWAIT_MODEL` | 一次模型往返，把输出**解释**成一个标准化决策 | PREPARE_LLM_CALL, CALL_LLM, RECEIVE_RESPONSE, CHECK_TOOL_CALLS, CHECK_EMPTY, JSON_PARSE, CLASSIFY |
| `EXECUTE_TOOL` | 执行工具批次 | EXECUTE_TOOL |
| `RECOVER` | 所有"出问题了怎么办"的收口 | RETRY_GATE, DEGRADE, ERROR_ABORT |
| `ANSWER` | 交最终答案（可被 AGENT_STOP 钩子打回） | EMIT_ANSWER |
| `DONE` | 终态 | DONE |

**协议档位（`CallingStrategy`）不再是状态**，降级为 `AWAIT_MODEL` 内部的一个属性。

### 事件

只保留"能推动状态变化"的事件，其余全部降为旁路观测（`ctx.emit`）：

| 事件 | 含义 | 替代 |
|---|---|---|
| `START` | 起手 | START |
| `TOOLS_REQUESTED` | 解释器判定：模型要调工具 | TOOLS_FOUND, CLASSIFIED_TOOL |
| `ANSWER_READY` | 解释器判定：这是最终答案 | NO_TOOLS, CLASSIFIED_ANSWER, FALLBACK_TEXT |
| `FAULT` | 解释器判定：这次输出不可用（payload 带 `RetryReason`） | EMPTY_RESPONSE, PARSE_ERROR, CLASSIFIED_ERROR, THOUGHT_MISSING, TRUNCATED_RESPONSE, LLM_ERROR |
| `TOOLS_DONE` | 整批工具跑完 | TOOL_DONE, ALL_TOOLS_DONE |
| `ROUND_READY` | 再来一轮模型调用 | LLM_PARAMS_READY, RETRIES_LEFT, DEGRADED |
| `ABORT` | 终止 | ABORT |
| `ANSWER_VETOED` | 钩子否决了这个答案 | STOP_VETOED |

**降为旁路、不进表**：`STREAM_TOKEN`、`STREAM_REASONING`、`TOOL_START`、`TOOL_DONE`、`ANSWER_THOUGHT`。
它们已经走 `ctx.emit`，且和队列事件共享同一个 `seq`（`react_types.py:118`），UI 层无感。

### 转移表

| from | event | to | handler |
|---|---|---|---|
| `INIT` | `START` | `AWAIT_MODEL` | `init` |
| `AWAIT_MODEL` | `TOOLS_REQUESTED` | `EXECUTE_TOOL` | `on_tools_requested` |
| `AWAIT_MODEL` | `ANSWER_READY` | `ANSWER` | `on_answer` |
| `AWAIT_MODEL` | `FAULT` | `RECOVER` | `on_fault` |
| `EXECUTE_TOOL` | `TOOLS_DONE` | `AWAIT_MODEL` | `run_batch` |
| `EXECUTE_TOOL` | `FAULT` | `RECOVER` | `on_fault` |
| `RECOVER` | `ROUND_READY` | `AWAIT_MODEL` | `recover_round` |
| `RECOVER` | `ANSWER_READY` | `ANSWER` | `salvage_answer` |
| `RECOVER` | `ABORT` | `DONE` | `fatal` |
| `ANSWER` | `ANSWER_VETOED` | `AWAIT_MODEL` | `on_vetoed` |
| `ANSWER` | *(unconditional)* | `DONE` | `emit_answer` |

33 行 → 11 行，15 个状态 → 6 个。

## 3. 为什么这次不会因为"某个状态没定义某个事件"而崩

关键改变：**每个状态的出口由它自己的决策函数的返回值类型决定，而返回值类型是封闭的三选一。**

```python
def interpret(...) -> ModelDecision      # kind ∈ {tools, answer, fault}
def recover(...) -> RecoverDecision      # kind ∈ {round, salvage, abort}
```

`AWAIT_MODEL` 的 handler 只能返回三种决策 → 表上正好三行；`RECOVER` 同理。
于是"全函数"变成**可以在测试里断言的不变量**，而不是靠人肉维护：

```python
for state, handler_fn in STATE_HANDLERS.items():
    assert {e.name for e in declared_returns(handler_fn)} == {row.event for row in rows_for(state)}
```

这正好把那个 AST 审计脚本从"事后发现"变成"提交时被拦"。

## 4. 保住的东西（不能丢）

| 能力 | 现状依据 | 新设计怎么保 |
|---|---|---|
| 逐步轨迹 | 每次转移都 notify `(event, from, to)` | 保留，粒度变粗（一轮一条） |
| 细粒度过程可见性 | `TOOL_START`/`STREAM_*` 等事件 | 走 `ctx.emit` 旁路，已经是这样 |
| 钩子否决 | `EMIT_ANSWER + STOP_VETOED` | `ANSWER + ANSWER_VETOED` |
| 取消 | `fsm.py:79 _raise_if_cancelled` | 引擎不动，循环里逐次检查 |
| 步数上限兜底答案 | `_on_max_steps_abort` | 改为 `FAULT(reason=STEPS_EXHAUSTED)` → `RECOVER` → salvage/abort，语义统一 |
| 轨迹重放 | `tests/unit/test_trajectory_replay.py` | 该文件不引用 `ReActState`，已确认不受状态名影响 |

## 5. 影响面（已核实）

| 范围 | 情况 |
|---|---|
| 厂商代码 | `re_act_agent.py`（1123 行，handler 大部分重写）、`react_types.py`（删 9 个状态、合并事件）、`react_runtime.py`（291 行，拆出 interpret/recover）、`react_transitions.py`（79 → 11 行） |
| **引擎** | `fsm.py`（148 行）**完全不用动**——它已经是通用的接表执行器 |
| TUI | `tui/screens/chat.py` 只 import `ReActEventType`（`:1469`），**不依赖状态名** → 事件字面值不改则零改动 |
| 引用状态的测试 | 只有 3 个：`test_fsm_engine.py`(45 处)、`test_react_fsm.py`(10 处)、`test_fsm_constraints.py`(1 处) |
| 引用事件的测试 | 8 个（`test_gui_event_mapping`、`test_realtime_events`、`test_ws_agent_stream`、`test_trace`、`test_chat_service`、`test_memory_context`、`test_batch_tool_events`、`test_agents_react_runtime`）——**因此建议保留事件名字面值**，别顺手重命名 |
| 文档 | `docs/Agents-Detailed.md`（345 行）有 FSM 章节，需同步 |

## 6. 迁移路径（每步都必须绿）

1. **抽纯函数（无行为变化）**：把 `_robust_json_parse` / `_classify_parsed` / `_fail_truncated_tool_calls` / `_recover_protocol_json` / `retry_gate` / `_select_strategy` 收进 `interpret()` 和 `recover()`，补单层单测；现有 handler 改为调用它们。
2. **事件统一（无行为变化）**：`TOOLS_FOUND`/`CLASSIFIED_TOOL` → `TOOLS_REQUESTED` 等，旧名保留 alias 一个发布周期；`TOOL_DONE` 降为纯旁路。跑全量回归。
3. **换表 + 新 handler**：只改 `react_transitions.py` 和 handler 注册，用现有 e2e 套件（`test_e2e_react_agent`、`test_native_content_tool_call`、`test_todo_double_answer`、`test_subagent_*`、`test_batch_tool_events`）当门禁。
4. **删旧状态/旧 handler**，同步 `docs/Agents-Detailed.md`，固化 totality 不变量测试。

回归命令：

```
cd D:/code/AgentNexus && "D:/python3.13/python.exe" -m pytest tests/unit tests/integration tests/regression -q
```

## 7. 已拍板的三个决定（2026-09-24）

### 决策 1：允许模型不做思考，直接调工具

删除 `THOUGHT_MISSING` 这条强制约束，连带删除 `_thought_retries` / `max_reflections` 那套计数。
影响的代码：`re_act_agent.py:503-510`（注入"你必须先用 Thought…"）、`:505-510` 的 thought 判空、
`:640-646` `_on_thought_missing`、`react_transitions.py:33` 与 `:65` 两行、`_select_visible_thought` 的门禁语义。

注意两点：
- `_select_visible_thought` **保留**，但降级为"要不要在 UI 上展示思考"，不再作为准入条件。
- thought 为空时别再输出 `思考: ` 这种空标题（`react_runtime.py:68`），要做空值跳过。

这个决定顺带消灭了三个崩溃缺口里的两个（`DEGRADE+LLM_PARAMS_READY`、`DEGRADE+ABORT`），
因为它们只在"三次缺 Thought"这条路上可达。

### 决策 2：允许一边说话一边调工具

`ModelDecision` 从三选一改成可共存：

```python
@dataclass
class ModelDecision:
    tool_calls: list[dict]        # 可以非空
    text: str = ""                # 可以非空：模型一边解释一边动手
    kind: str                     # tools / answer / fault
```

判定规则相应改为：**既没有工具、也没有 JSON 协议指令时，才把文本当最终答案**。
附带文本走旁路 `ctx.emit(ANSWER_THOUGHT, ...)` 展示，工具照常执行；`TOOLS_REQUESTED` 事件的 payload 带上 text。

这条同时把前面那个"一步只能落一个槽"的约束解掉了。

### 决策 3：`max_steps` 不再做任何强制措施

到点只提示，不终止。理由很实在：步数是个和真实成本、真实任务复杂度都不成正比的单位。

---

## 8. 跑飞兜底：四家怎么做（全部已读源码核实）

| 方案 | 谁在用 | 机制 | 是否强制 |
|---|---|---|---|
| **闭环检测 + 交给用户/模型** | Gemini CLI | `LoopDetectionService`：工具调用的 key = `name + JSON.stringify(args)`，连续重复 ≥ `TOOL_CALL_LOOP_THRESHOLD=5` 判为 `CONSECUTIVE_IDENTICAL_TOOL_CALLS`（`loopDetectionService.ts:29/174-176/313-340`）；正文重复 ≥ `CONTENT_LOOP_THRESHOLD=10` 判 `CONTENT_CHANTING_LOOP`（`:30`）；单条 prompt 内轮数 ≥ `LLM_CHECK_AFTER_TURNS=30` 后，每 5~15 轮让一个独立模型做一次判断，置信度 ≥0.9 才报 `LLM_DETECTED_LOOP`（`:42/:48-54/:65-66`）。UI 侧有 `LoopDetectionConfirmation.tsx`——**弹给用户决定** | 否 |
| **软到极致：默认就没有上限** | OpenCode | `agent.steps ?? Infinity`（`prompt.ts:1178`），**默认无限**；用户配了才生效，且到点不是终止，而是往 messages 里插一条 `MAX_STEPS_PROMPT` 伪 assistant 消息要求"不许再调工具，总结已完成的工作和遗留事项"（`max-steps.ts:1-16`、`prompt.ts:1179/1281`） | 否 |
| **预算提醒（按 token 不按步数）** | Codex CLI | `RolloutBudget`：加权 token（`output*采样权重 + 未缓存输入*prefill 权重`）累计 vs `limit_tokens`；在剩余量跨过阈值时插入提醒（`rollout_budget.rs:48-67/69-93`），通篇没有"到 N 步就停" | 否 |
| **硬上限（部署语境）** | Claude Code | 官方 hosting 文档明确写着"No top-level session timeout —— Set `maxTurns`/`max_turns` to bound how many tool-use round trips"；另有 `CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS` 做子 agent 停摆看门狗。Anthropic 自己的 computer-use 示例代码把 `max_iterations` 定位成"防止可能导致意外 API 费用的潜在无限循环" | 是，且主要是给**无人值守**场景兜底 |

**看出来的共识**：交互式场景下没人用步数硬卡，大家更喜欢"检测到异常 → 提示一把 → 让用户/模型自己决定"；
硬上限只在 CI、GitHub Actions、托管 Subagent 这类没有人在旁边看着的语境里保留。这个分野和你的直觉完全一致。

---

## 9. 落地到本项目的分层方案（全部非强制）

现状其实已经有了一半的料，不用从零造：

| 层 | 现状 | 建议改动 |
|---|---|---|
| L0 漂移检测 | 已实现：每 3 步 `check()`，critical 信号会 `emit_drift_alerts` + 注入"请重新聚焦"软提示（`re_act_agent.py:424-452`） | 保留 |
| L1 闭环告警 | 已有但没用上：`_check_repeated_steps` 判"连续 3 次相同工具"(`drift_detector.py:100/196-231`)，`record_step` 已经在 `_on_tool_done` 被调用（`re_act_agent.py:653-661`），但 `REPEATED_STEPS` 只是 WARNING，**只进日志** | 升级为可见 + 软干预：`ctx.emit` 一条警告给 TUI + 注入一句 nudge（和 critical 那条同样的手法）。Gemini 阈值是 5，你这里是 3，可以先沿用再看是否调 |
| L2 max_steps | 目前是硬终止 + 兜底答案（`re_act_agent.py:882-889`） | 改成软 checkpoint：到点注入一条收尾提示（抄 OpenCode `MAX_STEPS_PROMPT` 的思路），**不终止**；另留一个 `hard_stop_on_max_steps` 开关，**默认 False**，评测/CI 场景按需打开 |
| L3 成本预算 | `ctx._total_usage` 已经在累计 input/output tokens（`react_runtime.py:39-42`） | 加一层"剩余预算跨阈值 → 插入提醒"，照 Codex 的做法，比步数更贴近真实成本 |

**兜底链路的最短完整性**：软提示 → 模型仍不停 → 用户取消。取消这条已经有了
（`fsm.py:79` 每轮进循环都检查，甚至流式输出期间也响应 ESC，`re_act_agent.py:454-462`）。
所以"取消硬上限"不会让用户在失控时束手无策——代价只是需要人在旁边。

**理论上仍存在**：模型不听劝、又不触发闭环检测、又没人取消 → 这一轮不会结束。
这是移除强制措施必须接受的代价，用 L2 那个默认关闭的开关兜住 CI 场景即可。
