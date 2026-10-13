"""Run recovery regressions with isolated state and retain the test result."""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest


def main() -> int:
    """Keep all test homes and pytest files inside a unique workspace directory."""
    root = Path(__file__).resolve().parents[1]
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(Path(tempfile.gettempdir()) / "data-gym-cache"))
    sys.path.insert(0, str(root))
    temporary_root = root / ".tmp_pytest"
    temporary_root.mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="recovery-", dir=temporary_root))
    if not output.resolve().is_relative_to(temporary_root.resolve()):
        raise ValueError("Test output must stay inside the workspace")
    tempfile.tempdir = str(output)
    arguments = sys.argv[1:] or ["tests/unit/test_failure_recovery.py"]
    result = pytest.main([
        *arguments, "-q", "-p", "no:cacheprovider", f"--basetemp={output / 'pytest'}", "--tb=short",
    ])
    (output / "result.json").write_text(
        json.dumps({"arguments": arguments, "exit_code": int(result)}, indent=2), encoding="utf-8",
    )
    print(f"Recovery test result: {output / 'result.json'}")
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
