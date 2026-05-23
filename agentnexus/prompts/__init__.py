"""Prompt loader + contextual helpers — readable templates with current date injection."""
from datetime import datetime, timezone
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")


def get_current_date() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def format_prompt(name: str, **kwargs) -> str:
    template = load_prompt(name)
    kwargs.setdefault("date", get_current_date())
    return template.format(**kwargs)
