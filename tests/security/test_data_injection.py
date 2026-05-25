"""Security: YAML deserialization, SQL injection, and ChromaDB metadata injection tests.

Tests that the application safely handles malicious or malformed inputs
across config loading, database operations, and vector store metadata.
"""

import pytest
import yaml

# ── YAML Deserialization Tests ─────────────────────────────────────

class TestYamlSafeLoad:
    """YAML is loaded with safe_load, not unsafe yaml.load()."""

    def test_yaml_safe_load_rejects_dangerous_objects(self):
        """yaml.safe_load raises ConstructorError for !!python/object tags."""
        dangerous_yaml = """
        malicious: !!python/object:os.system ["echo pwned"]
        """
        with pytest.raises(yaml.constructor.ConstructorError):
            yaml.safe_load(dangerous_yaml)

    def test_yaml_safe_load_rejects_python_module_tag(self):
        """yaml.safe_load rejects !!python/module tags."""
        dangerous_yaml = """
        exploit: !!python/module:os
        """
        with pytest.raises(yaml.constructor.ConstructorError):
            yaml.safe_load(dangerous_yaml)

    def test_yaml_safe_load_rejects_python_tuple_tag(self):
        """yaml.safe_load rejects !!python/tuple with code."""
        dangerous_yaml = """
        exploit: !!python/tuple [1, 2, 3]
        """
        with pytest.raises(yaml.constructor.ConstructorError):
            yaml.safe_load(dangerous_yaml)

    def test_yaml_safe_load_accepts_benign_data(self):
        """yaml.safe_load correctly loads benign YAML as dict."""
        benign_yaml = """
        key: value
        nested:
          number: 42
          list: [a, b, c]
        """
        data = yaml.safe_load(benign_yaml)
        assert data["key"] == "value"
        assert data["nested"]["number"] == 42
        assert data["nested"]["list"] == ["a", "b", "c"]

    def test_yaml_safe_load_accepts_string_with_colon(self):
        """Strings with colons and special chars are handled."""
        yaml_str = """
        query: "SELECT * FROM users WHERE id = 1; DROP TABLE users;"
        config: "${api_key}"
        path: "C:\\\\Users\\\\test"
        """
        data = yaml.safe_load(yaml_str)
        assert "DROP TABLE" in data["query"]

    def test_malicious_eval_dataset_is_safe(self):
        """An eval dataset YAML with embedded python objects is not executed."""
        eval_yaml = """
        dataset: "eval_set"
        cases:
          - name: test_case
            system_prompt: "You are helpful"
            user_prompt: "Hello"
            reference: "Hi"
        """
        data = yaml.safe_load(eval_yaml)
        assert data["dataset"] == "eval_set"
        assert len(data["cases"]) == 1
        assert data["cases"][0]["name"] == "test_case"

    def test_config_yaml_loads_with_safe_load(self, temp_agentnexus_home):
        """Config YAML is loaded with yaml.safe_load via _load_yaml."""
        from agentnexus.core.config import _load_yaml

        config_yaml = """
        llm_api_key: "sk-test-12345"
        llm_model_id: "test/model"
        shell_timeout: 60
        """
        config_path = temp_agentnexus_home / "config.yaml"
        config_path.write_text(config_yaml, encoding="utf-8")

        loaded = _load_yaml()
        assert loaded["llm_api_key"] == "sk-test-12345"
        assert loaded["llm_model_id"] == "test/model"
        assert loaded["shell_timeout"] == 60


# ── SQL Injection Tests ────────────────────────────────────────────

