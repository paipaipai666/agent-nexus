"""No-comment gate for CI: tracked source files must not contain comments.

Explanatory comments are banned project-wide; only functional directives are
allowed (toolchain hints a machine reads, not prose a human reads):

  Python:  shebang, coding cookie, # type:, noqa, nosec, pragma, pylint:,
           fmt:, isort:, pyright:, ruff:, yapf:, mypy:, PEP 723 # ///
  TS/JS:   eslint, @ts-ignore/@ts-expect-error/@ts-nocheck, @__PURE__,
           webpack*, vite-ignore, sourceMappingURL, /// <reference,
           prettier-ignore, @license/@preserve
  CSS:     /*! license, sourceMappingURL
  YAML/TOML/shell/PS1/requirements/.gitignore:
           shebang, yaml-language-server, shellcheck

Docstrings are not comments and are untouched by this gate. The scanners are
conservative by design: a miss is acceptable, a false positive is not.

Usage: python scripts/check_no_comments.py [root]
Exit code 1 when any violation is found.
"""

import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

PY_ALLOW = [re.compile(p) for p in (
    r"#\s*type:\s*\S",
    r"\bnoqa\b",
    r"\bnosec\b",
    r"\bpragma:",
    r"\bpylint:",
    r"\bfmt:\s*(on|off|skip)\b",
    r"\bisort:",
    r"\bpyright:",
    r"\bruff:",
    r"\byapf:",
    r"\bmypy:",
    r"#\s*///",
)]
CODING_RE = re.compile(r"coding[:=]\s*[-\w.]+")

JS_ALLOW = [re.compile(p) for p in (
    r"\beslint\b",
    r"@ts-(ignore|expect-error|nocheck)\b",
    r"@__PURE__|@pure\b",
    r"\bwebpack",
    r"vite-ignore",
    r"sourceMappingURL",
    r"^///\s*<reference",
    r"prettier-ignore",
    r"@license|@preserve",
    r"^/\*!",
)]

CSS_ALLOW = [re.compile(p) for p in (r"^/\*!", r"sourceMappingURL")]

HASH_ALLOW = [re.compile(p) for p in (
    r"yaml-language-server",
    r"\bshellcheck\b",
)]

REGEX_PREV_CHARS = set("([{,;:!&|?+-*%^~<>=~")
REGEX_PREV_WORDS = {
    "return", "typeof", "case", "in", "of", "new", "delete", "void",
    "instanceof", "yield", "await", "else", "do", "throw",
}


def scan_python(text):
    findings = []
    try:
        toks = tokenize.generate_tokens(io.StringIO(text).readline)
        for tok in toks:
            if tok.type != tokenize.COMMENT:
                continue
            body, row = tok.string, tok.start[0]
            if row == 1 and body.startswith("#!"):
                continue
            if row <= 2 and CODING_RE.search(body):
                continue
            if any(r.search(body) for r in PY_ALLOW):
                continue
            findings.append((row, body.strip()[:70]))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        pass
    return findings


def _js_try_regex(text, i, n):
    j = i + 1
    in_class = False
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "\n":
            return None
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            j += 1
            while j < n and (text[j].isalpha()):
                j += 1
            return j
        j += 1
    return None


def scan_js(text):
    findings = []
    i, n = 0, len(text)
    line = 1
    mode = "code"
    tpl_stack = []
    depth = 0
    prev_char = ""
    prev_word = ""
    while i < n:
        c = text[i]
        if mode == "sq" or mode == "dq":
            q = "'" if mode == "sq" else '"'
            if c == "\\":
                i += 2
                continue
            if c == "\n":
                line += 1
            if c == q:
                mode = "code"
                prev_char = '"'
                prev_word = ""
            i += 1
            continue
        if mode == "tpl":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                mode = "code"
                prev_char = '"'
                prev_word = ""
                i += 1
                continue
            if c == "$" and i + 1 < n and text[i + 1] == "{":
                tpl_stack.append(depth)
                depth += 1
                mode = "code"
                prev_char = "{"
                prev_word = ""
                i += 2
                continue
            if c == "\n":
                line += 1
            i += 1
            continue
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r":
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] in "/*":
            k = i - 1
            while k >= 0 and text[k] in " \t":
                k -= 1
            m = re.search(r"[A-Za-z][A-Za-z0-9+.-]*:$", text[max(0, k - 30):k + 1])
            if text[i + 1] == "/" and m:
                prev_char = '"'
                prev_word = ""
                i += 2
                continue
            if text[i + 1] == "/":
                j = text.find("\n", i)
                j = n if j == -1 else j
                body = text[i:j].strip()
                if not any(r.search(body) for r in JS_ALLOW):
                    findings.append((line, body[:70]))
                i = j
                continue
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            body = text[i:j].strip()
            if not any(r.search(body) for r in JS_ALLOW):
                findings.append((line, body[:70]))
            line += text.count("\n", i, j)
            i = j
            continue
        if c == "'":
            mode = "sq"
            i += 1
            continue
        if c == '"':
            mode = "dq"
            i += 1
            continue
        if c == "`":
            mode = "tpl"
            i += 1
            continue
        if c == "/" and (
            not prev_char
            or (prev_word and prev_word in REGEX_PREV_WORDS)
            or (not prev_word and prev_char in REGEX_PREV_CHARS)
        ):
            end = _js_try_regex(text, i, n)
            if end is not None:
                line += text.count("\n", i, end)
                i = end
                prev_char = '"'
                prev_word = ""
                continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if tpl_stack and depth == tpl_stack[-1]:
                tpl_stack.pop()
                mode = "tpl"
                i += 1
                continue
        if c.isalnum() or c in "_$":
            prev_word += c
        else:
            prev_word = ""
        if not c.isspace():
            prev_char = c
        i += 1
    return findings


