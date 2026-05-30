# 贡献指南

## 提交 Issue

### Bug 报告

使用模板 `Bug Report`，需包含：
- **环境信息**：AgentNexus 版本、Python 版本、操作系统
- **复现步骤**：最小可复现示例
- **预期行为与实际行为**：对比说明
- **相关日志**：`~/.agentnexus/traces/` 中对应时间段的 JSONL 文件、Trace ID

LLM 输入的 prompt 和响应已记录在 trace 的 `input`/`output` 字段。

### 功能请求

使用模板 `Feature Request`，需包含：
- **使用场景**：什么情况下需要此功能
- **期望 API**：CLI 命令签名或配置项格式
- **影响范围**：是否涉及新工具、新评估器、新安全机制等

## 开发环境

```bash
pip install -e ".[dev,eval]"
```

首次参与：
1. Fork 仓库
2. 创建分支：`git checkout -b feat/my-feature`
3. 阅读 [docs/architecture.md](docs/architecture.md) 理解系统结构
4. 阅读 [docs/development.md](docs/development.md) 了解测试和 CI 约定

## PR 提交规范

### 前置检查

提交 PR 前必过：

```bash
ruff check agentnexus/ tests/                    # 零告警
python -m pytest tests/ -v                        # 全绿
```

涉及评估系统时另外运行：
```bash
nexus eval ci -d 7                                # CI 评估
```

代码覆盖率不应显著下降（CI 会检查）。

### PR 描述

PR 标题格式：`<type>: <简短描述>`

| 类型 | 适用场景 |
|------|----------|
| `feat` | 新功能（工具/命令/评估器/配置项） |
| `fix` | Bug 修复 |
| `docs` | 文档变更 |
| `refactor` | 重构（行为不变） |
| `perf` | 性能优化 |
| `test` | 新增/修改测试 |
| `security` | 安全修复 |

描述中说明：
- 变更内容（是什么）
- 设计理由（为什么）
- 风险评估（影响哪些模块）

## 代码规范

<!-- TODO(CLI-018): CLI help text currently mixes Chinese and English inconsistently.
     Some commands use Chinese-only help strings, others English, and some a mix.
     A full standardization pass is needed (estimated 6h). Until then, new commands
     should use Chinese help text to match the majority of existing commands. -->

- **Python 版本**：3.11+
- **行长度**：120 字符
- **类型标注**：所有新函数和方法的参数必须有类型标注；返回类型尽量标注
- **字符串**：f-string 优先，避免 `%` 和 `.format()`
- **提示词模板**：`str.format()`，不用 Jinja2（`agentnexus/prompts/`）
- **导入顺序**：标准库 → 第三方库 → 项目内部模块，每类用空行隔开
- **抽象层级**：一个函数只做一件事。工具实现放在 `agentnexus/tools/`，服务放在 `agentnexus/services/`

### 不需做的事

- 不要在非服务模块内引入 `Settings`（服务层之外的模块直接使用环境变量或传参）
- 不要在工具执行器（`ToolExecutor`）内部访问 `LLM`（工具应无状态）
- 不要向 `MemoryManager` 直接写入 PII（`conclude()` 已内建脱敏）

## 同步检查清单

| 变更类型 | 需要同步更新 |
|----------|-------------|
| 新增 CLI 命令 | `docs/commands.md` |
| 新增配置项 | `docs/configuration.md`（配置表 + 目录路径表） |
| 新增动态导入依赖 | `agentnexus.spec` 的 `hiddenimports` |
| 新增工具 | `docs/architecture.md` 的工具参数表 |
| 新增评估器 | `docs/architecture.md` 的评估体系小节 |
| 新增提示词模板 | `prompts/` 目录 + `docs/architecture.md` 提示词表 |
| 新增 MCP 配置项 | `docs/configuration.md` MCP 服务器配置表 |

## 测试要求

- 新功能必须有对应的单元测试（`tests/unit/`）
- 跨模块功能需有集成测试（`tests/integration/`）
- 涉及 ChromaDB/SQLite 的测试使用 `temp_agentnexus_home` fixture（自动隔离 + 清理）
- 涉及 LLM 调用的测试使用 `mock_llm` fixture（mock `AgentLLM.think()`）
- CLI 测试使用 `typer.testing.CliRunner` + `isolated_filesystem()`
- 需要外部服务的测试必须 mock（Tavily、E2B、第三方 API）
- 性能敏感变更附性能测试（`tests/perf/`，`@pytest.mark.perf` + `pytest-benchmark`）
- 安全相关变更附安全测试（`tests/security/`）

## 安全注意事项

### 代码执行

`python_execute` 和 `shell_exec` 是最高风险的入口。

- 永远不要添加逃逸代码沙箱的方法
- 修改沙箱逻辑时确保 `tests/security/test_sandbox_escape.py` 通过
- 新增后端必须在 `docs/architecture.md` 的降级链图中体现，并在 `tests/perf/` 添加基准

### 密钥处理

- 配置中密钥字段类型为 `SecretStr`，日志脱敏
- 审计日志中参数调用记录使用 `_truncate_params()` 自动脱敏
- 不要在 trace 的 `input`/`output` 中明文记录密钥

### PII 脱敏

所有最终写入 LTM 的文本经过 `MemoryManager._mask_pii()`，如果增强了脱敏规则，同步更新 `tests/unit/test_memory.py` 中的脱敏测试用例。

## 版本发布流程

项目经理执行：

```bash
# 1. 更新版本号（pyproject.toml + agentnexus/__init__.py）
# 2. 更新 CHANGELOG
# 3. git tag v0.x.x && git push origin v0.x.x
# 4. CI 自动构建跨平台二进制并发布 GitHub Release
```

CI 中 `release.yml` 的构建步骤用到 `agentnexus.spec`，因此 spec 文件必须与源码同步更新。
