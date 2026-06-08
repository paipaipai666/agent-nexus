> **[中文](Prompts-Detailed.md) | [English](Prompts-Detailed.en.md)**

# 💬 Prompts 提示词模块（详细版）

## 概述

`prompts` 模块管理 AgentNexus 的提示词模板系统，支持模板加载、片段组合和动态注入。

## 模块结构

```
prompts/
├── __init__.py           # 提示词加载入口
└── fragments/            # 提示词片段目录
    ├── react.md          # ReAct 系统提示词
    ├── react_think.md    # ReAct 思考模式提示词
    ├── memory_summarize.md  # 记忆压缩提示词
    ├── rag_query_rewrite.md # RAG 查询改写提示词
    ├── rag_multi_query.md   # RAG 多查询提示词
    ├── rag_hyde.md          # RAG HyDE 提示词
    └── ...              # 更多提示词模板
```

## 核心函数

```python
def load_prompt(name: str) -> str:
    """加载指定名称的提示词模板"""
```

## 提示词类型

| 类型 | 说明 | 示例 |
| --- | --- | --- |
| 系统提示词 | 定义代理行为和能力 | `react.md` |
| 任务提示词 | 特定任务的指令 | `memory_summarize.md` |
| 格式提示词 | 输出格式约束 | `rag_query_rewrite.md` |
| 片段提示词 | 可组合的提示词片段 | `fragments/` 目录下 |

## 使用方式

```python
from agentnexus.prompts import load_prompt

# 加载 ReAct 系统提示词
react_prompt = load_prompt("react")

# 加载记忆压缩提示词
summarize_prompt = load_prompt("memory_summarize")
```

## 模块依赖关系

```
prompts/__init__.py
    └── load_prompt()
         └── fragments/*.md

使用方:
    ├── agents/re_act_agent.py    # 加载 react, react_think
    ├── memory/manager.py         # 加载 memory_summarize
    ├── rag/retriever.py          # 加载 rag_query_rewrite, rag_multi_query, rag_hyde
    └── skills/router/llm_decider.py  # 加载路由决策提示词
```
