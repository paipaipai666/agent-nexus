"""grep_search tool — ripgrep code search for exact symbol/pattern matching."""

import re
import subprocess
from pathlib import Path


def _normalize_glob_pattern(pattern: str) -> str:
    """Normalize glob pattern to match ripgrep behavior.

    ripgrep treats ** as 'match any directory component', so:
    - **/*.py is equivalent to *.py (match all .py files)
    - **/test_* is equivalent to test_* (match all test_* files)
    """
    if pattern.startswith("**/"):
        pattern = pattern[3:]
    if pattern.endswith("/**"):
        pattern = pattern[:-3]
    return pattern


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Convert glob pattern to regex, matching ripgrep's glob semantics.

    ripgrep matches glob patterns against the full relative path, so:
    - ``*``  matches any characters including ``/`` (not just filename)
    - ``?``  matches a single character including ``/``
    - ``[seq]`` / ``[!seq]`` character class

    The ``**`` prefix/suffix is already handled by ``_normalize_glob_pattern``.
    We prepend ``(?:.*/)?`` so patterns like ``test_*`` match ``src/test_file.py``.
    """
    parts: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                parts.append(".*")
                i += 2
                if i < n and pattern[i] == "/":
                    i += 1
            else:
                parts.append(".*")
                i += 1
        elif c == "?":
            parts.append(".")
            i += 1
        elif c == "[":
            j = i + 1
            if j < n and pattern[j] == "!":
                # Convert [!seq] to [^seq] for regex
                parts.append("[^")
                j += 1
            else:
                parts.append("[")
            while j < n and pattern[j] != "]":
                parts.append(pattern[j])
                j += 1
            if j < n:
                parts.append("]")
                i = j + 1
            else:
                # No closing ], treat as literal
                parts[-1] = re.escape(c)
                i += 1
        else:
            parts.append(re.escape(c))
            i += 1
    return re.compile("(?:.*/)?" + "".join(parts) + "\\Z", re.DOTALL)


def grep_available() -> bool:
    try:
        subprocess.run(["rg", "--version"], capture_output=True, timeout=2)
        return True
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def grep_search(
    pattern: str,
    path: str = ".",
    glob: str = "*",
    max_results: int = 10,
    literal: bool = True,
) -> str:
    """Search files with ripgrep for symbol, text, or pattern matches.

    Defaults to literal (fixed-string) search — the pattern is matched as-is,
    not as a regex. This avoids regex escaping mistakes for common searches
    like function names, import paths, class names, or error messages.

    Use this for precise code searches: finding function definitions, import usage,
    class names, error messages, config keys, or specific text patterns in the project.
    For semantic/natural language queries, use memory_search instead.

    Args:
        pattern: Search pattern — literal text by default. Set literal=False for regex.
        path: Root directory to search. Defaults to current working directory.
        glob: File pattern filter (e.g. "*.py", "*.yaml"). Default "*" (all files).
        max_results: Maximum results to return (1-50). Default 10.
        literal: If True (default), treat pattern as fixed string, not regex.
                 Set to False when you need regex patterns like "(foo|bar)".

    Returns:
        Formatted search results with file:line:content, or a message if nothing found.
    """
    if not pattern or len(pattern.strip()) < 2:
        return "[grep_search] 搜索模式至少需要2个字符"

    max_results = max(1, min(50, max_results))

    if not grep_available():
        fallback = _python_grep_search(pattern, path, glob, max_results, literal)
        if fallback.startswith("[grep_search] 未找到"):
            return "[grep_search] ripgrep (rg) 未安装，且 Python fallback " + fallback.removeprefix("[grep_search] ")
        return "[grep_search] ripgrep (rg) 未安装，已使用 Python fallback\n" + fallback

    try:
        args = [
            "rg",
            "--no-heading", "--with-filename", "--line-number",
            "--max-count", str(max_results * 2),
            "--glob", glob,
        ]
        if literal:
            args.append("--fixed-strings")
        args += ["-e", pattern, path]
        result = subprocess.run(
            args, capture_output=True, text=True,
            timeout=15, encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return _python_grep_search(pattern, path, glob, max_results, literal)

    if result.returncode not in (0, 1) or not result.stdout:
        return f"[grep_search] 未找到匹配 '{pattern}' 的结果"

    lines = result.stdout.strip().split("\n")
    output_lines = [f"grep 搜索结果 (模式: '{pattern}'):"]
    for line in lines[:max_results]:
        if ":" not in line:
            continue
        # rg output: file:lineno:content
        idx = line.find(":")
        if idx == -1:
            continue
        idx2 = line.find(":", idx + 1)
        if idx2 == -1:
            continue
        fname = line[:idx]
        lineno = line[idx + 1:idx2]
        content = line[idx2 + 1:].strip()
        output_lines.append(f"  {fname}:{lineno}  {content}")

    if not output_lines:
        return f"[grep_search] 未找到匹配 '{pattern}' 的结果"

    return "\n".join(output_lines)


def _python_grep_search(
    pattern: str,
    path: str,
    glob: str,
    max_results: int,
    literal: bool,
) -> str:
    root = Path(path)
    if not root.exists():
        return f"[grep_search] 路径不存在: {path}"
    matcher = None if literal else re.compile(pattern)
    output_lines = [f"grep 搜索结果 (模式: '{pattern}'):"]
    files = [root] if root.is_file() else sorted(item for item in root.rglob("*") if item.is_file())

    glob_regex = _glob_to_regex(_normalize_glob_pattern(glob))

    for file_path in files:
        rel = str(file_path if root.is_file() else file_path.relative_to(root))
        if not glob_regex.match(rel) and not glob_regex.match(file_path.name):
            continue
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as handle:
                for lineno, line in enumerate(handle, start=1):
                    text = line.rstrip("\n")
                    matched = pattern in text if literal else matcher.search(text) is not None
                    if not matched:
                        continue
                    output_lines.append(f"  {rel}:{lineno}  {text.strip()}")
                    if len(output_lines) > max_results:
                        return "\n".join(output_lines)
        except OSError:
            continue
    if len(output_lines) == 1:
        return f"[grep_search] 未找到匹配 '{pattern}' 的结果"
    return "\n".join(output_lines)
