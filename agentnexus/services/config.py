"""Configuration service facade."""

from __future__ import annotations

from typing import Any


class ConfigService:
    def __init__(self, settings: Any, extension_manager: Any = None):
        self.settings = settings
        self.extension_manager = extension_manager

    def get_settings(self) -> Any:
        return self.settings

    def extension_status(self) -> Any:
        if self.extension_manager is None:
            return None
        return self.extension_manager.status()

