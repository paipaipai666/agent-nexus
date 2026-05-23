import time

from agentnexus.memory.short_term import ShortTermMemory


class TestShortTermMemory:

    def test_append_and_get_all(self):
        stm = ShortTermMemory()
        stm.append("user", "你好")
        stm.append("assistant", "你好，有什么可以帮你？")
        msgs = stm.get_all()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "你好"
        assert msgs[1]["role"] == "assistant"

    def test_ts_field_present(self):
        stm = ShortTermMemory()
        stm.append("user", "hello")
        msgs = stm.get_all()
        assert "ts" in msgs[0]
        assert isinstance(msgs[0]["ts"], float)

    def test_maxlen_boundary(self):
        stm = ShortTermMemory(max_messages=3)
        for i in range(5):
            stm.append("user", f"msg{i}")
        msgs = stm.get_all()
        assert len(msgs) == 3
        # oldest messages dropped, newest retained
        assert msgs[0]["content"] == "msg2"
        assert msgs[2]["content"] == "msg4"

    def test_token_estimation_english(self):
        stm = ShortTermMemory()
        stm.append("user", "hello world this is a test")
        tokens = stm.estimate_tokens()
        # 33 ASCII chars * 0.4 ≈ 13 tokens
        assert tokens > 0
        assert tokens < 50

    def test_token_estimation_chinese(self):
        stm = ShortTermMemory()
        stm.append("user", "你好世界这是一个测试")
        tokens = stm.estimate_tokens()
        # 10 Chinese chars * 1.4 ≈ 14 tokens
        assert tokens > 0
        assert tokens < 50

    def test_token_estimation_mixed(self):
        stm = ShortTermMemory()
        stm.append("user", "你好hello世界world")
        tokens = stm.estimate_tokens()
        assert tokens > 0

    def test_compact_with_keep_recent(self):
        stm = ShortTermMemory()
        for i in range(10):
            stm.append("user", f"msg{i}")
        stm.compact("这是摘要", keep_recent=3)
        msgs = stm.get_all()
        # 1 system summary + 3 recent = 4 total
        assert len(msgs) == 4
        assert msgs[0]["role"] == "system"
        assert "[会话摘要]" in msgs[0]["content"]
        assert "这是摘要" in msgs[0]["content"]
        # last 3 messages retained
        assert msgs[1]["content"] == "msg7"
        assert msgs[2]["content"] == "msg8"
        assert msgs[3]["content"] == "msg9"

    def test_compact_fewer_than_keep(self):
        stm = ShortTermMemory()
        stm.append("user", "msg0")
        stm.append("user", "msg1")
        stm.compact("摘要", keep_recent=5)
        msgs = stm.get_all()
        assert len(msgs) == 3  # 1 summary + 2 original
        assert msgs[0]["role"] == "system"
        assert msgs[1]["content"] == "msg0"
        assert msgs[2]["content"] == "msg1"

    def test_clear(self):
        stm = ShortTermMemory()
        stm.append("user", "hello")
        stm.append("assistant", "world")
        stm.compact("summary", keep_recent=1)
        msgs = stm.get_all()
        # 1 summary + 1 recent = 2
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        stm.clear()
        assert len(stm.get_all()) == 0

    def test_snip_normal(self):
        stm = ShortTermMemory()
        for i in range(20):
            stm.append("user", f"msg{i}")
        removed = stm.snip(keep_recent=5)
        assert removed == 15
        msgs = stm.get_all()
        assert len(msgs) == 6  # 1 boundary marker + 5 recent
        assert msgs[0]["role"] == "system"
        assert "上下文已裁剪" in msgs[0]["content"]
        assert "15" in msgs[0]["content"]
        assert msgs[1]["content"] == "msg15"
        assert msgs[5]["content"] == "msg19"

    def test_snip_noop_when_few_messages(self):
        stm = ShortTermMemory()
        for i in range(5):
            stm.append("user", f"msg{i}")
        removed = stm.snip(keep_recent=10)
        assert removed == 0
        msgs = stm.get_all()
        assert len(msgs) == 5
        # no boundary marker inserted
        assert msgs[0]["content"] == "msg0"

    def test_get_last_ts(self):
        stm = ShortTermMemory()
        assert stm.get_last_ts() == 0.0
        stm.append("user", "msg0")
        ts0 = stm.get_last_ts()
        assert ts0 > 0
        time.sleep(0.01)
        stm.append("user", "msg1")
        ts1 = stm.get_last_ts()
        assert ts1 > ts0

    def test_snip_mixed_roles(self):
        stm = ShortTermMemory()
        stm.append("system", "welcome")
        stm.append("user", "question")
        stm.append("assistant", "answer")
        stm.append("tool", "result1")
        stm.append("tool", "result2")
        stm.append("assistant", "final")
        removed = stm.snip(keep_recent=3)
        assert removed == 3
        msgs = stm.get_all()
        assert len(msgs) == 4  # 1 boundary + 3 recent
        assert msgs[1]["content"] == "result1"
        assert msgs[2]["content"] == "result2"
        assert msgs[3]["content"] == "final"
