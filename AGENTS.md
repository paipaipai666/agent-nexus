# AGENTS.md — AgentNexus

See CLAUDE.md for the full operating contract. The rule below is absolute and
applies to every agent and every change, in every language in this repo.

## No explanatory comments

Do not write explanatory comments — prose that restates what the code does
(`# increment the counter`, `// fetch user data`, `/* card styles */`). Code
must explain itself through naming and structure. This covers Python, TS/JS,
CSS, YAML/TOML, shell, and config files (requirements.txt, .gitignore, …).

TODO/FIXME/XXX/HACK markers are also banned — track pending work in issues,
not in source files.

Allowed: functional directives that a tool reads, never prose for humans.

- Python: shebang, `# -*- coding: ... -*-`, `# type: ...`, `# noqa`,
  `# nosec`, `# pragma: ...`, `# pylint:/fmt:/isort:/pyright:/ruff:/yapf:/mypy:`,
  PEP 723 `# ///` blocks
- TS/JS: `/// <reference ...>`, `eslint-*`, `@ts-ignore` / `@ts-expect-error` /
  `@ts-nocheck`, `@__PURE__`, `webpack*` / `vite-ignore` magic comments,
  `sourceMappingURL`
- CSS: `/*! ... */` license banners
- YAML / shell: shebang, `# yaml-language-server: ...`, `# shellcheck ...`

Docstrings are documentation, not comments — keep writing them for public
modules, classes, and functions.

Enforcement: `python scripts/check_no_comments.py` runs in the CI lint job and
locally under pytest (`tests/unit/test_comment_gate.py`). A red gate blocks
merge: strip the comment, or convert it to an allowed directive.
