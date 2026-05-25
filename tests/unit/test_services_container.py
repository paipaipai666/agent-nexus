"""Tests for agentnexus.services.container.

Covers the dependency injection / service wiring layer.
"""
import pytest

from agentnexus.services.container import AppServices


class TestAppServices:
    """AppServices dataclass behavior."""

    def test_create_with_all_fields(self):
        svc = AppServices(
            chat="chat_svc",
            skill="skill_svc",
            knowledge_base="kb_svc",
            eval="eval_svc",
            config="config_svc",
        )
        assert svc.chat == "chat_svc"
        assert svc.skill == "skill_svc"
        assert svc.knowledge_base == "kb_svc"
        assert svc.eval == "eval_svc"
        assert svc.config == "config_svc"

    def test_is_frozen(self):
        svc = AppServices(
            chat="chat_svc",
            skill="skill_svc",
            knowledge_base="kb_svc",
            eval="eval_svc",
            config="config_svc",
        )
        with pytest.raises(AttributeError):
            svc.chat = "modified"

    def test_equality(self):
        s1 = AppServices("a", "b", "c", "d", "e")
        s2 = AppServices("a", "b", "c", "d", "e")
        s3 = AppServices("x", "b", "c", "d", "e")
        assert s1 == s2
        assert s1 != s3

    def test_hashable(self):
        s1 = AppServices("a", "b", "c", "d", "e")
        s2 = AppServices("a", "b", "c", "d", "e")
        assert hash(s1) == hash(s2)

    def test_repr(self):
        svc = AppServices("a", "b", "c", "d", "e")
        r = repr(svc)
        assert "AppServices" in r