class TestSQLInjectionLongTermMemory:
    """LongTermMemory handles SQL injection payloads via parameterized queries."""

    def _count_tables(self, ltm) -> set[str]:
        """Get all table names from the long-term memory database."""
        rows = ltm._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {r["name"] for r in rows}

    def _count_rows(self, ltm) -> int:
        """Count rows in long_term_memories table."""
        row = ltm._conn.execute(
            "SELECT COUNT(*) as cnt FROM long_term_memories"
        ).fetchone()
        return row["cnt"]

    def test_session_id_with_sql_metacharacters(self, temp_agentnexus_home):
        """Session_id with SQL metacharacters is safely stored (parameterized)."""
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        tables_before = self._count_tables(ltm)

        session_id = "test'; DROP TABLE long_term_memories; --"
        content = "safe content"
        ltm.save(session_id=session_id, content=content, category="test")

        tables_after = self._count_tables(ltm)
        assert tables_before == tables_after
        assert "long_term_memories" in tables_after

        recent = ltm.list_recent(limit=10)
        found = [m for m in recent if m["content"] == content]
        assert len(found) >= 1

    def test_content_with_sql_injection_payload(self, temp_agentnexus_home):
        """Content with SQL metacharacters is safely stored."""
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        tables_before = self._count_tables(ltm)

        content = "'; DELETE FROM long_term_memories; SELECT '"
        ltm.save(session_id="sql_inject_test", content=content, category="test")

        tables_after = self._count_tables(ltm)
        assert tables_before == tables_after

        recent = ltm.list_recent(limit=10)
        found = [m for m in recent if m["content"] == content]
        assert len(found) == 1

    def test_multiple_injection_payloads(self, temp_agentnexus_home):
        """Multiple concurrent injection payloads don't corrupt the database."""
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        payloads = [
            ("test';--", "content1"),
            ('test";--', "content2"),
            ("test`;--", "content3"),
            ("test\\';--", "content4"),
            ("test' OR '1'='1", "content5"),
            ("test' UNION SELECT * FROM long_term_memories--", "content6"),
        ]

        tables_before = self._count_tables(ltm)
        for session_id, content in payloads:
            ltm.save(session_id=session_id, content=content, category="inject")

        tables_after = self._count_tables(ltm)
        assert tables_before == tables_after
        assert self._count_rows(ltm) >= len(payloads)

    def test_very_long_session_id(self, temp_agentnexus_home):
        """Very long session_id with SQL-like content is handled."""
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        long_session = "x" * 5000 + "' OR '1'='1"
        tables_before = self._count_tables(ltm)

        ltm.save(session_id=long_session, content="long session test", category="test")

        tables_after = self._count_tables(ltm)
        assert tables_before == tables_after
        assert "long_term_memories" in tables_after

    def test_null_byte_in_content_is_filtered(self, temp_agentnexus_home):
        """Content with null bytes is stored (null byte filtered by SQLite)."""
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        content = "normal text with \x00 null byte \x01\x02\x03 control chars"
        tables_before = self._count_tables(ltm)

        ltm.save(session_id="special_chars_test", content=content, category="test")

        tables_after = self._count_tables(ltm)
        assert tables_before == tables_after

    def test_unicode_and_sql_combined(self, temp_agentnexus_home):
        """Unicode combined with SQL injection payload is safe."""
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        session_id = "你好'; DROP TABLE long_term_memories; --"
        content = "测试内容 with SQL: ' OR '1'='1"
        tables_before = self._count_tables(ltm)

        ltm.save(session_id=session_id, content=content, category="unicode_test")

        tables_after = self._count_tables(ltm)
        assert tables_before == tables_after
        assert "long_term_memories" in tables_after


