# 🤝 贡献指南

## Issue 规范

### Bug 报告
- AgentNexus 版本、Python 版本、操作系统
- 最小复现步骤
- 预期行为 vs 实际行为
- `~/.agentnexus/traces/` 中对应 Trace ID

### 功能请求
- 使用场景
- 期望 API
- 影响范围

## PR 流程

1. Fork 仓库 → `git checkout -b feat/my-feature`
2. 阅读 [Architecture](Architecture.md) 和 [Development](Development.md) 理解系统
3. 代码通过 lint + 全量测试

```bash
ruff check agentnexus/ tests/
python -m pytest tests/ -v
```

4. PR 标题格式：`<type>: <简短描述>`

| 类型 | 场景 |
|------|------|
| `feat` | 新功能 |
| `fix` | 修复 |
| `docs` | 文档 |
| `refactor` | 重构 |
| `perf` | 性能 |
| `test` | 测试 |
| `security` | 安全 |

## 代码规范

- Python 3.11+, 行 120 字符
- 所有新函数参数需类型标注
- 提示词用 `str.format()`，不用 Jinja2
- 一个函数只做一件事

## 安全要求

- 不添加逃逸沙箱的代码
- 密钥字段用 `SecretStr`，日志脱敏
- PII 增强脱敏规则时同步更新测试

## 测试要求

- 新功能附单元测试
- 跨模块功能附集成测试
- 涉及 ChromaDB/SQLite 用 `temp_agentnexus_home` fixture
- 涉及 LLM 用 `mock_llm` fixture
- 外部服务必须 mock
