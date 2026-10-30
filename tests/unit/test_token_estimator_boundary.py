"""Skill prompt budgeting does not depend on retained short-term memory."""

import subprocess
import sys
from pathlib import Path


def test_skill_budget_estimates_match_with_short_term_implementation_absent():
    program = '''
import importlib.abc
import sys

attempts = []
class MissingMemory(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "agentnexus.memory.short_term":
            attempts.append(fullname)
            raise ModuleNotFoundError(fullname, name=fullname)

sys.meta_path.insert(0, MissingMemory())
from agentnexus.skills.router.retrieve import estimate_text_tokens
assert estimate_text_tokens("hello world") == 2
assert estimate_text_tokens("") == 0
assert attempts == [], attempts
print("skill token budgeting works without short-term memory")
'''
    result = subprocess.run([sys.executable, "-c", program], cwd=Path(__file__).resolve().parents[2],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skill token budgeting works" in result.stdout


def test_default_short_term_and_skill_budget_share_the_same_text_estimate():
    from agentnexus.memory.short_term import ShortTermMemory
    from agentnexus.skills.router.retrieve import estimate_text_tokens

    memory = ShortTermMemory()
    memory.append("user", "hello world")
    assert estimate_text_tokens("hello world") == 2
    assert memory.token_count == 2
    assert memory.estimate_tokens() == 2
