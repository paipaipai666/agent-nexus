# 运行时模块

Agent 循环、短期记忆、长期记忆、LLM Wiki、评测和观测在启动时分别选择实现。选择固定在一个运行时内，当前不支持执行中的热替换。

## 配置开关

在现有 config.yaml 中配置：

```yaml
modules:
  agent:
    enabled: true
  short_term:
    enabled: false
  long_term:
    enabled: true
  wiki:
    enabled: false
  evaluation:
    enabled: true
  observability:
    enabled: false
```

默认 Wiki 关闭，其余模块启用。旧的 `wiki_enabled` 设置继续生效，明确配置 `modules.wiki` 时以后者为准。

关闭 Agent 后，不能发送对话或委派子任务，历史、知识库及其他已启用服务仍可使用。关闭短期记忆后，不恢复或保留跨轮上下文，当前轮工作消息仍可使用。长期记忆的检索与保存独立于短期记忆。关闭观测后，不收集 trace、审计和漂移诊断；任务回执、恢复、协作和会话历史继续持久化。

直接调用已关闭或缺失的模块会抛出 `ModuleUnavailable`。HTTP 返回 503 和 `error.code=module_unavailable`，CLI 输出明确错误。WebSocket 拒绝该次提交，连接可以继续处理其他请求。

## 提供替代实现

配置中的 factory 使用 `包.模块:函数` 格式，函数接受一个 `ModuleContext`，返回实现对象：

```yaml
modules:
  agent:
    enabled: true
    factory: my_runtime.engine:create_engine
```

也可以在 Python 组装边界注入工厂。`None` 明确关闭模块，`DEFAULT` 使用配置中的默认选择，函数明确选择替代实现：

```python
from dataclasses import replace

from agentnexus.app.runtime import AppRuntime
from agentnexus.core.modules import ModuleFactories


class Engine:
    def run(self, question, *, memory_manager=None, images=None, attachment_note=None):
        return "来自替代引擎的回答"


factories = replace(ModuleFactories.disabled(), agent=lambda context: Engine())
with AppRuntime.build(module_factories=factories) as runtime:
    session = runtime.chat.start_session()
    run = runtime.chat.send_message(session.id, "你好")
    print(runtime.chat.get_run_recovery(run.id))
```

主代理、子代理和实际执行任务的评测入口沿用所选 Agent。工厂上下文包含运行时配置、会话、工作区、工具、LLM 和执行限制。默认子代理通过延迟 LLM 工厂保持独立客户端；替代引擎决定是否使用这些依赖。

Agent 的最小入口是上例中的 `run`，结果可以是字符串或具有 `answer` 的对象。需要实时事件时公开可写的 `event_handler`，发送 `core.agent_contracts.AgentSignal`。事件转发保留调用参数，支持单信号回调和原有状态机回调。输出、待办、步数和用量分别使用 `output`、`todo_list`、`step_count` 和 `total_usage`。需要交互确认时公开可写的 `confirm_handler`。取消、协作及上下文等增强能力通过相应公开 setter 提供，最小引擎不要求实现这些能力。

短期记忆使用 `core.memory_contracts.ConversationMemory` 的公开消息和快照接口。长期记忆提供文本检索与写入接口，嵌入模型归实现所有；管理入口使用同一所选对象。Wiki 和评测替代服务实现其对外开放的对应操作。观测接口位于 `core.telemetry.TraceBackend`，调用方通过上下文中的观测实现发送数据。

`ModuleHost.status()` 可查看选择及初始化状态，不触发加载。Agent 和短期记忆按执行者或会话构造；长期记忆、Wiki、评测和观测在运行时内复用。运行时关闭时释放所拥有的模块资源，每个对象的 `close()` 最多调用一次。进程共享的 LLM 连接池由进程退出处理。

显式工厂初始化失败会抛出 `ModuleLoadError`，不会换回默认实现。观测已初始化后的写入失败不会覆盖任务自身的结果或异常。

## 移除默认实现

默认实现只在选中时加载。可以关闭模块或配置替代工厂，再移除下列实现：

| 模块 | 默认实现位置 |
| --- | --- |
| Agent 循环 | `agentnexus/agents/` |
| 短期记忆 | `agentnexus/memory/short_term.py` |
| 长期记忆 | `agentnexus/memory/long_term.py` |
| LLM Wiki | `agentnexus/wiki/` |
| 评测 | `agentnexus/evaluation/`、`agentnexus/eval/` |
| 观测 | `agentnexus/observability/` |

`memory` 目录还包含会话协调和历史存储，不能将整个目录当作短期或长期记忆的实现删除。任务持久化位于 `storage.timeline`，不属于可移除的观测系统。

特定默认记忆算法的评测需要对应默认实现，不能用于任意替代后端。此类评测会检查选择并报告不适用，不会为了运行评测重新装配已关闭的默认记忆。

`prompt_benchmark` 的完整提示词实验和 `prompt_holdout` 的隔离实验专门比较默认 Agent 的提示词，要求选择默认引擎。它们会在引擎关闭、缺失或被替换时报告不适用。通用任务评测、细粒度评测及单案例执行函数继续使用所选引擎。

## 验证

```text
rtk pytest tests/unit/test_module_combinations.py tests/unit/test_module_host.py -q
rtk pytest tests/unit/test_optional_module_imports.py tests/unit/test_modular_agent.py tests/unit/test_modular_memory.py -q
rtk pytest tests/unit/test_optional_telemetry.py tests/unit/test_execution_persistence.py tests/unit/test_wiki_eval_modules.py -q
rtk pytest tests/unit/test_transport_modules.py tests/unit/test_optional_memory_routes.py tests/unit/test_subagent_modules.py -q
rtk pytest tests/unit/test_tui_removed_agent.py tests/unit/test_evaluation_module_consumers.py tests/unit/test_runtime_settings_ownership.py -q
rtk proxy python scripts/check_no_comments.py
```

组合测试覆盖六个开关的 64 种选择。独立子进程通过导入屏蔽模拟默认实现缺失，不删除工作区文件。确定性测试使用替代模型或固定向量，不要求真实 LLM 请求或模型下载。
