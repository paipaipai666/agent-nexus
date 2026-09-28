"""Attachment paths extend the file_read allow-list for exactly one run.

Registry is global module state (not a ContextVar) on purpose: tool calls
execute on dispatcher lane threads that never inherit context.
"""

import pytest

from agentnexus.tools.file_ops import file_read
from agentnexus.tools.workspace import (
    current_workspace,
    register_attachment_paths,
    unregister_attachment_paths,
)


class TestAttachmentRoots:
    def test_registered_attachment_readable_then_revoked(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        f = tmp_path / "outside_ws_note.txt"
        f.write_text("attachment payload", encoding="utf-8")

        ws_token = current_workspace.set(str(ws))
        try:
            with pytest.raises(ValueError, match="路径越界|out of bounds"):
                file_read(str(f))

            register_attachment_paths((str(f),))
            try:
                assert "attachment payload" in file_read(str(f))
            finally:
                unregister_attachment_paths((str(f),))

            # Unregister revokes access — no cross-run leakage.
            with pytest.raises(ValueError, match="路径越界|out of bounds"):
                file_read(str(f))
        finally:
            current_workspace.reset(ws_token)

    def test_refcount_two_runs_one_unregister_keeps_access(self, tmp_path):
        f = tmp_path / "shared.txt"
        f.write_text("shared", encoding="utf-8")
        register_attachment_paths((str(f),))
        register_attachment_paths((str(f),))
        unregister_attachment_paths((str(f),))
        try:
            assert "shared" in file_read(str(f))
        finally:
            unregister_attachment_paths((str(f),))
        with pytest.raises(ValueError, match="路径越界|out of bounds"):
            file_read(str(f))
