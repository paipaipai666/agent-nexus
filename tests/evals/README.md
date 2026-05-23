# AgentNexus Evaluation Datasets

此目录用于存放评估数据集（.jsonl 格式），供 `nexus eval` 命令使用。

## 格式规范

每个评估数据集为 JSONL 文件，每行一个 JSON 对象：

```json
{"trace_id": "...", "question": "...", "expected_answer": "...", "tools_used": [...]}
```

## 文件命名

- `agent_eval.jsonl` — Agent 整体评估
- `tool_selection.jsonl` — 工具选择评估
- `hallucination.jsonl` — 幻觉检测评估
- `coherence.jsonl` — 连贯性评估
- `trajectory.jsonl` — 轨迹评估

## 将来计划

数据集将在后续版本中由自动化 pipeline 生成和填充。
