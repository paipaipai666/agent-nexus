"""Test helper replacing click 8.5's removed CliRunner.isolated_filesystem().

Runs the block inside a fresh temp working directory and restores the
original cwd afterwards. Does NOT touch environment variables — callers
manage AGENTNEXUS_HOME themselves, same as before.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager


@contextmanager
def isolated_filesystem():
    """Chdir into a temporary directory for the duration of the block."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            yield tmp
        finally:
            os.chdir(old_cwd)
