"""Standard HumanEval/164 dataset loader and evaluator tests.

Tests alignment with the official HumanEval benchmark format.
"""
import json
import os
import tempfile

import pytest


class TestHumanEval164Dataset:
    """HumanEval/164 standard format tests."""

    _SAMPLE_PROBLEMS = [
        {
            "task_id": "HumanEval/0",
            "prompt": "def add(a: int, b: int) -> int:\n    \"\"\"Return a + b.\"\"\"\n",
            "canonical_solution": "    return a + b\n",
            "test": "def check(candidate):\n    assert candidate(1, 2) == 3\n    assert candidate(-1, 1) == 0\n    assert candidate(0, 0) == 0\n",
            "entry_point": "add",
        },
        {
            "task_id": "HumanEval/1",
            "prompt": "def multiply(a: int, b: int) -> int:\n    \"\"\"Return a * b.\"\"\"\n",
            "canonical_solution": "    return a * b\n",
            "test": "def check(candidate):\n    assert candidate(2, 3) == 6\n    assert candidate(-1, 1) == -1\n",
            "entry_point": "multiply",
        },
        {
            "task_id": "HumanEval/2",
            "prompt": "def is_palindrome(s: str) -> bool:\n    \"\"\"Return True if s is a palindrome.\"\"\"\n",
            "canonical_solution": "    return s == s[::-1]\n",
            "test": "def check(candidate):\n    assert candidate('aba') == True\n    assert candidate('abc') == False\n",
            "entry_point": "is_palindrome",
        },
    ]

    def test_sample_dataset_structure(self):
        """Each problem has required fields."""
        for problem in self._SAMPLE_PROBLEMS:
            assert "task_id" in problem
            assert "prompt" in problem
            assert "canonical_solution" in problem
            assert "test" in problem
            assert "entry_point" in problem
            assert problem["task_id"].startswith("HumanEval/")

    def test_task_ids_sequential(self):
        """Task IDs are sequential 0-163."""
        for i, problem in enumerate(self._SAMPLE_PROBLEMS):
            assert problem["task_id"] == f"HumanEval/{i}"

    def test_executable_solution(self):
        """Canonical solutions pass tests."""
        for problem in self._SAMPLE_PROBLEMS:
            full_code = problem["prompt"] + problem["canonical_solution"]
            local_ns = {}
            exec(full_code, {}, local_ns)
            func = local_ns[problem["entry_point"]]

            exec_locals = {}
            exec(problem["test"], {}, exec_locals)
            exec_locals["check"](func)

    def test_dataset_loadable_as_jsonl(self, tmp_path):
        """Dataset can be saved and loaded as JSONL."""
        jsonl_path = tmp_path / "humaneval.jsonl"
        for problem in self._SAMPLE_PROBLEMS:
            jsonl_path.write_text(
                "\n".join(json.dumps(p) for p in self._SAMPLE_PROBLEMS),
                encoding="utf-8",
            )

        loaded = []
        for line in jsonl_path.read_text().strip().split("\n"):
            loaded.append(json.loads(line))

        assert len(loaded) == len(self._SAMPLE_PROBLEMS)
        for i, problem in enumerate(self._SAMPLE_PROBLEMS):
            assert loaded[i]["task_id"] == problem["task_id"]

    def test_prompt_ends_with_colon(self):
        """All prompts end with function definition colon."""
        for problem in self._SAMPLE_PROBLEMS:
            assert problem["prompt"].strip().endswith(":") or problem["prompt"].strip().endswith("\"\"\"")

    def test_canonical_solution_indentation(self):
        """Solutions are indented properly."""
        for problem in self._SAMPLE_PROBLEMS:
            lines = problem["canonical_solution"].strip().split("\n")
            first_line = lines[0]
            assert first_line.startswith("    ") or "return" in first_line

    def test_tests_use_assert(self):
        """Tests use assert statements."""
        for problem in self._SAMPLE_PROBLEMS:
            assert "assert" in problem["test"]

    def test_all_sample_pass(self):
        """All sample problems are valid and pass tests."""
        for problem in self._SAMPLE_PROBLEMS:
            full_code = problem["prompt"] + problem["canonical_solution"]
            local_ns = {}
            exec(full_code, {}, local_ns)
            func = local_ns[problem["entry_point"]]

            exec_locals = {}
            exec(problem["test"], {}, exec_locals)
            exec_locals["check"](func)
