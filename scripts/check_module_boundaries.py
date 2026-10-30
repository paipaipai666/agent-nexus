"""Probe application imports with selected implementation packages absent."""

from __future__ import annotations

import argparse
import importlib
import importlib.abc
import sys
from pathlib import Path


class MissingImplementations(importlib.abc.MetaPathFinder):
    """Simulate uninstalled packages without changing the checkout."""

    def __init__(self, packages: list[str]):
        self.packages = tuple(packages)

    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == package or fullname.startswith(package + ".") for package in self.packages):
            raise ModuleNotFoundError(f"Implementation removed: {fullname}", name=fullname)
        return None


def main() -> None:
    """Import a public application entry point with unavailable modules."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--block", action="append", default=[])
    parser.add_argument("--import-module", default="agentnexus.app.runtime")
    parser.add_argument("--build-disabled", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.meta_path.insert(0, MissingImplementations(args.block))
    module = importlib.import_module(args.import_module)
    if args.build_disabled:
        from agentnexus.core.config import Settings
        from agentnexus.core.modules import ModuleFactories

        settings = Settings(memory_db_path=":memory:")
        with module.AppRuntime.build(settings=settings, module_factories=ModuleFactories.disabled()) as runtime:
            session = runtime.chat.start_session()
            assert runtime.chat.get_session_workspace(session.id) is None
            assert all(not status["enabled"] for status in runtime.modules.status().values())
        print("Built runtime with disabled implementations")
        return
    print(f"Imported {module.__name__}")


if __name__ == "__main__":
    main()
