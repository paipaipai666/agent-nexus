"""jieba loader with a pkg_resources compatibility shim.

jieba 0.42.1 (unmaintained) loads its dictionary via
``pkg_resources.resource_stream``; setuptools 82 (2026-02) removed
pkg_resources, and environments where a partial pkg_resources importable
(e.g. the GitHub macos runners) crash jieba at first ``cut()``.

When the stock loader is broken, each jieba namespace's ``get_module_res``
is replaced with a plain file loader pointing into the jieba package
directory — the same thing jieba's own fallback does. Environments where
the stock loader works are untouched.
"""

from __future__ import annotations

import os
import threading

_jieba = None
_lock = threading.Lock()


def _patch_resource_loading(jieba) -> None:
    """Replace broken pkg_resources-based get_module_res with a file loader."""
    try:
        stream = jieba.get_module_res(jieba.DEFAULT_DICT_NAME)
        stream.close()
        return  # stock path works — nothing to do
    except Exception:
        pass

    pkg_dir = os.path.dirname(jieba.__file__)

    def _get_module_res(*res):
        return open(os.path.join(pkg_dir, *res), "rb")

    # from-import* gave each submodule its own binding — patch them all
    targets = [jieba]
    try:
        import jieba._compat
        targets.append(jieba._compat)
    except ImportError:
        pass
    try:
        import jieba.finalseg
        targets.append(jieba.finalseg)
    except ImportError:
        pass
    for mod in targets:
        if hasattr(mod, "get_module_res"):
            mod.get_module_res = _get_module_res


def load_jieba():
    """Import jieba once, applying the resource-loading shim if needed."""
    global _jieba
    if _jieba is None:
        with _lock:
            if _jieba is None:
                import jieba
                _patch_resource_loading(jieba)
                _jieba = jieba
    return _jieba
