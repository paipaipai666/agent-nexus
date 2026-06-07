"""Platform-specific accessibility backends.

Provides automatic backend selection based on sys.platform.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentnexus.tools.computer_use.backends.base import ComputerUseBackend


def get_backend(preferred: str = "auto") -> ComputerUseBackend:
    """Instantiate the appropriate platform backend.

    Args:
        preferred: 'auto' to detect platform, or 'windows'/'linux'/'macos'.

    Returns:
        A ComputerUseBackend instance for the current platform.

    Raises:
        RuntimeError: If the platform is unsupported or required libraries are missing.
    """
    if preferred == "auto":
        preferred = _detect_platform()

    if preferred == "windows":
        from agentnexus.tools.computer_use.backends.windows_backend import WindowsBackend
        return WindowsBackend()

    if preferred == "linux":
        from agentnexus.tools.computer_use.backends.linux_backend import LinuxBackend
        return LinuxBackend()

    if preferred == "macos":
        from agentnexus.tools.computer_use.backends.macos_backend import MacOSBackend
        return MacOSBackend()

    raise RuntimeError(f"不支持的 computer_use 后端: {preferred}，可选: auto, windows, linux, macos")


def _detect_platform() -> str:
    """Detect the current platform string."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"
