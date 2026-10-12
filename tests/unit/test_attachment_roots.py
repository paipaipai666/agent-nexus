"""Attachment paths extend the file_read allow-list for exactly one run.

Reference counts keep shared attachments readable until all runs release them.
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

            with pytest.raises(ValueError, match="路径越界|out of bounds"):
                file_read(str(f))
        finally:
            current_workspace.reset(ws_token)

    def test_refcount_two_runs_one_unregister_keeps_access(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        f = tmp_path / "shared.txt"
        f.write_text("shared", encoding="utf-8")
        token = current_workspace.set(str(ws))
        try:
            register_attachment_paths((str(f),))
            register_attachment_paths((str(f),))
            unregister_attachment_paths((str(f),))
            try:
                assert "shared" in file_read(str(f))
            finally:
                unregister_attachment_paths((str(f),))
            with pytest.raises(ValueError, match="路径越界|out of bounds"):
                file_read(str(f))
        finally:
            current_workspace.reset(token)
