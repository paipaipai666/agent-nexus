"""Prompt 加载器 — 从 prompts/*.txt 读取，返回 Python format 模板"""
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")
