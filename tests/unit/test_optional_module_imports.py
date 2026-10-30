"""Public entry points remain usable without optional implementations."""

import subprocess
import sys
from pathlib import Path


def test_runtime_import_without_evaluation():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-X", "utf8",
            str(root / "scripts" / "check_module_boundaries.py"),
            "--block",
            "agentnexus.evaluation",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Imported agentnexus.app.runtime"


def test_runtime_assembles_with_all_implementations_removed():
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable, "-X", "utf8", str(root / "scripts" / "check_module_boundaries.py"), "--build-disabled"]
    for package in (
        "agentnexus.agents.re_act_agent", "agentnexus.memory.short_term", "agentnexus.memory.long_term",
        "agentnexus.wiki", "agentnexus.evaluation", "agentnexus.eval", "agentnexus.observability",
    ):
        command.extend(["--block", package])
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert "Built runtime with disabled implementations" in result.stdout


def test_default_engine_completes_with_other_implementations_removed(tmp_path):
    code = """
import importlib.abc
import sys
from dataclasses import replace
from unittest.mock import MagicMock, patch

packages = ('agentnexus.memory.short_term', 'agentnexus.memory.long_term',
            'agentnexus.wiki', 'agentnexus.evaluation', 'agentnexus.eval', 'agentnexus.observability')

class MissingImplementations(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == package or fullname.startswith(package + '.') for package in packages):
            raise ModuleNotFoundError(name=fullname)

sys.meta_path.insert(0, MissingImplementations())
from agentnexus.app.runtime import AppRuntime
from agentnexus.core.config import Settings
from agentnexus.core.modules import DEFAULT, ModuleFactories

importlib.import_module('agentnexus.agents.re_act_agent')
llm = MagicMock()
llm.model = 'test-model'
llm.last_error = ''
llm.last_truncated = False
llm.last_tool_calls = []
llm.last_reasoning_content = ''
llm.last_usage = {'input_tokens': 0, 'output_tokens': 0}
llm.capabilities.supports_thinking = False
llm.capabilities.supports_tool_calling = True
llm.capabilities.supports_json_mode = True
llm.capabilities.supports_json_schema = False
llm.capabilities.supports_parallel_tool_calls = False
llm.capabilities.thinking_effort = 'none'
llm.think.return_value = 'default engine still works'
factories = replace(ModuleFactories.disabled(), agent=DEFAULT)
settings = Settings(memory_db_path=sys.argv[1], default_skill='', skill_auto_route=False)
with patch('agentnexus.core.llm.AgentLLM', return_value=llm):
    with AppRuntime.build(settings=settings, module_factories=factories) as runtime:
        session = runtime.chat.start_session()
        run = runtime.chat.send_message(session.id, 'answer directly')
        outcome = runtime.chat.get_run_recovery(run.id)
        assert outcome['answer'] == 'default engine still works', outcome
        assert outcome['persisted'] is True
assert not any(name == package or name.startswith(package + '.') for name in sys.modules for package in packages)
print('Default engine completed without the other implementations')
"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(tmp_path / "history.db")],
                            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert "Default engine completed without the other implementations" in result.stdout
