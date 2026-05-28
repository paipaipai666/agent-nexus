from agentnexus.core.pii import _contains_pii, _mask_pii, contains_pii, mask_pii


class TestContainsPii:
    def test_email_detected(self):
        assert contains_pii("contact user@example.com for info") is True

    def test_phone_detected(self):
        assert contains_pii("call me at 13812345678") is True

    def test_api_key_detected(self):
        assert contains_pii("key: sk-abcdefghijklmnopqrstuvwxyz012345") is True

    def test_credit_card_detected(self):
        assert contains_pii("card 1234567890123456 is valid") is True

    def test_no_pii_returns_false(self):
        assert contains_pii("hello world no secrets here") is False

    def test_empty_string(self):
        assert contains_pii("") is False


class TestMaskPii:
    def test_email_masked(self):
        result = mask_pii("send to user@example.com please")
        assert "u" in result
        assert "***@***" in result
        assert ".com" in result
        assert "user@example.com" not in result

    def test_phone_masked(self):
        result = mask_pii("call 13812345678 now")
        assert "138****5678" in result
        assert "13812345678" not in result

    def test_api_key_masked(self):
        key = "sk-abcdefghijklmnopqrstuvwxyz012345"
        result = mask_pii(f"key={key}")
        assert "sk-" in result
        assert key not in result
        assert "****" in result or "..." in result

    def test_credit_card_masked(self):
        result = mask_pii("card 1234567890123456 end")
        assert "1234****3456" in result
        assert "1234567890123456" not in result

    def test_multiple_pii_masked(self):
        text = "email user@test.com phone 13812345678"
        result = mask_pii(text)
        assert "user@test.com" not in result
        assert "13812345678" not in result

    def test_no_pii_unchanged(self):
        text = "nothing sensitive here"
        assert mask_pii(text) == text

    def test_empty_string(self):
        assert mask_pii("") == ""


class TestBackwardCompatAliases:
    def test_contains_alias_same_as_original(self):
        assert _contains_pii is contains_pii

    def test_mask_alias_same_as_original(self):
        assert _mask_pii is mask_pii

    def test_alias_detects_email(self):
        assert _contains_pii("a@b.com") is True

    def test_alias_masks_phone(self):
        result = _mask_pii("13812345678")
        assert "138****5678" in result
