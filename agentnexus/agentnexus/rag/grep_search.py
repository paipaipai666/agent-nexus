"""ripgrep 精确匹配检索"""
import subprocess
from pathlib import Path


def grep_search(query: str, root_dir: str = ".", top_k: int = 5, file_pattern: str = "*") -> list[dict]:
    try:
        args = [
            "rg",
            "--no-heading", "--with-filename", "--line-number",
            "--max-count", str(top_k * 3),
            "--glob", file_pattern,
            "-e", query,
            root_dir,
        ]
        result = subprocess.run(args, capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace")
        if result.returncode not in (0, 1) or not result.stdout:
            return []

        lines = result.stdout.strip().split("\n")
        results = []
        for line in lines[:top_k]:
            if ":" not in line:
                continue
            # 格式: file:lineno:content
            parts = line.split(":", 2)
            if len(parts) >= 3:
                results.append({
                    "file": parts[0],
                    "line": int(parts[1]),
                    "text": parts[2].strip(),
                    "source": "grep",
                })
        return results
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def grep_available() -> bool:
    try:
        subprocess.run(["rg", "--version"], capture_output=True, timeout=2)
        return True
    except FileNotFoundError:
        return False
