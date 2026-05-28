"""Security: PII handling across the system.

Tests that PII detection and masking work correctly, preserving structure
while hiding sensitive content.
"""

from agentnexus.core.pii import contains_pii, mask_pii


class TestMaskPII:
    """mask_pii partially masks PII while preserving structure."""

    def test_mask_email_preserves_structure(self):
        """Masked email keeps first char, domain TLD visible."""
        text = "Contact user@example.com for info"
        masked = mask_pii(text)
        assert "user@example.com" not in masked
        assert "@" in masked
        assert ".com" in masked

    def test_mask_phone_preserves_structure(self):
        """Masked phone keeps first 3 and last 4 digits."""
        text = "Call 13812345678 for details"
        masked = mask_pii(text)
        assert "13812345678" not in masked
        assert "138" in masked
        assert "5678" in masked
        assert "****" in masked

    def test_mask_api_key_preserves_prefix(self):
        """Masked API key keeps sk- prefix."""
        text = "Key: sk-abcdefghijklmnopqrstuvwxyz123456"
        masked = mask_pii(text)
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in masked
        assert "sk-" in masked

    def test_mask_credit_card_preserves_ends(self):
        """Masked credit card keeps first 4 and last 4 digits."""
        text = "Card: 1234567890123456"
        masked = mask_pii(text)
        assert "1234567890123456" not in masked
        assert "1234" in masked
        assert "3456" in masked

    def test_mask_pii_with_multiple_types(self):
        """Multiple PII types in one text are all masked."""
        text = "Email: admin@test.com Phone: 13900001111 Key: sk-abcd1234abcd1234abcd1234abcd1234"
        masked = mask_pii(text)
        assert "admin@test.com" not in masked
        assert "13900001111" not in masked
        assert "sk-abcd1234abcd1234abcd1234abcd1234" not in masked

    def test_mask_pii_idempotent(self):
        """Masking twice produces same result as masking once."""
        text = "Contact admin@example.com or call 13812345678"
        once = mask_pii(text)
        twice = mask_pii(once)
        assert once == twice

    def test_mask_pii_empty_string(self):
        """Empty string returns empty string."""
        assert mask_pii("") == ""

    def test_mask_pii_none_input(self):
        """None input returns None."""
        assert mask_pii(None) is None

    def test_mask_pii_no_pii(self):
        """Text without PII returns unchanged."""
        text = "This is a normal message without sensitive data"
        assert mask_pii(text) == text

    def test_mask_pii_preserves_surrounding_text(self):
        """Non-PII text around PII is preserved."""
        text = "Before email@test.com After"
        masked = mask_pii(text)
        assert masked.startswith("Before ")
        assert masked.endswith(" After")


class TestContainsPII:
    """contains_pii detects all PII types."""

    def test_detects_email(self):
        assert contains_pii("user@example.com") is True

    def test_detects_phone(self):
        assert contains_pii("13812345678") is True

    def test_detects_api_key(self):
        assert contains_pii("sk-abcdefghijklmnopqrstuvwxyz123456") is True

    def test_detects_credit_card(self):
        assert contains_pii("123456789012345") is True

    def test_no_false_positive_on_short_number(self):
        """Short numbers are not flagged as credit cards."""
        assert contains_pii("Order 12345") is False

    def test_no_pii_in_normal_text(self):
        assert contains_pii("Hello world, no secrets here") is False

    def test_empty_string(self):
        assert contains_pii("") is False

    def test_detects_multiple_pii_types(self):
        text = "admin@test.com and 13812345678"
        assert contains_pii(text) is True
