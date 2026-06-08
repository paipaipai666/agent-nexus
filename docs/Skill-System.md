> **[中文](Skill-System.md) | [English](Skill-System.en.md)**

# 🎯 Skill 系统

Skill 是通过 `SKILL.md` 或 `workflow.yaml` 定义的可复用工作流模板。

## 生命周期

```
发现 → 索引 → 路由 → 选择 → 执行前步骤 → Agent 执行
```

**发现**：`SkillRegistry.discover()` 扫描 `~/.agentnexus/skills/` + `extensions_dirs` + 内置目录。

**路由**：`SkillRouter` 使用 TF-IDF 模型匹配用户输入：
- 对每个 skill 的 id/name/description 分词计算 IDF
- 评分 = 匹配词 IDF 之和 × 奖励系数
- 确定性匹配 + 可选 LLM 回退（分差 < margin 时）

**执行前步骤**：`WorkflowRuntime.prepare()` 顺序执行：
- `prompt` — 格式化提示文本
- `tool_call` — 调用指定工具
- `retrieve` — 检索知识库
- `checkpoint` — 记录检查点
- `finalize` — 验证成功标准

## SKILL.md 格式

```markdown
---
id: my-skill
display_name: My Skill
description: 描述
max_risk: medium
allow_tools: [web_search, file_read]
fragments: [react, security]
system: react
---

这里是 skill 的指导提示词（Markdown 正文）。
```

## 路由决策

1. 计算用户输入与每个 skill 的 TF-IDF 得分
2. 最高分 < `min_score` → 不走路由
3. 最高分 - 次高分 < `margin` → LLM 回退
4. 满足确定性条件 → 自动选择
