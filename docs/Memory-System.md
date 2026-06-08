> **[中文](Memory-System.md) | [English](Memory-System.en.md)**

# 🧠 记忆系统

## 双层架构

```
用户交互 → MemoryManager (贯穿 Agent 生命周期)
    │
    ├── init_session(question)
    │     ├── 编码 → ChromaDB LTM 搜索 (top-5, min_similarity=0.5)
    │     ├── 格式化 → 注入系统提示 {memory_context}
    │     └── 快照 write_counter
    │
    ├── append(role, content) — 每次 Agent 消息
    │     ├── 大结果(>阈值) → offload.py 卸载到磁盘, 返回短存根 + 预览
    │     ├── 追加到 STM
    │     └── maybe_compact() → 五层压缩
    │
    ├── refresh_ltm_context()
    │
    ├── conclude(question, answer)
    │     ├── PII 脱敏
    │     ├── 预过滤 (_should_extract): 信号词检测 + 长度/模式判断
    │     ├── LLM 提取 → 3 类记忆 (fact/preference/note)
    │     ├── 语义去重 (cosine similarity ≥ 0.90 → 跳过)
    │     ├── 冲突检测 (fact/preference 类: LLM 判断矛盾 → 覆盖旧记忆)
    │     └── 编码 + 保存 LTM (SQLite + ChromaDB)
    │
    └── run_reflection() — 周期性反思
          ├── 拉取近期 note 类记忆 (≥ 5 条)
          ├── LLM 归纳高阶模式 → 保存为 fact/preference
          └── 标记原始 note 为 superseded_by
```

## STM 压缩金字塔

`maybe_compact()` 从低到高逐层触发：

| 层 | 触发条件 | 操作 |
|----|----------|------|
| 1 断路器 | 连续 3 次压缩失败 | 跳过压缩，指数退避 (30s → 120s)；半开探测恢复 |
| 2 Snip | STM 条数过多 | 保留最近 10 条 |
| 3 时间微压缩 | 距上次 API 调用 > 配置间隔 | 清除可恢复工具结果 |
| 3b 消息微压缩 | 紧接 LLM 摘要前 | 清除旧可恢复工具结果（保留最近 5 条）；助手消息 > 2000 字符截断 |
| 4 读时投影 | token 占用 ≥ 90% ctx | 见下方「读时投影（projection.py）」章节 |
| 5 LLM 摘要 | 缓冲 token 不足 | 写入 transcript → LTM drain 高重要性消息 → LLM 摘要替换 |

**断路器状态机**：`closed → open (退避) → half-open (探测) → closed/open`

**LTM Drain**：LLM 摘要前将 importance ≥ 0.7 的消息先保存到 LTM，确保关键信息不丢失。

## 读时投影（projection.py）

非破坏性读时压缩，在每次 LLM 调用前按 token 占比自动选择策略：

| 占比 | 策略 | 操作 |
|------|------|------|
| < 90% | 无 | 不做处理 |
| 90%-95% | `project_mild()` | 助手/工具消息 > 1000 字符截断（前 500 + 后 500），保留最近 4 条不截断 |
| ≥ 95% | `project_aggressive()` | 清除可恢复工具结果，助手消息截断，插入投影分隔标记 |

**重要性保护**：importance ≥ 0.7 的消息免于截断。

## LTM 评分与驱逐

**搜索评分**：
```
score = cosine_similarity × 0.6 + importance × 0.2 + decay × 0.2
decay = 1.0 / (1.0 + age_hours / 168)   // 7 天半衰期
```

**驱逐策略**（超出 `max_memories`）：
1. `_compact_low_score()` — 合并同 category 低分条目
2. 按 `importance ASC, created_at ASC` 删除超出部分（同步删 ChromaDB）
3. 清理 TTL 过期条目（默认 90 天）

**重要性类别**（3 类体系）：

| 类别 | 默认权重 | 说明 |
|------|----------|------|
| `fact` | 0.85 | 事实：实体事实 + 结论（永久，高重要性） |
| `preference` | 0.9 | 偏好：用户偏好 + 工具偏好（永久，高重要性） |
| `note` | 0.7 | 笔记：任务进展 + 错误模式 + 对话上下文（临时，中重要性） |

> **迁移映射**：`entity_fact`/`conclusion` → `fact`，`user_preference`/`tool_preference` → `preference`，`task_progress`/`error_pattern`/`conversation` → `note`

LLM 提取时可返回每条记忆的自定义 importance（0.0-1.0），优先于类别默认值。

## 对话版本控制

`ConversationVersionManager`（`versioned.py`）实现线性检查点系统：

- 每次用户轮次自动 `commit()`，记录 question/answer/STM 快照
- SQLite 三表：`conversation_checkpoints`（线性链）、`conversation_sessions`（工作区会话）、`conversation_messages`（消息日志）
- 工作区会话管理：`register_session(workspace_path, profile)` 关联会话与工作区
- 消息日志：`append_message(role, content)` 记录完整对话历史
- 支持 `undo()` / `redo()`（redo 栈在新 commit 后清空）

## 大结果卸载（offload.py）

当工具结果超过配置阈值（`large_result_threshold`）时，将完整内容写入磁盘并返回短存根：
```
[工具结果已缓存] 文件: /path/to/offload/{session_id}_{timestamp}.txt
预览(前500字符): {preview}
```
- 自动清理：每次卸载时删除超过 24 小时的旧文件
- 由 `MemoryManager.append()` 在 Layer 1 自动触发

## 周期性反思（reflection.py）

`run_reflection()` 从近期 note 类记忆中归纳高阶模式：

1. 拉取最近 N 天的 note 类记忆（排除已反射的），至少 5 条才触发
2. LLM 归纳反复出现的模式或偏好 → 保存为 `fact` 或 `preference` 类别
3. 原始 note 标记为 `superseded_by → 新模式记忆`
4. 语义去重：与已有记忆 cosine similarity ≥ 0.90 则跳过

反射后的记忆以 `[Reflection]` 前缀保存，importance 范围 0.7-0.95。
