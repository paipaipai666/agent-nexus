"""Persist terminal drafts independently for each conversation."""

import hashlib
import json
import logging
import os
import tempfile

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


class DraftAttachment(BaseModel):
    """Validated metadata for a pending local attachment."""

    path: str
    name: str
    mime: str
    size: int = Field(ge=0)


class DraftState(BaseModel):
    """Independent input, file draft and attachments for one conversation."""

    input: str = ""
    draft: str = ""
    attachments: list[DraftAttachment] = Field(default_factory=list)


class DraftStore:
    """Store validated drafts using atomic replacements and session-specific paths."""

    def __init__(self):
        from agentnexus.core.config import get_config_dir

        self.directory = get_config_dir() / "tui-drafts"

    def _path(self, sid):
        return self.directory / (hashlib.sha256(sid.encode()).hexdigest() + ".json")

    def load(self, sid):
        """Restore a validated session draft or an empty state."""
        try:
            return DraftState.model_validate_json(self._path(sid).read_text(encoding="utf-8")).model_dump()
        except FileNotFoundError:
            return DraftState().model_dump()
        except (OSError, ValidationError):
            logger.warning("Unable to restore terminal draft for session %s", sid)
            return DraftState().model_dump()

    def save(self, sid, state):
        """Atomically save a session draft or remove an empty draft."""
        state = DraftState.model_validate(state).model_dump()
        if not any(state.values()):
            self._path(sid).unlink(missing_ok=True)
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory, delete=False) as file:
            temporary = file.name
            json.dump(state, file, ensure_ascii=False)
        try:
            os.replace(temporary, self._path(sid))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
