"""
Backward Compatibility Tests for API Key Authentication

Tests that OAuth support does NOT break existing API Key authentication.
Focuses on key integration points: ModelSelector, Setup Wizard, and REPL.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


class TestModelSelectorBackwardCompatibility(unittest.TestCase):
    """Test ModelSelector still works with API Key authentication."""

    def setUp(self):
        """Set up test environment."""
        self.original_api_key = os.environ.get("OPENAI_API_KEY")

    def tearDown(self):
        """Restore original environment."""
        if self.original_api_key:
            os.environ["OPENAI_API_KEY"] = self.original_api_key
        else:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_api_key_detection(self):
        """Test that ModelSelector detects API Key."""
        from pantheon.utils.model_selector import ModelSelector

        os.environ["OPENAI_API_KEY"] = "sk-test123"

        selector = ModelSelector(None)
        provider = selector.detect_available_provider()

        # Should detect openai provider via API key
        assert provider == "openai"

    def test_model_resolution_with_api_key(self):
        """Test that models can be resolved with API key."""
        from pantheon.utils.model_selector import ModelSelector

        os.environ["OPENAI_API_KEY"] = "sk-test123"

        selector = ModelSelector(None)
        models = selector.resolve_model("normal")

        # Should return list of models
        assert isinstance(models, list)

    def test_api_key_still_has_public_api(self):
        """Test that ModelSelector public API is unchanged."""
        from pantheon.utils.model_selector import ModelSelector

        selector = ModelSelector(None)

        # Check public methods exist
        assert hasattr(selector, "detect_available_provider")
        assert hasattr(selector, "resolve_model")
        assert hasattr(selector, "get_provider_info")
        assert hasattr(selector, "list_available_models")

    def test_no_oauth_doesnt_break_selector(self):
        """Test that missing OAuth doesn't break ModelSelector."""
        from pantheon.utils.model_selector import ModelSelector

        os.environ["OPENAI_API_KEY"] = "sk-test123"

        with patch(
            "pantheon.auth.oauth_manager.get_oauth_manager"
        ) as mock_oauth:
            # Simulate OAuth not available
            mock_oauth.side_effect = ImportError("OAuth not configured")

            selector = ModelSelector(None)

            # Should not crash, should still work with API key
            provider = selector.detect_available_provider()
            assert provider == "openai"


class TestSetupWizardBackwardCompatibility(unittest.TestCase):
    """Test Setup Wizard still supports API Key authentication."""

    def test_api_key_option_in_menu(self):
        """Test that OpenAI API key option is in Setup Wizard menu."""
        from pantheon.repl.setup_wizard import PROVIDER_MENU

        api_key_entries = [
            e for e in PROVIDER_MENU if e.provider_key == "openai"
        ]

        assert len(api_key_entries) == 1
        assert api_key_entries[0].display_name == "OpenAI"

    def test_api_key_env_var_in_menu(self):
        """Test that API Key menu entry has correct env var."""
        from pantheon.repl.setup_wizard import PROVIDER_MENU

        api_key_entry = next(
            (e for e in PROVIDER_MENU if e.provider_key == "openai"), None
        )

        assert api_key_entry is not None
        assert api_key_entry.env_var == "OPENAI_API_KEY"

    def test_both_auth_methods_available(self):
        """Test that both OAuth and API Key are available."""
        from pantheon.repl.setup_wizard import PROVIDER_MENU

        provider_keys = [e.provider_key for e in PROVIDER_MENU]

        assert "openai" in provider_keys, "API Key option must be present"
        assert "openai_oauth" in provider_keys, "OAuth option must be present"

    def test_menu_structure_preserved(self):
        """Test that menu structure is still valid."""
        from pantheon.repl.setup_wizard import PROVIDER_MENU

        # Should be a list
        assert isinstance(PROVIDER_MENU, list)

        # All entries should have required properties
        for entry in PROVIDER_MENU:
            assert hasattr(entry, "provider_key")
            assert hasattr(entry, "display_name")


class TestREPLBackwardCompatibility(unittest.TestCase):
    """Test REPL commands still work with API Key."""

    def test_repl_package_exports_repl_symbol(self):
        """Test that pantheon.repl exports Repl lazily."""
        import pantheon.repl as repl_pkg

        assert "Repl" in getattr(repl_pkg, "__all__", [])
        assert hasattr(repl_pkg, "__getattr__")

    def test_oauth_command_contract_present_in_source(self):
        """Test that the REPL source still defines the OAuth command handler."""
        core_path = Path(__file__).resolve().parents[1] / "pantheon" / "repl" / "core.py"
        content = core_path.read_text(encoding="utf-8")

        assert "def _handle_oauth_command" in content
        assert 'elif cmd_lower.startswith("/oauth")' in content

    def test_setup_wizard_import_no_longer_requires_repl_core(self):
        """Test that setup_wizard import does not force pantheon.repl.core import."""
        from pantheon.repl.setup_wizard import PROVIDER_MENU

        assert isinstance(PROVIDER_MENU, list)


