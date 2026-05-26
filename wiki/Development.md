# 🛠 开发指南

## 环境搭建

```bash
pip install -e ".[dev,eval]"
```

可选运行时：`sentence-transformers`（嵌入）、`pytesseract`（OCR）、`scipy`（校准）、`bubblewrap`（Linux 沙箱）、Docker。

## 开发命令

```bash
ruff check agentnexus/ tests/            # Lint
python -m pytest tests/ -v               # 全量测试
python -m pytest tests/unit/ -v          # 单元
python -m pytest tests/integration/ -v   # 集成
python -m pytest tests/perf/ -v --benchmark-only
python -m pytest tests/ -v --cov=agentnexus --cov-report=html
pyinstaller agentnexus.spec --noconfirm  # 打包
```

## 测试目录

| 目录 | 风格 | 内容 |
|------|------|------|
| `tests/unit/` | class-based | 各模块单元测试 |
| `tests/integration/` | function-based | 跨组件集成 |
| `tests/regression/` | function-based | 全功能回归 |
| `tests/perf/` | function-based + benchmark | 性能基准（26 个）|
| `tests/security/` | function-based | 安全渗透（8 类）|
| `tests/evals/` | JSONL | 9 个评估数据集 |

## Fixtures

```python
temp_agentnexus_home  # 临时隔离数据目录
mock_llm              # Mock AgentLLM.think()
```

## CI 流程

```
push/PR → ruff lint → pytest tests/ -v → eval sanity → [tag: v*] PyInstaller
```

## 同步清单

| 变更 | 同步更新 |
|------|----------|
| 新 CLI 命令 | `docs/commands.md` |
| 新配置项 | `docs/configuration.md` |
| 新动态导入依赖 | `agentnexus.spec` 的 `hiddenimports` |
| 新工具 | `docs/architecture.md` 工具表 |

## 已知问题

- **ChromaDB 双客户端**：RAG 和 LTM 各自创建独立 `PersistentClient` 指向同一目录
- **BM25 不持久化**：仅内存，每会话重建
- **Trace 崩溃安全**：仅 `end_trace()` 时 flush，异常崩溃丢数据
- **PII 脱敏**：12 个单元测试覆盖
- **ChromaDB 1.5.8 numpy 兼容**：`get("embeddings")` 需显式 `is not None`
