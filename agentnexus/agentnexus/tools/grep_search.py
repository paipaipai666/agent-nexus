"""grep_search tool — ripgrep code search for exact symbol/pattern matching."""

import subprocess


def grep_available() -> bool:
    try:
        subprocess.run(["rg", "--version"], capture_output=True, timeout=2)
        return True
    except FileNotFoundError:
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

    if not grep_available():
        return "[grep_search] ripgrep (rg) 未安装，此工具不可用"

    max_results = max(1, min(50, max_results))

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
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "[grep_search] 搜索超时或 rg 不可用"

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