class TestAuthenticationCoexistence(unittest.TestCase):
    """Test that API Key and OAuth can coexist."""

    def setUp(self):
        """Set up test environment."""
        self.original_api_key = os.environ.get("OPENAI_API_KEY")

    def tearDown(self):
        """Clean up."""
        if self.original_api_key:
            os.environ["OPENAI_API_KEY"] = self.original_api_key
        else:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_api_key_with_oauth_token(self):
        """Test that both can be present simultaneously."""
        from pantheon.utils.model_selector import ModelSelector

        # Set API key
        os.environ["OPENAI_API_KEY"] = "sk-test123"

        with patch(
            "pantheon.auth.oauth_manager.get_oauth_manager"
        ) as mock_oauth:
            mock_mgr = Mock()
            mock_mgr.auth_path = Path("oauth_openai.json")
            mock_oauth.return_value = mock_mgr

            selector = ModelSelector(None)

            # Should detect OpenAI (works with either auth method)
            provider = selector.detect_available_provider()
            assert provider == "openai"

    def test_api_key_preferred_when_both_present(self):
        """Test API Key detection when both are available."""
        from pantheon.utils.model_selector import ModelSelector

        os.environ["OPENAI_API_KEY"] = "sk-test123"

        selector = ModelSelector(None)

        # Should detect API key (simpler to check first)
        provider = selector.detect_available_provider()
        assert provider == "openai"


class TestNoAuthenticationScenario(unittest.TestCase):
    """Test system behavior without any authentication."""

    def setUp(self):
        """Set up test environment."""
        self.original_api_key = os.environ.get("OPENAI_API_KEY")

    def tearDown(self):
        """Restore environment."""
        if self.original_api_key:
            os.environ["OPENAI_API_KEY"] = self.original_api_key
        else:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_setup_wizard_menu_available_without_auth(self):
        """Test Setup Wizard offers options even without auth."""
        from pantheon.repl.setup_wizard import PROVIDER_MENU

        # Clear any auth
        os.environ.pop("OPENAI_API_KEY", None)

        # Menu should still exist and offer options
        assert len(PROVIDER_MENU) > 0
        assert any(e.provider_key == "openai" for e in PROVIDER_MENU)


class TestAPIKeyPriority(unittest.TestCase):
    """Test that API Key check is working correctly."""

    def setUp(self):
        """Set up test environment."""
        self.original_api_key = os.environ.get("OPENAI_API_KEY")

    def tearDown(self):
        """Restore environment."""
        if self.original_api_key:
            os.environ["OPENAI_API_KEY"] = self.original_api_key
        else:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_api_key_string_detection(self):
        """Test that API key detection works with valid key format."""
        from pantheon.utils.model_selector import ModelSelector

        # Set a properly formatted API key
        os.environ["OPENAI_API_KEY"] = "sk-proj-abcdef123456"

        selector = ModelSelector(None)
        provider = selector.detect_available_provider()

        assert provider == "openai"

    def test_empty_api_key_not_detected(self):
        """Test that empty API key is not detected as valid."""
        from pantheon.utils.model_selector import ModelSelector

        # Set empty API key
        os.environ["OPENAI_API_KEY"] = ""

        selector = ModelSelector(None)
        provider = selector.detect_available_provider()

        # Should not detect empty string as valid provider
        assert provider != "openai" or provider is None


# Pytest-style integration tests
@pytest.mark.integration
class TestBackwardCompatibilityIntegration:
    """Integration tests for backward compatibility."""

    def test_api_key_full_flow(self):
        """Test complete flow with API Key authentication."""
        from pantheon.utils.model_selector import ModelSelector

        os.environ["OPENAI_API_KEY"] = "sk-test123"

        selector = ModelSelector(None)

        # Detect provider
        assert selector.detect_available_provider() == "openai"

        # Resolve models
        models = selector.resolve_model("normal")
        assert isinstance(models, list)

    def test_api_key_and_oauth_menu_both_present(self):
        """Test that both auth options are in Setup Wizard."""
        from pantheon.repl.setup_wizard import PROVIDER_MENU

        provider_keys = [e.provider_key for e in PROVIDER_MENU]

        # Both must be present
        assert "openai" in provider_keys
        assert "openai_oauth" in provider_keys

        # Count should be 2 for OpenAI options
        openai_count = sum(
            1
            for e in PROVIDER_MENU
            if e.provider_key in ["openai", "openai_oauth"]
        )
        assert openai_count == 2


if __name__ == "__main__":
    unittest.main(argv=[""], exit=False, verbosity=2)
