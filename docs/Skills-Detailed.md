> **[中文](Skills-Detailed.md) | [English](Skills-Detailed.en.md)**

# 🎯 Skills 技能模块（详细版）

## 概述

`skills` 模块实现了 AgentNexus 的技能发现、路由和运行时系统。技能是预定义的工作流配置，可以自动或手动应用于代理会话，改变代理的行为模式。

## 架构总览

```
用户输入: "帮我审查这段代码"
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│                    SkillRouter                               │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ Normalize│→│ Retrieve │→│ Rank     │→│ Decide   │   │
│  │ 文本预处理│  │ 候选检索 │  │ 排序打分 │  │ 决策选择 │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│                                              │               │
│                                              ▼               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ LLM Fallback (可选)                                   │   │
│  │ 当规则引擎置信度不足时，使用 LLM 辅助决策              │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│                    SkillRegistry                             │
│                                                              │
│  扫描 SKILL.md 文件 → 解析元数据 → 构建 SkillEntry           │
│  支持: SKILL.md (新格式) + workflow.yaml (旧格式)            │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│                    SessionProfile                            │
│                                                              │
│  应用到代理会话:                                             │
│  - ToolPolicy: 工具白名单/黑名单                            │
│  - PromptProfile: 系统提示词注入                            │
│  - RetrievalPolicy: RAG 检索策略                            │
│  - MemoryPolicy: 记忆策略                                   │
└─────────────────────────────────────────────────────────────┘
```

## 核心类型

### SkillEntry（技能条目）

```python
@dataclass(frozen=True)
class SkillEntry:
    namespace: str                    # 命名空间
    workflow_id: str                  # 工作流 ID
    display_name: str                 # 显示名称
    description: str                  # 描述
    path: Path                        # 文件路径
    workflow: Workflow                 # 工作流定义
    source_kind: str = "skill"        # 来源类型
    aliases: tuple[str, ...] = ()     # 别名
    verbs: tuple[str, ...] = ()       # 动词（用于路由匹配）
    objects: tuple[str, ...] = ()     # 宾语（用于路由匹配）
    domains: tuple[str, ...] = ()     # 领域（用于路由匹配）
    examples: tuple[str, ...] = ()    # 示例
    negative_hints: tuple[str, ...] = ()  # 负面提示
```

### Workflow（工作流）

```python
@dataclass
class Workflow:
    id: str
    name: str
    description: str
    steps: list[WorkflowStep]
    tool_policy: ToolPolicy
    prompt_profile: PromptProfile
    retrieval_policy: RetrievalPolicy
    memory_policy: MemoryPolicy
    resources: list[SkillResource]
```

### SessionProfile（会话配置）

```python
@dataclass
class SessionProfile:
    workflow: Workflow
    compiled_tools: list[str]         # 编译后的工具列表
    compiled_prompt: str              # 编译后的系统提示词
```

## SkillRegistry（技能注册表）

**文件**：`skills/registry.py`

```python
class SkillRegistry:
    def __init__(self, roots, default_namespace="default", loader=None)
    def discover()                              # 扫描所有技能目录
    def list_skills() -> list[SkillEntry]       # 列出所有技能
    def get_skill(qualified_id) -> SkillEntry   # 获取技能
    def from_settings(settings) -> SkillRegistry  # 从配置创建
```

### 技能发现流程

```
扫描 roots 目录
    │
    ├── 查找 SKILL.md 文件
    │       └── 解析 frontmatter (YAML) + body (Markdown)
    │
    ├── 查找 workflow.yaml 文件（旧格式兼容）
    │       └── 解析 YAML 工作流定义
    │
    └── 构建 SkillEntry 列表
            └── 提取路由元数据 (verbs, objects, domains)
```

## SkillRouter（技能路由器）

**目录**：`skills/router/`

### 路由流程

```
用户输入
    │
    ▼
┌──────────────┐
│ 1. Normalize │  文本预处理（分词、小写、去停用词）
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 2. Retrieve  │  从技能索引中检索候选技能
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 3. Rank      │  对候选技能打分排序
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 4. Decide    │  规则引擎决策（置信度阈值 + 边际检查）
└──────┬───────┘
       │
       ├── 置信度 >= min_score 且边际 >= margin → 直接选择
       │
       └── 置信度不足 → LLM Fallback（可选）
                │
                ▼
          最终选择或无匹配
```

### 路由文件

| 文件 | 职责 |
| --- | --- |
| `types.py` | 路由类型定义 |
| `normalize.py` | 文本预处理 |
| `parse.py` | 解析用户意图 |
| `retrieve.py` | 候选技能检索 |
| `rank.py` | 排序打分 |
| `decide.py` | 规则引擎决策 |
| `llm_decider.py` | LLM 辅助决策 |
| `llm_fallback.py` | LLM 回退逻辑 |
| `telemetry.py` | 路由遥测 |

## SkillService（技能服务）

```python
class SkillService:
    def __init__(self, registry, agent, auto_route=True,
                 auto_route_llm_fallback=True, llm_client=None)
    def use_default(skill_id)          # 设置默认技能
    def route(user_input) -> str | None  # 自动路由
    def apply_skill(skill_id)          # 应用技能到会话
```

## 模块依赖关系

```
SkillService
    ├── SkillRegistry (registry.py)
    │       └── SkillEntry, Workflow
    ├── SkillRouter (router/)
    │       ├── normalize → retrieve → rank → decide
    │       └── llm_decider (可选)
    ├── SessionProfile (profile.py)
    │       └── ToolPolicy, PromptProfile
    └── ReActAgent (agents/)
            └── set_session_profile()
```
