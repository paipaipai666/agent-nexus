"""Tests for ChatService message queue functionality."""

from unittest.mock import MagicMock

from agentnexus.services.chat import ChatService


def _make_service() -> ChatService:
    agent = MagicMock()
    agent.run.return_value = MagicMock(answer="test answer")
    return ChatService(agent=agent)


class TestMessageQueue:
    def test_enqueue_returns_position(self):
        service = _make_service()
        session = service.start_session()
        pos = service.enqueue_message(session.id, "hello")
        assert pos == 1
        pos2 = service.enqueue_message(session.id, "world")
        assert pos2 == 2

    def test_dequeue_returns_fifo_order(self):
        service = _make_service()
        session = service.start_session()
        service.enqueue_message(session.id, "first")
        service.enqueue_message(session.id, "second")
        item = service.dequeue_message()
        assert item == (session.id, "first")
        item = service.dequeue_message()
        assert item == (session.id, "second")

    def test_dequeue_empty_returns_none(self):
        service = _make_service()
        assert service.dequeue_message() is None

    def test_queue_size(self):
        service = _make_service()
        session = service.start_session()
        assert service.queue_size == 0
        service.enqueue_message(session.id, "a")
        assert service.queue_size == 1
        service.enqueue_message(session.id, "b")
        assert service.queue_size == 2
        service.dequeue_message()
        assert service.queue_size == 1

    def test_is_processing_default_false(self):
        service = _make_service()
        assert service.is_processing is False

    def test_mark_processing(self):
        service = _make_service()
        service.mark_processing(True)
        assert service.is_processing is True
        service.mark_processing(False)
        assert service.is_processing is False

    def test_multiple_sessions_queued(self):
        service = _make_service()
        s1 = service.start_session()
        s2 = service.start_session()
        service.enqueue_message(s1.id, "msg from s1")
        service.enqueue_message(s2.id, "msg from s2")
        assert service.dequeue_message() == (s1.id, "msg from s1")
        assert service.dequeue_message() == (s2.id, "msg from s2")
