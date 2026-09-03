"""jieba_loader: pkg_resources removal compatibility shim."""

from __future__ import annotations


def test_load_jieba_and_cut():
    from agentnexus.core.jieba_loader import load_jieba

    jieba = load_jieba()
    tokens = list(jieba.cut("结巴分词是中文分词工具"))
    assert "结巴" in tokens or "结巴分词" in tokens
    # cached singleton
    assert load_jieba() is jieba


def test_patches_broken_resource_loader(monkeypatch):
    """Simulate setuptools>=82: pkg_resources exists but resource_stream is gone."""
    import jieba
    import jieba._compat
    import jieba.finalseg

    from agentnexus.core import jieba_loader

    def _broken(*_res):
        raise AttributeError("module 'pkg_resources' has no attribute 'resource_stream'")

    # Break the stock loader in every namespace; monkeypatch restores the
    # originals afterwards, so the working environment is unaffected.
    monkeypatch.setattr(jieba, "get_module_res", _broken)
    monkeypatch.setattr(jieba._compat, "get_module_res", _broken)
    monkeypatch.setattr(jieba.finalseg, "get_module_res", _broken)

    jieba_loader._patch_resource_loading(jieba)

    stream = jieba.get_module_res(jieba.DEFAULT_DICT_NAME)
    try:
        assert stream.read(16)
    finally:
        stream.close()
    # finalseg's own binding was patched too
    assert jieba.finalseg.get_module_res is jieba.get_module_res