def scan_css(text):
    findings = []
    i, n = 0, len(text)
    line = 1
    mode = None
    while i < n:
        c = text[i]
        if mode is not None:
            if c == "\\":
                i += 2
                continue
            if c == mode:
                mode = None
            i += 1
            continue
        if c in "\"'":
            mode = c
            i += 1
            continue
        if c == "\n":
            line += 1
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            body = text[i:j].strip()
            if not any(r.search(body) for r in CSS_ALLOW):
                findings.append((line, body[:70]))
            line += text.count("\n", i, j)
            i = j
            continue
        i += 1
    return findings


def _hash_comment_col(line, need_space):
    in_s = in_d = False
    k = 0
    while k < len(line):
        c = line[k]
        if in_s:
            if c == "'":
                if k + 1 < len(line) and line[k + 1] == "'":
                    k += 2
                    continue
                in_s = False
            k += 1
            continue
        if in_d:
            if c == "\\":
                k += 2
                continue
            if c == '"':
                in_d = False
            k += 1
            continue
        if c == "'":
            in_s = True
        elif c == '"':
            in_d = True
        elif c == "#" and (k == 0 or not need_space or line[k - 1] in " \t"):
            return k
        k += 1
    return None


def scan_hash(text, kind):
    findings = []
    block_indent = None
    triple = None
    heredoc = None
    for row, raw in enumerate(text.split("\n"), 1):
        line = raw.rstrip("\r")
        stripped = line.lstrip(" \t")
        indent = len(line) - len(stripped)
        if heredoc is not None:
            if stripped == heredoc:
                heredoc = None
            continue
        if triple is not None:
            if triple in line:
                triple = None
            continue
        if block_indent is not None:
            if stripped == "" or indent > block_indent:
                continue
            block_indent = None
        if row == 1 and line.startswith("#!"):
            continue
        if kind == "gitignore":
            if line.startswith("#"):
                body = line.strip()
                if not any(r.search(body) for r in HASH_ALLOW):
                    findings.append((row, body[:70]))
            continue
        if kind in ("sh", "ps1") and heredoc is None:
            m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", line)
            if m:
                heredoc = m.group(1)
        if kind == "yaml" and re.search(r"(:\s*|-\s+)[|>][+-]?[0-9]?[+-]?\s*(#.*)?$", line):
            block_indent = indent
        if kind == "toml":
            for q in ('"""', "'''"):
                if line.count(q) % 2 == 1:
                    pre = line.split(q, 1)[0]
                    col = _hash_comment_col(pre, need_space=False)
                    if col is not None:
                        body = pre[col:].strip()
                        if not any(r.search(body) for r in HASH_ALLOW):
                            findings.append((row, body[:70]))
                    triple = q
                    break
            if triple is not None:
                continue
        need_space = kind != "toml"
        col = _hash_comment_col(line, need_space=need_space)
        if col is not None:
            body = line[col:].strip()
            if not any(r.search(body) for r in HASH_ALLOW):
                findings.append((row, body[:70]))
    return findings


def classify(rel):
    name = rel.rsplit("/", 1)[-1]
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix in (".py", ".pyi") or name == "agentnexus.spec":
        return "python"
    if suffix in (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts"):
        return "js"
    if suffix == ".css":
        return "css"
    if suffix in (".yml", ".yaml"):
        return "yaml"
    if suffix == ".toml":
        return "toml"
    if suffix in (".sh", ".bash"):
        return "sh"
    if suffix == ".ps1":
        return "ps1"
    if name == ".gitignore" or name == ".dockerignore":
        return "gitignore"
    if re.fullmatch(r"requirements.*\.txt", name):
        return "txt"
    return None


def collect_files(root):
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
        ).stdout
        rels = [p for p in out.decode("utf-8", errors="replace").split("\0") if p]
    except Exception:
        skip = {".git", "node_modules", "__pycache__", "dist", "build", "_internal"}
        rels = []
        for p in Path(root).rglob("*"):
            if p.is_file() and not any(part in skip for part in p.parts):
                rels.append(p.relative_to(root).as_posix())
    return [r for r in rels if not r.startswith(".codegraph/")]


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    violations = {}
    for rel in collect_files(root):
        kind = classify(rel)
        if kind is None:
            continue
        path = root / rel
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8", errors="replace")
        except OSError:
            continue
        if kind == "python":
            found = scan_python(text)
        elif kind == "js":
            found = scan_js(text)
        elif kind == "css":
            found = scan_css(text)
        else:
            found = scan_hash(text, kind)
        if found:
            violations[rel] = found
    for rel in sorted(violations):
        for row, body in violations[rel]:
            print(f"{rel}:{row}: {body}")
    total = sum(len(v) for v in violations.values())
    if total:
        print(f"\n{total} comment(s) in {len(violations)} file(s). "
              "Only toolchain directives are allowed; see scripts/check_no_comments.py docstring.")
        return 1
    print("no-comment gate: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
