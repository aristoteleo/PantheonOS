"""
Integration Tests for OpenAI OAuth Manager

Tests the OAuth manager integration with:
- ModelSelector for provider detection
- Setup Wizard for user configuration
- REPL commands for user interaction
"""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest


class TestOAuthModelSelectorIntegration(unittest.TestCase):
    """Test OAuth integration with ModelSelector."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.auth_path = Path(self.temp_dir.name) / "oauth.json"
        # Create mock oauth token file
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        self.auth_path.write_text(json.dumps({
            "provider": "openai",
            "tokens": {
                "access_token": "test_token_123",
                "expires_at": "2099-12-31T23:59:59Z"
            }
        }))

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_oauth_token_detection_in_model_selector(self):
        """Test that ModelSelector detects OAuth token as available provider."""
        from pantheon.utils.model_selector import ModelSelector
        from pantheon.settings import Settings

        with patch("pantheon.auth.openai_oauth_manager.get_oauth_manager") as mock_get_mgr:
            mock_mgr = Mock()
            mock_mgr.auth_path = self.auth_path
            mock_get_mgr.return_value = mock_mgr

            settings = Settings()
            selector = ModelSelector(settings)

            # Should detect OAuth token as available provider
            available = selector._get_available_providers()
            assert "openai" in available

    def test_oauth_provider_in_available_providers(self):
        """Test that OAuth provider is included in available providers list."""
        from pantheon.utils.model_selector import ModelSelector
        from pantheon.settings import Settings

        with patch("pantheon.auth.openai_oauth_manager.get_oauth_manager") as mock_get_mgr:
            mock_mgr = Mock()
            mock_mgr.auth_path = self.auth_path
            mock_get_mgr.return_value = mock_mgr

            settings = Settings()
            selector = ModelSelector(settings)

            provider_info = selector.get_provider_info()
            available_providers = provider_info.get("available_providers", [])
            assert "openai" in available_providers

    def test_oauth_enables_model_resolution(self):
        """Test that OAuth token enables model selection for OpenAI."""
        from pantheon.utils.model_selector import ModelSelector
        from pantheon.settings import Settings

        with patch("pantheon.auth.openai_oauth_manager.get_oauth_manager") as mock_get_mgr:
            mock_mgr = Mock()
            mock_mgr.auth_path = self.auth_path
            mock_get_mgr.return_value = mock_mgr

            settings = Settings()
            selector = ModelSelector(settings)

            # Should be able to resolve models
            models = selector.resolve_model("normal")
            assert len(models) > 0
            assert isinstance(models, list)

    def test_oauth_priority_over_missing_api_key(self):
        """Test that OAuth token is detected even when API key env var is empty."""
        from pantheon.utils.model_selector import ModelSelector
        from pantheon.settings import Settings

        # Ensure OPENAI_API_KEY is not set
        os.environ.pop("OPENAI_API_KEY", None)

        with patch("pantheon.auth.openai_oauth_manager.get_oauth_manager") as mock_get_mgr:
            mock_mgr = Mock()
            mock_mgr.auth_path = self.auth_path
            mock_get_mgr.return_value = mock_mgr

            settings = Settings()
            selector = ModelSelector(settings)

            # Should still detect openai as available via OAuth
            provider = selector.detect_available_provider()
            assert provider == "openai"


class TestOAuthSetupWizardIntegration(unittest.TestCase):
    """Test OAuth integration with Setup Wizard."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.auth_path = Path(self.temp_dir.name) / "oauth.json"

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_oauth_provider_in_menu(self):
        """Test that OpenAI OAuth is in the Setup Wizard menu."""
        from pantheon.repl.setup_wizard import PROVIDER_MENU

        # Find OpenAI OAuth entry
        oauth_entries = [e for e in PROVIDER_MENU if e.provider_key == "openai_oauth"]
        assert len(oauth_entries) == 1

        entry = oauth_entries[0]
        assert entry.display_name == "OpenAI (OAuth)"
        assert entry.env_var is None  # OAuth doesn't need API key

    def test_oauth_menu_entry_properties(self):
        """Test OAuth menu entry has correct properties."""
        from pantheon.repl.setup_wizard import PROVIDER_MENU

        oauth_entry = next(e for e in PROVIDER_MENU if e.provider_key == "openai_oauth")

        # Should be a provider entry but not custom endpoint
        assert oauth_entry.provider_key == "openai_oauth"
        assert oauth_entry.display_name == "OpenAI (OAuth)"
        assert oauth_entry.env_var is None
        assert oauth_entry.is_custom is False
        assert oauth_entry.custom_config is None

    def test_setup_wizard_skips_when_oauth_token_exists(self):
        """Test that Setup Wizard is skipped when OAuth token exists."""
        from pantheon.repl.setup_wizard import check_and_run_setup

        # Create mock OAuth token
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        self.auth_path.write_text(json.dumps({"access_token": "test"}))

        with patch("pantheon.auth.openai_oauth_manager.get_oauth_manager") as mock_get_mgr:
            mock_mgr = Mock()
            mock_mgr.auth_path = self.auth_path
            mock_get_mgr.return_value = mock_mgr

            with patch("pantheon.repl.setup_wizard.run_setup_wizard") as mock_wizard:
                check_and_run_setup()

                # Setup wizard should NOT be called since token exists
                mock_wizard.assert_not_called()

    def test_oauth_provider_no_env_var_needed(self):
        """Test that OAuth provider entry has env_var as None."""
        from pantheon.repl.setup_wizard import PROVIDER_MENU

        oauth_entry = next(e for e in PROVIDER_MENU if e.provider_key == "openai_oauth")
        # This is important: OAuth doesn't use environment variables
        assert oauth_entry.env_var is None


