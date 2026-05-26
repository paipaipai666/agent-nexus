# 🛠 Development Guide

## Environment Setup

```bash
pip install -e ".[dev,eval]"
```

Optional runtimes: `sentence-transformers` (embedding), `pytesseract` (OCR), `scipy` (calibration), `bubblewrap` (Linux sandbox), Docker.

## Development Commands

```bash
ruff check agentnexus/ tests/            # Lint
python -m pytest tests/ -v               # Full test suite
python -m pytest tests/unit/ -v          # Unit tests
python -m pytest tests/integration/ -v   # Integration tests
python -m pytest tests/perf/ -v --benchmark-only
python -m pytest tests/ -v --cov=agentnexus --cov-report=html
pyinstaller agentnexus.spec --noconfirm  # Package
```

## Test Directory

| Directory | Style | Content |
|------|------|------|
| `tests/unit/` | class-based | Module unit tests |
| `tests/integration/` | function-based | Cross-component integration |
| `tests/regression/` | function-based | Full-feature regression |
| `tests/perf/` | function-based + benchmark | Performance benchmarks (26) |
| `tests/security/` | function-based | Security penetration (8 categories) |
| `tests/evals/` | JSONL | 9 evaluation datasets |

## Fixtures

```python
temp_agentnexus_home  # Temporary isolated data directory
mock_llm              # Mock AgentLLM.think()
```

## CI Pipeline

```
push/PR → ruff lint → pytest tests/ -v → eval sanity → [tag: v*] PyInstaller
```

## Sync Checklist

| Change | Sync Update |
|------|----------|
| New CLI command | `docs/commands.md` |
| New config item | `docs/configuration.md` |
| New dynamic import dependency | `agentnexus.spec` `hiddenimports` |
| New tool | `docs/architecture.md` tool table |

## Known Issues

- **Dual ChromaDB clients**: RAG and LTM each create independent `PersistentClient` pointing to same directory
- **BM25 not persisted**: In-memory only, rebuilt per session
- **Trace crash safety**: Only flushes on `end_trace()`, crash loses data
- **PII masking**: 12 unit tests covering
- **ChromaDB 1.5.8 numpy compat**: `get("embeddings")` needs explicit `is not None`
