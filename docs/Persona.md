> **[中文](Persona.md) | [English](Persona.en.md)**

# 🎭 Persona 系统

Persona 系统定义了 Agent 的**身份、行为原则和任务上下文**，让 Agent 从"有用的助手"变成有立场、有边界、有上下文的操作搭档。

## 设计理念

大多数 Agent 的系统提示词只定义了工作流程（怎么用工具、怎么推理），但没有定义**行为准则**（该不该同意、什么操作需要确认、产出被忽略时怎么办）。

Persona 系统填补了这个空白，分为两层：

### 第一层：平台行为原则（无条件加载）

三个 fragment 文件，在每次 Agent 运行时自动注入，不受 Skill Profile 影响：

| Fragment | 文件 | 职责 |
| --- | --- | --- |
| **Stance** | `fragments/stance.txt` | 立场总纲：不无脑同意，反对必须带证据 |
| **Autonomy** | `fragments/autonomy.txt` | 自主权边界：按风险等级决定是否需要确认 |
| **Accountability** | `fragments/accountability.txt` | 问责闭环：用户跳过建议时主动提醒 |

这些原则是 Agent 的核心行为准则，优先于任何用户设定的语气偏好。

### 第二层：用户个性化配置

在 `config.yaml` 的 `persona` 区块定义，运行时编译为 prompt fragment 注入：

```yaml
persona:
  agent_name: "Nexus"
  identity: "开发搭档"
  tone: "直接、简洁，不啰嗦"
  projects:
    - name: "AgentNexus"
      focus: "v0.2.0 发布"
    - name: "SideProject"
      focus: "原型验证"
```

## 自主权边界详解

`autonomy.txt` 将操作分为三个风险等级：

### 低风险（直接执行）

无需确认，Agent 自行完成：

- 读取、查询、搜索类操作
- 生成内容但不写入持久存储
- 格式转换、文本处理
- 列出、统计、分析现有数据

### 中风险（执行后告知）

Agent 执行后在**同一条回复**中告知用户：

- 创建新文件（不覆盖已有文件）
- 运行只读命令（ls, cat, grep, git status）
- 修改内存中的状态（todo、session 变量）

### 高风险（操作前确认）

Agent 必须说明意图、影响范围和不可逆程度，等用户确认：

- 写入、修改、删除文件或数据库记录
- 调用有副作用的外部 API
- 执行改变系统状态的 shell 命令
- 一步操作影响多个系统或多条记录
- 覆盖已有文件

## 问责机制

`accountability.txt` 实现了软触发的问责闭环：

**触发条件**：Agent 上一轮有明确建议，且用户下一条消息完全没有涉及它。

**静默条件**（不触发）：
- 用户刚发第一条消息
- 产出刚给出的下一轮
- 用户明确说了"先放一放"或"跳过"

**触发方式**：简短问一句，不长篇解释。

## 注入顺序

在 prompt 的 context 消息中，各部分的注入顺序：

```text
memory_context
conversation_context
available_skill_context
mcp_context
compiled_profile.fragments_text   ← Skill 特定 fragment（如有）
persona_text                      ← 用户个性化（如有）
behavior_fragments_text           ← 行为原则（最后注入，权重最高）
todo_context
```

行为原则放在最后，利用 LLM 对末尾内容注意力更强的特性，确保优先级最高。

## 配置方式

### config.yaml

```yaml
persona:
  agent_name: "Nexus"          # Agent 名称
  identity: "开发搭档"          # 角色定义
  tone: "直接、简洁"            # 沟通风格
  projects:                     # 当前关注的项目
    - name: "项目名"
      focus: "当前重点"
```

### 桌面端 GUI

在 Settings 页面的 **Persona** 区域直接编辑，支持：

- Agent Name / Identity / Tone 文本输入
- Projects 动态列表（增删改）
- 即时保存到 config.yaml

### 不配置会怎样？

- 行为原则三件套仍然生效（无条件加载）
- Persona fragment 跳过（无内容不注入）
- Agent 表现为标准的 ReAct 工作流，没有个性化行为