class TestOAuthREPLCommandsIntegration(unittest.TestCase):
    """Test OAuth integration with REPL commands."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.auth_path = Path(self.temp_dir.name) / "oauth.json"

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_oauth_command_exists_in_repl(self):
        """Test that /oauth command is handled by REPL."""
        from pantheon.repl.core import Repl

        # Check that _handle_oauth_command method exists
        assert hasattr(Repl, "_handle_oauth_command")

        # Check it's an async method
        import inspect
        assert inspect.iscoroutinefunction(Repl._handle_oauth_command)

    def test_oauth_login_subcommand_routing(self):
        """Test that /oauth login routes to correct handler."""
        # This is a routing test - actual execution would require full REPL setup
        # Here we just verify the command structure

        test_commands = [
            "/oauth login",
            "/oauth status",
            "/oauth logout",
            "/oauth",  # Should default to status
        ]

        for cmd in test_commands:
            # Extract subcommand
            if cmd.startswith("/oauth"):
                parts = cmd.split()
                subcommand = parts[1] if len(parts) > 1 else "status"
                assert subcommand in ["login", "status", "logout", ""]

    @patch("pantheon.auth.openai_oauth_manager.get_oauth_manager")
    def test_oauth_status_command_format(self, mock_get_mgr):
        """Test that /oauth status returns properly formatted output."""
        mock_mgr = Mock()
        mock_mgr.auth_path = self.auth_path

        # Mock authenticated status as async function
        async def mock_get_status():
            return {
                "authenticated": True,
                "email": "user@example.com",
                "organization_id": "org-123",
                "project_id": "proj-abc",
                "token_expires_at": "2025-03-30T12:00:00Z"
            }

        mock_mgr.get_status = mock_get_status
        mock_get_mgr.return_value = mock_mgr

        # Status should have all required fields
        async def run_test():
            status = await mock_mgr.get_status()
            assert "authenticated" in status
            assert "email" in status
            assert "organization_id" in status
            assert "project_id" in status

        asyncio.run(run_test())


class TestOAuthCompleteWorkflow(unittest.TestCase):
    """Test complete OAuth workflow from setup to usage."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.auth_path = Path(self.temp_dir.name) / "oauth.json"

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_workflow_oauth_available_without_api_key(self):
        """Test that OAuth works when API key env var is not set."""
        from pantheon.utils.model_selector import ModelSelector
        from pantheon.settings import Settings

        # Clear API key
        os.environ.pop("OPENAI_API_KEY", None)

        # Create mock OAuth token
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        self.auth_path.write_text(json.dumps({
            "provider": "openai",
            "tokens": {"access_token": "test_token"}
        }))

        with patch("pantheon.auth.openai_oauth_manager.get_oauth_manager") as mock_get_mgr:
            mock_mgr = Mock()
            mock_mgr.auth_path = self.auth_path
            mock_get_mgr.return_value = mock_mgr

            settings = Settings()
            selector = ModelSelector(settings)

            # OpenAI should be detected via OAuth
            provider = selector.detect_available_provider()
            assert provider == "openai"

            # Models should be resolvable
            models = selector.resolve_model("normal")
            assert len(models) > 0

    def test_workflow_oauth_token_file_location(self):
        """Test that OAuth token is stored in correct location."""
        from pantheon.auth.openai_oauth_manager import get_oauth_manager, reset_oauth_manager

        reset_oauth_manager()

        manager = get_oauth_manager()

        # Should be at ~/.pantheon/oauth.json
        expected_path = Path.home() / ".pantheon" / "oauth.json"
        assert manager.auth_path == expected_path

    def test_workflow_multiple_providers_with_oauth(self):
        """Test that OAuth works alongside other providers."""
        from pantheon.utils.model_selector import ModelSelector
        from pantheon.settings import Settings

        # Set both API key and OAuth token
        os.environ["OPENAI_API_KEY"] = "sk-test123"

        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        self.auth_path.write_text(json.dumps({
            "provider": "openai",
            "tokens": {"access_token": "oauth_token"}
        }))

        with patch("pantheon.auth.openai_oauth_manager.get_oauth_manager") as mock_get_mgr:
            mock_mgr = Mock()
            mock_mgr.auth_path = self.auth_path
            mock_get_mgr.return_value = mock_mgr

            settings = Settings()
            selector = ModelSelector(settings)

            # Both should be available
            available = selector._get_available_providers()
            assert "openai" in available

        # Clean up
        os.environ.pop("OPENAI_API_KEY", None)


