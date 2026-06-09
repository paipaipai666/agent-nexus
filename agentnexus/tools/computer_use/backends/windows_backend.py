"""Windows accessibility backend using pywinauto (UIA).

Requires: pip install pywinauto
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from agentnexus.tools.computer_use.backends.base import ComputerUseBackend
from agentnexus.tools.computer_use.element import DesktopElement, normalize_role

logger = logging.getLogger(__name__)

# Lazy import guard
_pywinauto: Any = None


def _ensure_pywinauto() -> Any:
    """Import pywinauto on demand."""
    global _pywinauto
    if _pywinauto is None:
        try:
            import pywinauto as _mod
            _pywinauto = _mod
        except ImportError:
            raise RuntimeError(
                "pywinauto 未安装。请执行: pip install pywinauto"
            )
    return _pywinauto


class WindowsBackend(ComputerUseBackend):
    """Windows UIA backend via pywinauto."""

    def __init__(self) -> None:
        self._desktop: Any = None

    def _get_desktop(self) -> Any:
        """Get or create the pywinauto Desktop instance (UIA backend)."""
        if self._desktop is None:
            pw = _ensure_pywinauto()
            self._desktop = pw.Desktop(backend="uia")
        return self._desktop

    async def list_windows(self) -> list[dict[str, Any]]:
        """List all visible top-level windows."""
        desktop = self._get_desktop()
        windows = []
        for w in desktop.windows():
            try:
                rect = w.rectangle()
                windows.append({
                    "title": w.window_text(),
                    "app_name": w.element_info.class_name or "",
                    "bounds": (rect.left, rect.top, rect.width(), rect.height()),
                    "handle": w.handle,
                })
            except Exception:
                continue
        return windows

    async def get_snapshot(
        self,
        app_name: str | None = None,
        window_title: str | None = None,
    ) -> list[DesktopElement]:
        """Get accessibility tree for matching windows."""
        desktop = self._get_desktop()
        roots: list[DesktopElement] = []

        for w in desktop.windows():
            try:
                title = w.window_text()
                if window_title and window_title.lower() not in title.lower():
                    continue
                if app_name:
                    cls = w.element_info.class_name or ""
                    if app_name.lower() not in cls.lower() and app_name.lower() not in title.lower():
                        continue

                elem = self._build_element(w)
                if elem:
                    roots.append(elem)
            except Exception as e:
                logger.debug("Skipping window: %s", e)
                continue

        return roots

    def _build_element(self, ctrl: Any, depth: int = 0) -> DesktopElement | None:
        """Recursively build a DesktopElement from a pywinauto control."""
        if depth > 30:
            return None

        try:
            info = ctrl.element_info
            platform_role = info.control_type or "Custom"
            name = info.name or ""
            enabled = info.enabled if hasattr(info, "enabled") else True
            focused = info.has_keyboard_focus if hasattr(info, "has_keyboard_focus") else False

            # Get value
            value = None
            try:
                value = ctrl.get_value()
            except Exception:
                pass

            # Get bounds
            try:
                rect = ctrl.rectangle()
                bounds = (rect.left, rect.top, rect.width(), rect.height())
            except Exception:
                bounds = (0, 0, 0, 0)

            # Get checked state for checkboxes/radios
            checked = None
            if platform_role in ("CheckBox", "RadioButton"):
                try:
                    checked = bool(ctrl.get_toggle_state())
                except Exception:
                    pass

            # Get automation ID
            platform_id = ""
            try:
                platform_id = info.automation_id or ""
            except Exception:
                pass

            # Build children
            children: list[DesktopElement] = []
            try:
                for child in ctrl.children():
                    child_elem = self._build_element(child, depth + 1)
                    if child_elem:
                        children.append(child_elem)
            except Exception:
                pass

            return DesktopElement(
                role=normalize_role("windows", platform_role),
                name=name,
                value=str(value) if value is not None else None,
                enabled=enabled,
                focused=focused,
                checked=checked,
                bounds=bounds,
                children=tuple(children),
                platform_role=platform_role,
                platform_id=platform_id,
            )
        except Exception as e:
            logger.debug("Failed to build element: %s", e)
            return None

    async def click(
        self,
        element_id: str,
        button: str = "left",
        clicks: int = 1,
    ) -> None:
        """Click an element by automation_id or name."""
        elem = self._find_element(element_id)
        if button == "right":
            elem.click_input(button="right")
        elif clicks == 2:
            elem.double_click_input()
        else:
            elem.click_input()

    async def type_text(
        self,
        element_id: str,
        text: str,
        clear: bool = True,
    ) -> None:
        """Type text into an element."""
        elem = self._find_element(element_id)
        if clear:
            try:
                elem.set_edit_text("")
            except Exception:
                pass
        elem.type_keys(text, with_spaces=True)

    async def press_key(self, keys: str) -> None:
        """Press a key combination."""
        pw = _ensure_pywinauto()
        desktop = self._get_desktop()
        # pywinauto uses send_keys format: {CTRL}s, {ALT}{TAB}, etc.
        formatted = self._format_keys(keys)
        desktop.send_keys(formatted)

    async def scroll(
        self,
        element_id: str | None,
        direction: str = "down",
        amount: int = 3,
    ) -> None:
        """Scroll an element or the screen."""
        import pyautogui

        clicks = amount * 3
        if direction == "down":
            pyautogui.scroll(-clicks)
        elif direction == "up":
            pyautogui.scroll(clicks)
        elif direction == "left":
            pyautogui.hscroll(-clicks)
        elif direction == "right":
            pyautogui.hscroll(clicks)

    async def select(
        self,
        element_id: str,
        value: str,
    ) -> None:
        """Select a value in a combobox."""
        elem = self._find_element(element_id)
        try:
            elem.select(value)
        except Exception:
            # Fallback: try clicking the item
            items = elem.children()
            for item in items:
                if value.lower() in (item.window_text() or "").lower():
                    item.click_input()
                    return
            raise ValueError(f"找不到选项: {value}")

    async def toggle(
        self,
        element_id: str,
        checked: bool | None = None,
    ) -> None:
        """Toggle a checkbox."""
        elem = self._find_element(element_id)
        if checked is not None:
            current = bool(elem.get_toggle_state())
            if current != checked:
                elem.click_input()
        else:
            elem.click_input()

    async def launch_app(
        self,
        app_path: str,
        args: list[str] | None = None,
    ) -> dict[str, Any]:
        """Launch an application."""
        pw = _ensure_pywinauto()
        cmd = [app_path] + (args or [])
        proc = subprocess.Popen(cmd)
        return {
            "pid": proc.pid,
            "title": "",
            "app_name": app_path,
        }

    async def get_clipboard(self) -> str:
        """Read clipboard content."""
        try:
            import pyperclip
            return pyperclip.paste()
        except ImportError:
            # Fallback to Windows API
            import ctypes
            CF_UNICODETEXT = 13
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            user32.OpenClipboard(0)
            try:
                if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                    h = user32.GetClipboardData(CF_UNICODETEXT)
                    if h:
                        p = kernel32.GlobalLock(h)
                        if p:
                            try:
                                return ctypes.c_wchar_p(p).value or ""
                            finally:
                                kernel32.GlobalUnlock(h)
                return ""
            finally:
                user32.CloseClipboard()

    async def set_clipboard(self, text: str) -> None:
        """Write to clipboard."""
        try:
            import pyperclip
            pyperclip.copy(text)
        except ImportError:
            import ctypes
            CF_UNICODETEXT = 13
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            data = text.encode("utf-16-le") + b"\x00\x00"
            h = kernel32.GlobalAlloc(0x0042, len(data))
            p = kernel32.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            kernel32.GlobalUnlock(h)
            user32.OpenClipboard(0)
            try:
                user32.EmptyClipboard()
                user32.SetClipboardData(CF_UNICODETEXT, h)
            finally:
                user32.CloseClipboard()

    def _find_element(self, element_id: str) -> Any:
        """Find a pywinauto control by automation_id or name."""
        desktop = self._get_desktop()
        # Try automation_id first
        try:
            elems = desktop.windows()
            for w in elems:
                for desc in w.descendants():
                    try:
                        if desc.element_info.automation_id == element_id:
                            return desc
                    except Exception:
                        continue
        except Exception:
            pass

        # Try by name
        try:
            for w in desktop.windows():
                for desc in w.descendants():
                    try:
                        if desc.window_text() == element_id:
                            return desc
                    except Exception:
                        continue
        except Exception:
            pass

        raise ValueError(f"找不到元素: {element_id}")

    @staticmethod
    def _format_keys(keys: str) -> str:
        """Convert 'ctrl+s' to pywinauto send_keys format '{CTRL}s'."""
        parts = keys.lower().split("+")
        result = ""
        for part in parts[:-1]:
            result += "{" + part.upper() + "}"
        result += parts[-1] if parts[-1] else ""
        return result