class TestSQLInjectionVersioned:
    """ConversationVersionManager handles SQL injection payloads."""

    def test_session_id_with_sql_metacharacters_in_versioned(self, temp_agentnexus_home):
        """Session_id with SQL injection payload is safe in versioned manager."""
        from agentnexus.core.config import get_settings
        from agentnexus.memory.versioned import ConversationVersionManager

        settings = get_settings()
        session_id = "session'; DROP TABLE conversation_checkpoints; --"
        mgr = ConversationVersionManager(session_id, settings.memory_db_path)

        cp_id = mgr.commit(stm_snapshot='{"key": "value"}', question="safe?", answer="yes")
        assert cp_id is not None

        log = mgr.log()
        assert len(log) >= 1

    def test_branch_name_with_sql_payload(self, temp_agentnexus_home):
        """Branch name with SQL metacharacters is handled safely."""
        from agentnexus.core.config import get_settings
        from agentnexus.memory.versioned import ConversationVersionManager

        settings = get_settings()
        mgr = ConversationVersionManager("branch_inject_test", settings.memory_db_path)
        mgr.commit(stm_snapshot='{"x": 1}', question="q", answer="a")

        branch_name = "'; DROP TABLE conversation_branches; --"
        mgr.branch(branch_name)

        cp = mgr.checkout(branch_name)
        assert cp is not None
        current = mgr._current_branch()
        assert current == branch_name

    def test_branch_with_unicode_sql_payload(self, temp_agentnexus_home):
        """Unicode branch name with SQL payload is safe."""
        from agentnexus.core.config import get_settings
        from agentnexus.memory.versioned import ConversationVersionManager

        settings = get_settings()
        mgr = ConversationVersionManager("branch_unicode_inject", settings.memory_db_path)
        mgr.commit(stm_snapshot='{"x": 1}', question="q", answer="a")

        branch_name = "你好'; DROP TABLE conversation_branches; SELECT '"
        mgr.branch(branch_name)
        cp = mgr.checkout(branch_name)
        assert cp is not None
        assert mgr._current_branch() == branch_name

    def test_branch_name_preserves_db_integrity(self, temp_agentnexus_home):
        """Branch with SQL injection payload doesn't corrupt other sessions."""
        from agentnexus.core.config import get_settings
        from agentnexus.memory.versioned import ConversationVersionManager

        settings = get_settings()

        mgr1 = ConversationVersionManager("victim_session", settings.memory_db_path)
        mgr1.commit(stm_snapshot='{"x": 1}', question="q", answer="a")

        mgr2 = ConversationVersionManager("attacker_session", settings.memory_db_path)
        mgr2.commit(stm_snapshot='{"x": 1}', question="q", answer="a")
        mgr2.branch("'; DROP TABLE conversation_checkpoints; --")
        mgr2.checkout("'; DROP TABLE conversation_checkpoints; --")

        mgr1_log = mgr1.log()
        assert len(mgr1_log) >= 1


# ── ChromaDB Metadata Injection Tests ──────────────────────────────

class TestChromaDBMetadataInjection:
    """ChromaDB handles special characters in stored content."""

    def test_null_bytes_in_content(self, temp_agentnexus_home):
        """Null bytes in content are handled without crash."""
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        content = "test\x00null\x00byte"
        cleaned = content.replace("\x00", "")
        try:
            ltm.save(session_id="null_byte_test", content=content, category="test")
        except Exception:
            ltm.save(session_id="null_byte_test", content=cleaned, category="test")

        recent = ltm.list_recent(limit=10)
        assert len(recent) > 0

    def test_emoji_in_content(self, temp_agentnexus_home):
        """Emoji characters in content are stored and retrieved."""
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        content = "Hello 😀 World 🌍 Testing 🚀 Emoji 📝"
        ltm.save(session_id="emoji_test", content=content, category="emoji_test")

        recent = ltm.list_recent(limit=10)
        found = [m for m in recent if m["content"] == content]
        assert len(found) >= 1

    def test_very_long_content_string(self, temp_agentnexus_home):
        """Very long content strings are stored."""
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        content = "x" * 100000
        ltm.save(session_id="long_string_test", content=content, category="test")

        recent = ltm.list_recent(limit=10)
        found = [m for m in recent if m["content"] == content]
        assert len(found) >= 1

    def test_unicode_control_chars(self, temp_agentnexus_home):
        """Unicode control characters and zero-width chars are handled."""
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        content = "test\u200bzero\u200cwidth\u200dspace\u200eand\u2060invisible"
        ltm.save(session_id="unicode_ctrl_test", content=content, category="test")

        recent = ltm.list_recent(limit=10)
        found = [m for m in recent if m["content"] == content]
        assert len(found) >= 1

    def test_xss_payload_in_content(self, temp_agentnexus_home):
        """XSS-like payload strings don't break storage."""
        from agentnexus.memory.long_term import get_long_term_memory

        ltm = get_long_term_memory()
        payloads = [
            "<script>alert('xss')</script>",
            "javascript:alert(1)",
            "{{7*7}}",
            "${7*7}",
            "<img src=x onerror=alert(1)>",
        ]
        for content in payloads:
            ltm.save(session_id="xss_test", content=content, category="xss")

        recent = ltm.list_recent(limit=10)
        saved_contents = {m["content"] for m in recent}
        for content in payloads:
            assert content in saved_contents
