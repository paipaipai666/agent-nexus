"""ChatService.send_message attachment validation matrix.

Attachments are local files referenced by absolute path. Validation is
all-or-nothing and must run before anything is committed or started. The
agent-only manifest note rides via run(attachment_note=...) — it must NEVER
appear in the user's committed message (bubbles/history stay clean).
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentnexus.services.chat import ChatService

_MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _att(path: str, mime: str = "text/plain") -> dict:
    return {"path": path, "name": Path(path).name, "size": 1, "mime": mime}


@pytest.fixture
def agent():
    a = MagicMock()
    a.run.return_value = "ok"
    a.llm_client.capabilities.supports_vision = False
    return a


@pytest.fixture
def chat(agent):
    return ChatService(
        agent_factory=lambda _sid=None: agent,
        memory_factory_builder=lambda _sid: lambda: MagicMock(),
    )


@pytest.fixture
def session(chat):
    return chat.start_session()


def _committed_texts(chat, session_id) -> list[str]:
    return [m.get("content", "") for m in chat._get_version_manager(session_id).get_messages(limit=0)]


class TestSendMessageAttachments:
    def test_normal_attachment_commits_clean_text_and_note_to_agent(self, chat, session, agent, tmp_path):
        f = tmp_path / "note.txt"
        f.write_text("hi", encoding="utf-8")
        chat.send_message(session.id, "看看这个", attachments=[_att(str(f))])

        # User message stays clean — no manifest in bubbles/history.
        assert _committed_texts(chat, session.id) == ["看看这个"]
        # The agent gets the manifest through the note channel.
        note = agent.run.call_args.kwargs["attachment_note"]
        assert "[附件]" in note and str(f) in note and "file_read" in note

    def test_no_attachments_no_note(self, chat, session, agent):
        chat.send_message(session.id, "纯文本")
        assert _committed_texts(chat, session.id) == ["纯文本"]
        assert "attachment_note" not in agent.run.call_args.kwargs
        assert "images" not in agent.run.call_args.kwargs

    def test_two_missing_paths_one_error_listing_both(self, chat, session, agent, tmp_path):
        missing1 = str(tmp_path / "gone1.txt")
        missing2 = str(tmp_path / "gone2.txt")
        with pytest.raises(ValueError) as exc:
            chat.send_message(session.id, "hi", attachments=[_att(missing1), _att(missing2)])
        msg = str(exc.value)
        assert missing1 in msg and missing2 in msg
        # All-or-nothing: nothing committed, no run started.
        assert _committed_texts(chat, session.id) == []
        agent.run.assert_not_called()

    def test_attachment_count_limit(self, chat, session, agent, tmp_path):
        f = tmp_path / "ok.txt"
        f.write_text("x", encoding="utf-8")
        atts = [_att(str(f)) for _ in range(11)]
        with pytest.raises(ValueError, match="最多 10 个"):
            chat.send_message(session.id, "hi", attachments=atts)
        agent.run.assert_not_called()

    def test_image_boundary_exact_5mb_ok_over_rejected(self, chat, session, agent, tmp_path):
        img = tmp_path / "big.png"
        img.write_bytes(b"\x89PNG" + b"0" * (_MAX_IMAGE_BYTES - 4))
        chat.send_message(session.id, "图", attachments=[_att(str(img), "image/png")])
        agent.run.assert_called_once()  # exactly 5MB passes

        agent.reset_mock()
        img.write_bytes(b"\x89PNG" + b"0" * _MAX_IMAGE_BYTES)
        with pytest.raises(ValueError, match="超过 5MB"):
            chat.send_message(session.id, "图", attachments=[_att(str(img), "image/png")])
        agent.run.assert_not_called()

    def test_directory_attachment_rejected(self, chat, session, agent, tmp_path):
        with pytest.raises(ValueError, match="附件不可用"):
            chat.send_message(session.id, "hi", attachments=[_att(str(tmp_path))])
        agent.run.assert_not_called()

    def test_vision_off_degrades_image_to_path(self, chat, session, agent, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        chat.send_message(session.id, "看图", attachments=[_att(str(img), "image/png")])

        assert agent.run.call_args.kwargs.get("images") is None
        note = agent.run.call_args.kwargs["attachment_note"]
        assert "未启用视觉" in note
        # User text still clean.
        assert _committed_texts(chat, session.id) == ["看图"]

    def test_vision_on_sends_image_blocks(self, chat, session, agent, tmp_path):
        agent.llm_client.capabilities.supports_vision = True
        img = tmp_path / "shot.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        chat.send_message(session.id, "看图", attachments=[_att(str(img), "image/png")])

        images = agent.run.call_args.kwargs["images"]
        assert len(images) == 1
        assert images[0]["data_url"].startswith("data:image/png;base64,")
        note = agent.run.call_args.kwargs["attachment_note"]
        assert "已随本条消息" in note
        assert _committed_texts(chat, session.id) == ["看图"]

    def test_attachment_path_visible_to_tools_during_run_only(self, chat, session, agent, tmp_path):
        """file_read on the attachment works from INSIDE the run (tool threads
        included — the registry is global, not a ContextVar) and is revoked
        when the run ends."""
        from agentnexus.tools.file_ops import file_read

        f = tmp_path / "live.txt"
        f.write_text("run-scoped payload", encoding="utf-8")
        seen: list[str] = []

        def run(text, memory_manager=None, **kwargs):
            try:
                seen.append(file_read(str(f)))
            except ValueError:
                seen.append("REJECTED")
            return "ok"

        agent.run.side_effect = run
        chat.send_message(session.id, "读附件", attachments=[_att(str(f))])

        assert len(seen) == 1 and "REJECTED" not in seen[0]
        assert "run-scoped payload" in seen[0]
        with pytest.raises(ValueError, match="路径越界|out of bounds"):
            file_read(str(f))
