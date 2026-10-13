> **[中文](Skill-System.md) | [English](Skill-System.en.md)**

# 🎯 Skill 系统

Skill 是通过 `SKILL.md` 或 `workflow.yaml` 定义的可复用工作流模板。

## 生命周期

```
发现 → 索引 → 路由 → 选择 → 执行前步骤 → Agent 执行
```

**发现**：`SkillRegistry.discover()` 默认递归扫描以下目录，无需配置 `extensions_dirs` 即可使用两级 `.agents/skills`：

- `$AGENTNEXUS_HOME/skills/`，未设置时为 `~/.agentnexus/skills/`。
- 用户级 `~/.agents/skills/`。
- 配置中的 `extensions_dirs`。
- 内置技能目录。
- 项目级 `<workspace>/.agents/skills/`。

CLI 使用当前工作目录作为 workspace。应用启动时使用传入的 `workspace_path`，未传入时使用当前工作目录。聊天会话绑定其他 workspace 时，技能服务只加载该工作区的项目技能，用户级和配置中的共享技能仍然可用。不同会话分别持有技能选择和路由索引，不会混用其他项目的技能。

目录不存在时跳过，不会创建目录。同一物理技能文件只加载一次。用户级和项目级定义相同的 `namespace/id` 时继续报告冲突，不进行覆盖。

启动或首次访问会话技能时执行发现。文件修改后需要显式刷新，没有文件监听。全局刷新和单项启停会在下次访问会话技能时同步。刷新会更新已选技能的正文，技能删除或产生冲突后会清除选择。

技能 API 支持可选查询参数 `session_id`。例如 `GET /api/skills?session_id=<id>` 和 `POST /api/skills/refresh?session_id=<id>` 查询或刷新该会话的工作区。省略参数时操作应用启动工作区的技能服务。带参数的选择、重置和启停操作只作用于该会话；未找到会话时返回 404。

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