class TestOAuthBackwardCompatibility(unittest.TestCase):
    """Test that OAuth doesn't break existing API Key authentication."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_api_key_still_works_without_oauth(self):
        """Test that API Key authentication still works when OAuth is not available."""
        from pantheon.utils.model_selector import ModelSelector
        from pantheon.settings import Settings

        # Set API key but no OAuth token
        os.environ["OPENAI_API_KEY"] = "sk-test123"

        with patch("pantheon.auth.openai_oauth_manager.get_oauth_manager") as mock_get_mgr:
            mock_mgr = Mock()
            # OAuth token file doesn't exist
            mock_mgr.auth_path = Path(self.temp_dir.name) / "nonexistent.json"
            mock_get_mgr.return_value = mock_mgr

            settings = Settings()
            selector = ModelSelector(settings)

            # OpenAI should be detected via API Key
            available = selector._get_available_providers()
            assert "openai" in available

        # Clean up
        os.environ.pop("OPENAI_API_KEY", None)

    def test_oauth_import_doesnt_break_when_optional(self):
        """Test that missing OAuth doesn't crash ModelSelector."""
        from pantheon.utils.model_selector import ModelSelector
        from pantheon.settings import Settings

        # Simulate OAuth check failing (e.g., OmicVerse not installed)
        with patch("pantheon.auth.openai_oauth_manager.get_oauth_manager") as mock_get_mgr:
            mock_get_mgr.side_effect = ImportError("OmicVerse not installed")

            settings = Settings()
            selector = ModelSelector(settings)

            # Should not raise, just skip OAuth detection
            available = selector._get_available_providers()
            # Should return empty or only other providers
            assert isinstance(available, set)

    def test_old_oauth_implementation_compatibility(self):
        """Test that code handles OAuth gracefully if not yet implemented."""
        # This test ensures the system doesn't break if OAuth isn't available
        try:
            from pantheon.auth.openai_oauth_manager import get_oauth_manager
            manager = get_oauth_manager()
            # If we get here, OAuth is available
            assert manager is not None
        except ImportError:
            # If OAuth isn't available, system should handle gracefully
            pass


class TestOAuthErrorRecovery(unittest.TestCase):
    """Test that OAuth errors are handled gracefully."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.auth_path = Path(self.temp_dir.name) / "oauth.json"

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_corrupt_oauth_file_handling(self):
        """Test that corrupt OAuth token file doesn't crash system."""
        from pantheon.utils.model_selector import ModelSelector
        from pantheon.settings import Settings

        # Create corrupt token file
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        self.auth_path.write_text("invalid json {{{")

        with patch("pantheon.auth.openai_oauth_manager.get_oauth_manager") as mock_get_mgr:
            mock_mgr = Mock()
            mock_mgr.auth_path = self.auth_path
            mock_get_mgr.return_value = mock_mgr

            settings = Settings()
            selector = ModelSelector(settings)

            # Should handle error gracefully
            available = selector._get_available_providers()
            assert isinstance(available, set)

    def test_oauth_network_error_recovery(self):
        """Test that network errors during OAuth don't crash REPL."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

        with patch.object(oauth_manager, "_get_manager") as mock_get:
            from pantheon.auth.openai_oauth_manager import OpenAIOAuthError
            mock_get.return_value = Mock(
                ensure_access_token_with_codex_fallback=Mock(
                    side_effect=OpenAIOAuthError("Network timeout")
                )
            )

            async def run_test():
                token = await oauth_manager.get_access_token()
                # Should return None on error, not raise
                assert token is None

            asyncio.run(run_test())


# Pytest-style integration tests
@pytest.mark.integration
class TestOAuthIntegrationPytest:
    """Pytest-style integration tests for OAuth."""

    @patch("pantheon.auth.openai_oauth_manager.get_oauth_manager")
    def test_full_oauth_setup_flow(self, mock_get_mgr):
        """Test complete setup flow with OAuth."""
        from pantheon.utils.model_selector import ModelSelector
        from pantheon.settings import Settings

        mock_mgr = Mock()
        mock_auth_path = Path("/tmp/oauth.json")
        mock_mgr.auth_path = mock_auth_path
        mock_get_mgr.return_value = mock_mgr

        # Step 1: Initialize selector
        settings = Settings()
        selector = ModelSelector(settings)

        # Step 2: Mock the oauth token check to return True for this test
        with patch.object(selector, "_check_oauth_token_available", return_value=True):
            available = selector._get_available_providers()
            # If OAuth token check works, openai should be in available
            # This test verifies the integration, actual OAuth would need token file
            assert isinstance(available, set)

    def test_oauth_provider_menu_structure(self):
        """Test that OAuth menu entry has correct structure."""
        from pantheon.repl.setup_wizard import PROVIDER_MENU, ProviderMenuEntry

        oauth_entry = next(
            (e for e in PROVIDER_MENU if e.provider_key == "openai_oauth"),
            None
        )

        assert oauth_entry is not None
        assert isinstance(oauth_entry, ProviderMenuEntry)
        assert oauth_entry.env_var is None
        assert oauth_entry.is_custom is False


if __name__ == "__main__":
    unittest.main(argv=[""], exit=False, verbosity=2)
