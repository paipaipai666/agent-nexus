# 🤝 Contributing Guide

## Issue Guidelines

### Bug Reports
- AgentNexus version, Python version, OS
- Minimal reproduction steps
- Expected vs actual behavior
- Corresponding Trace ID from `~/.agentnexus/traces/`

### Feature Requests
- Use case
- Expected API
- Impact scope

## PR Process

1. Fork repo → `git checkout -b feat/my-feature`
2. Read [Architecture](Architecture.en.md) and [Development](Development.en.md) to understand the system
3. Code must pass lint + full test suite

```bash
ruff check agentnexus/ tests/
python -m pytest tests/ -v
```

4. PR title format: `<type>: <short description>`

| Type | Scenario |
|------|------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation |
| `refactor` | Refactoring |
| `perf` | Performance |
| `test` | Testing |
| `security` | Security |

## Code Standards

- Python 3.11+, 120 char lines
- All new function parameters must have type annotations
- Prompts use `str.format()`, not Jinja2
- One function does one thing

## Security Requirements

- No code that escapes sandbox
- Secret fields use `SecretStr`, logs masked
- Update tests when enhancing PII masking rules

## Testing Requirements

- New features include unit tests
- Cross-module features include integration tests
- ChromaDB/SQLite tests use `temp_agentnexus_home` fixture
- LLM-dependent tests use `mock_llm` fixture
- External services must be mocked
