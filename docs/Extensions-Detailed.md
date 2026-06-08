> **[中文](Extensions-Detailed.md) | [English](Extensions-Detailed.en.md)**

# 🧩 Extensions 扩展模块（详细版）

## 概述

`extensions` 模块实现了 AgentNexus 的插件系统，支持动态加载和管理扩展功能。`capabilities` 模块提供能力运行时，协调扩展与核心系统的交互。

## 扩展系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    ExtensionManager                          │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ Discover │→│ Load     │→│ Enable   │→│ Unload   │   │
│  │ 扫描发现 │  │ 加载插件 │  │ 启用插件 │  │ 卸载插件 │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│                    CapabilityRuntime                         │
│                                                              │
│  协调扩展与核心系统的交互:                                   │
│  - 注册扩展提供的工具                                        │
│  - 注册扩展提供的钩子                                        │
│  - 管理扩展生命周期                                          │
└─────────────────────────────────────────────────────────────┘
```

## 核心类

### ExtensionManager

```python
class ExtensionManager:
    def __init__(self, settings)
    def discover()                     # 扫描扩展目录
    def load_enabled(runtime=None)     # 加载所有启用的扩展
    def unload(name)                   # 卸载指定扩展
    def list_extensions() -> list      # 列出所有扩展
    def get_extension(name)            # 获取扩展信息
```

### CapabilityRuntime

```python
@dataclass
class CapabilityRuntime:
    settings: Settings
    executor: ToolRegistry
    agent: ReActAgent
    skill_service: SkillService
    mcp_manager: MCPToolManager
    extension_manager: ExtensionManager
    register_tools: Callable
    llm_client: AgentLLM
    subagent_confirm: Callable
```

## 内置扩展

**目录**：`builtin_extensions/`

| 扩展 | 说明 |
| --- | --- |
| 内置工具扩展 | 提供核心工具集 |
| 内置技能扩展 | 提供核心技能集 |

## 扩展接口

### 扩展清单 (manifest.yaml)

```yaml
name: my-extension
version: 1.0.0
description: My custom extension
author: Developer
tools:
  - name: my_tool
    description: A custom tool
    param_schema: {...}
hooks:
  - type: before_tool_call
    handler: my_hook_handler
```

### 扩展生命周期

```
发现 (Discover)
    │
    ├── 扫描扩展目录
    ├── 解析 manifest.yaml
    └── 验证扩展完整性
         │
         ▼
加载 (Load)
    │
    ├── 导入扩展模块
    ├── 注册工具
    └── 注册钩子
         │
         ▼
启用 (Enable)
    │
    └── 扩展生效
         │
         ▼
卸载 (Unload)
    │
    ├── 注销工具
    ├── 注销钩子
    └── 释放资源
```

## 模块依赖关系

```
ExtensionManager (extensions/)
    ├── Settings (core/config.py)
    ├── ToolRegistry (tools/registry.py)
    └── HookManager (core/hooks.py)

CapabilityRuntime (capabilities/runtime.py)
    ├── ExtensionManager
    ├── ToolRegistry
    ├── ReActAgent (agents/)
    ├── SkillService (skills/)
    ├── MCPToolManager (tools/mcp_adapter.py)
    └── AgentLLM (core/llm.py)
```
