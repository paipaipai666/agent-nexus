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
    │     ├── 大结果(>10KB) → 卸载到磁盘
    │     ├── 追加到 STM
    │     └── maybe_compact() → 五层压缩
    │
    ├── refresh_ltm_context()
    │
    └── conclude(question, answer)
          ├── PII 脱敏
          ├── LLM 提取 (memory_extract.txt)
          └── 编码 + 保存 LTM (SQLite + ChromaDB)
```

## STM 压缩金字塔

`maybe_compact()` 从低到高逐层触发：

| 层 | 触发条件 | 操作 |
|----|----------|------|
| 1 断路器 | 连续 3 次压缩失败 | 跳过压缩 60 秒 |
| 2 Snip | STM 条数过多 | 保留最近 10 条 |
| 3 时间微压缩 | 距上次调用 > 300 秒 | 清除可恢复工具结果 |
| 4 消息截断 | 助手消息 > 2000 字符 | 截断长消息 |
| 5 LLM 摘要 | 缓冲 token 不足 | LLM 摘要替换（前备份快照） |

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

**重要性类别**：

| 类别 | 权重 | 说明 |
|------|------|------|
| `user_preference` | 0.9 | 用户偏好 |
| `entity_fact` | 0.7 | 实体事实 |
| `conclusion` | 0.8 | 结论 |
| `conversation` | 0.5 | 普通对话 |
| `task_progress` | 0.7 | 任务进展 |
| `error_pattern` | 0.8 | 错误模式 |
| `tool_preference` | 0.6 | 工具偏好 |

## 对话版本控制

`ConversationVersionManager` 实现类 Git 的检查点：
- 每次用户轮次自动 `commit()`
- SQLite 三表：`checkpoints`（DAG）、`checkpoint_ltm_refs`、`branches`
- 支持 `undo()` / `redo()` / `branch(name)`
