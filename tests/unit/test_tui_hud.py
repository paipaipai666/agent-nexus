"""Pure method tests for HUD widget and resolve_ctx_max helper."""

from unittest.mock import MagicMock, patch

from agentnexus.core.capabilities import resolve_ctx_max
from agentnexus.tui.widgets.hud import HUD


class TestResolveCtxMax:
    def test_exception_returns_none(self):
        """resolve_ctx_max returns None when no dynamic or registry info exists."""
        with patch("litellm.get_model_info", side_effect=Exception):
            assert resolve_ctx_max("any-model") is None

    def test_returns_max_input_tokens(self):
        """resolve_ctx_max returns the max_input_tokens value."""
        mock_info = MagicMock()
        mock_info.get.return_value = 128000
        with patch("litellm.get_model_info", return_value=mock_info):
            assert resolve_ctx_max("test-model") == 128000

    def test_registry_fallback_handles_deepseek_v4_flash(self):
        with patch("litellm.get_model_info", side_effect=Exception):
            assert resolve_ctx_max("deepseek-v4-flash", "https://api.deepseek.com") == 262144


class TestHudBuildText:
    """Test HUD._build_text() pure rendering logic."""

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=None)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_basic_build_text(self, mock_settings, mock_ctx):
        """Default state shows model name and unknown context when unresolved."""
        mock_settings.return_value.llm_model_id = "test/test-model"
        mock_settings.return_value.llm_base_url = ""
        hud = HUD()
        text = hud._build_text()
        assert "test-model" in text
        assert "ctx 0/[dim]?[/]" in text

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=None)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_with_thinking_and_strategy(self, mock_settings, mock_ctx):
        """Thinking indicator and strategy label appear when enabled."""
        mock_settings.return_value.llm_model_id = "simple-model"
        mock_settings.return_value.llm_base_url = ""
        hud = HUD()
        hud._supports_thinking = True
        hud._strategy = "JSON模式"
        text = hud._build_text()
        assert "JSON模式" in text
        assert "🧠" in text

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=None)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_compact_indicator(self, mock_settings, mock_ctx):
        """Compact indicator (⚙) appears when _compacting is True."""
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_base_url = ""
        hud = HUD()
        hud._compacting = True
        text = hud._build_text()
        assert "⚙" in text

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=128000)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_context_bar_when_ctx_max_known(self, mock_settings, mock_ctx):
        """Context bar with filled/empty blocks appears when ctx_max is set."""
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_base_url = ""
        hud = HUD()
        assert hud.ctx_max == 128000
        hud.current_tokens = 32000
        text = hud._build_text()
        assert "128k" in text
        assert "32k" in text
        assert "25%" in text
        assert "█" in text
        assert "░" in text

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=None)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_no_ctx_bar_when_ctx_max_unknown(self, mock_settings, mock_ctx):
        """No progress bar when ctx_max is None."""
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_base_url = ""
        hud = HUD()
        assert hud.ctx_max is None
        hud.current_tokens = 5000
        text = hud._build_text()
        assert "ctx 5.0k" in text
        assert "[dim]?[/]" in text
        assert "█" not in text
        assert "░" not in text

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=None)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_version_display_with_undo_redo(self, mock_settings, mock_ctx):
        """Version section shows undo/redo actions when available."""
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_base_url = ""
        hud = HUD()
        hud.update_version("abcdef123456", can_undo=True, can_redo=True)
        text = hud._build_text()
        assert "abcdef12" in text
        assert "undo" in text
        assert "redo" in text

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=None)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_token_display(self, mock_settings, mock_ctx):
        """Token totals shown as in:Xk out:Yk."""
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_base_url = ""
        hud = HUD()
        hud.total_input = 15000
        hud.total_output = 35000
        text = hud._build_text()
        assert "in:15k" in text
        assert "out:35k" in text

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=None)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_update_capabilities_sets_state(self, mock_settings, mock_ctx):
        """update_capabilities sets internal flags (refresh is a no-op here)."""
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_base_url = ""
        hud = HUD()
        hud.update_capabilities(supports_thinking=True, strategy="原生工具")
        assert hud._supports_thinking is True
        assert hud._strategy == "原生工具"

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=None)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_update_context_sets_state(self, mock_settings, mock_ctx):
        """update_context sets token values without crashing."""
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_base_url = ""
        hud = HUD()
        hud.update_context(current_tokens=100, total_input=200, total_output=300)
        assert hud.current_tokens == 100
        assert hud.total_input == 200
        assert hud.total_output == 300

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=None)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_set_compacting_sets_state(self, mock_settings, mock_ctx):
        """set_compacting toggles the _compacting flag."""
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_base_url = ""
        hud = HUD()
        hud.set_compacting(True)
        assert hud._compacting is True
        hud.set_compacting(False)
        assert hud._compacting is False

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=None)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_update_version_sets_state(self, mock_settings, mock_ctx):
        """update_version stores head/undo/redo state."""
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_base_url = ""
        hud = HUD()
        hud.update_version("deadbeef1234", can_undo=True, can_redo=False)
        assert hud._head == "deadbeef1234"
        assert hud._can_undo is True
        assert hud._can_redo is False

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=None)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_head_shortened_to_8_chars(self, mock_settings, mock_ctx):
        """Long HEAD is truncated to 8 characters in display."""
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_base_url = ""
        hud = HUD()
        hud._head = "abcdefghijklmnop"
        text = hud._build_text()
        assert "abcdefgh" in text
        assert "ijklmnop" not in text

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=None)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_head_dash_not_truncated(self, mock_settings, mock_ctx):
        """Default HEAD '---' is shown as-is, not truncated."""
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_base_url = ""
        hud = HUD()
        assert hud._head == "---"
        text = hud._build_text()
        assert "---" in text

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=10000)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_context_bar_saturation(self, mock_settings, mock_ctx):
        """Context bar shows full blocks when ctx_used >= ctx_max."""
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_base_url = ""
        hud = HUD()
        hud.current_tokens = 99999  # well beyond ctx_max
        text = hud._build_text()
        # Bar should be fully filled (10 blocks)
        assert "██████████" in text
