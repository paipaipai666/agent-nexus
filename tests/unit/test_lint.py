"""Verify the source code passes ruff linter checks.

Scoped to ``agentnexus/`` only — the CI workflow runs ``ruff check tests/``
separately, and test-parallel temp files can cause transient failures.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_ruff_check_passes():
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "agentnexus/"],
        capture_output=True, text=False,
        cwd=str(REPO_ROOT),
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    assert proc.returncode == 0, (
        f"ruff check exited {proc.returncode}\n{out}\n{err}"
    )
