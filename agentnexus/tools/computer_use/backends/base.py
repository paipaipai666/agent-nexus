"""Abstract base class for platform accessibility backends.

Each backend implements the same interface using platform-specific APIs:
- Windows: pywinauto (UIA)
- Linux: gi.repository.Atspi (AT-SPI2)
- macOS: pyobjc ApplicationServices (AX)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agentnexus.tools.computer_use.element import DesktopElement


class ComputerUseBackend(ABC):
    """Abstract interface for desktop accessibility backends."""

    @abstractmethod
    async def list_windows(self) -> list[dict[str, Any]]:
        """List all visible top-level windows.

        Returns:
            List of dicts with at least 'title', 'app_name', 'bounds' keys.
        """

    @abstractmethod
    async def get_snapshot(
        self,
        app_name: str | None = None,
        window_title: str | None = None,
    ) -> list[DesktopElement]:
        """Get the accessibility tree for a window.

        Args:
            app_name: Filter by application name (partial match).
            window_title: Filter by window title (partial match).

        Returns:
            List of root DesktopElement trees (typically one per window).
        """

    @abstractmethod
    async def click(
        self,
        element_id: str,
        button: str = "left",
        clicks: int = 1,
    ) -> None:
        """Click an element identified by platform_id.

        Args:
            element_id: Platform-specific element identifier.
            button: 'left', 'right', or 'middle'.
            clicks: Number of clicks (1=single, 2=double).
        """

    @abstractmethod
    async def type_text(
        self,
        element_id: str,
        text: str,
        clear: bool = True,
    ) -> None:
        """Type text into an element.

        Args:
            element_id: Platform-specific element identifier.
            text: Text to type.
            clear: Whether to clear the field first.
        """

    @abstractmethod
    async def press_key(self, keys: str) -> None:
        """Press a key combination.

        Args:
            keys: Key combo string like 'ctrl+s', 'alt+tab', 'enter'.
        """

    @abstractmethod
    async def scroll(
        self,
        element_id: str | None,
        direction: str = "down",
        amount: int = 3,
    ) -> None:
        """Scroll an element or the screen.

        Args:
            element_id: Platform-specific element identifier, or None for screen scroll.
            direction: 'up', 'down', 'left', 'right'.
            amount: Number of scroll steps.
        """

    @abstractmethod
    async def select(
        self,
        element_id: str,
        value: str,
    ) -> None:
        """Select a value in a combobox/list element.

        Args:
            element_id: Platform-specific element identifier.
            value: Value to select.
        """

    @abstractmethod
    async def toggle(
        self,
        element_id: str,
        checked: bool | None = None,
    ) -> None:
        """Toggle a checkbox or switch element.

        Args:
            element_id: Platform-specific element identifier.
            checked: True=check, False=uncheck, None=toggle.
        """

    @abstractmethod
    async def launch_app(
        self,
        app_path: str,
        args: list[str] | None = None,
    ) -> dict[str, Any]:
        """Launch an application.

        Args:
            app_path: Path to the application executable.
            args: Command-line arguments.

        Returns:
            Dict with 'pid', 'title', 'app_name' keys.
        """

    @abstractmethod
    async def get_clipboard(self) -> str:
        """Read the current clipboard content."""

    @abstractmethod
    async def set_clipboard(self, text: str) -> None:
        """Write text to the clipboard."""

    async def close(self) -> None:
        """Release backend resources. Override if needed."""
