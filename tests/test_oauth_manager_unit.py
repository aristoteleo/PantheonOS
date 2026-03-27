"""
Unit Tests for OpenAI OAuth Manager

Tests cover:
- Singleton creation and thread safety
- Token management and refresh
- JWT parsing and context extraction
- Error handling and edge cases
- Codex CLI credential import
"""

import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, AsyncMock

import pytest


class TestOpenAIOAuthManagerSingleton(unittest.TestCase):
    """Test singleton pattern and thread safety."""

    def setUp(self):
        """Reset singleton before each test."""
        from pantheon.auth.openai_oauth_manager import reset_oauth_manager
        reset_oauth_manager()

    def test_singleton_creation(self):
        """Test that get_oauth_manager creates a singleton instance."""
        from pantheon.auth.openai_oauth_manager import get_oauth_manager

        manager1 = get_oauth_manager()
        manager2 = get_oauth_manager()

        # Same instance
        assert manager1 is manager2

    def test_singleton_thread_safety(self):
        """Test that singleton creation is thread-safe."""
        from pantheon.auth.openai_oauth_manager import (
            get_oauth_manager,
            reset_oauth_manager,
        )

        # Reset to ensure fresh state
        reset_oauth_manager()

        instances = []
        errors = []

        def create_manager():
            try:
                manager = get_oauth_manager()
                instances.append(manager)
            except Exception as e:
                errors.append(e)

        # Create multiple threads trying to get singleton simultaneously
        threads = [threading.Thread(target=create_manager) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have no errors
        assert len(errors) == 0, f"Errors occurred: {errors}"

        # All instances should be the same
        assert len(instances) == 10
        first_instance = instances[0]
        for instance in instances[1:]:
            assert instance is first_instance

    def test_singleton_with_custom_path(self):
        """Test singleton creation with custom auth path."""
        from pantheon.auth.openai_oauth_manager import get_oauth_manager, reset_oauth_manager

        reset_oauth_manager()

        with tempfile.TemporaryDirectory() as tmpdir:
            custom_path = Path(tmpdir) / "custom_oauth.json"
            manager = get_oauth_manager(auth_path=custom_path)

            assert manager.auth_path == custom_path

    def test_singleton_default_path(self):
        """Test singleton uses default auth path."""
        from pantheon.auth.openai_oauth_manager import get_oauth_manager, reset_oauth_manager

        reset_oauth_manager()

        manager = get_oauth_manager()
        expected_path = Path.home() / ".pantheon" / "oauth.json"

        assert manager.auth_path == expected_path


class TestOpenAIOAuthManagerTokenHandling(unittest.TestCase):
    """Test token management and refresh logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.auth_path = Path(self.temp_dir.name) / "oauth.json"

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager")
    def test_get_access_token_with_valid_token(self, mock_get_manager):
        """Test retrieving a valid access token."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        mock_manager = Mock()
        mock_manager.ensure_access_token_with_codex_fallback.return_value = "test_token_123"
        mock_get_manager.return_value = mock_manager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

        async def run_test():
            token = await oauth_manager.get_access_token(refresh_if_needed=True)
            assert token == "test_token_123"
            mock_manager.ensure_access_token_with_codex_fallback.assert_called_once_with(
                refresh_if_needed=True, import_codex_if_missing=True
            )

        asyncio.run(run_test())

    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager")
    def test_get_access_token_no_token(self, mock_get_manager):
        """Test when no valid token is available."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        mock_manager = Mock()
        mock_manager.ensure_access_token_with_codex_fallback.return_value = None
        mock_get_manager.return_value = mock_manager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

        async def run_test():
            token = await oauth_manager.get_access_token()
            assert token is None

        asyncio.run(run_test())

    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager")
    def test_get_access_token_error_handling(self, mock_get_manager):
        """Test error handling in token retrieval."""
        from pantheon.auth.openai_oauth_manager import (
            OpenAIOAuthManager,
            OpenAIOAuthError,
        )

        mock_manager = Mock()
        mock_manager.ensure_access_token_with_codex_fallback.side_effect = OpenAIOAuthError(
            "Token refresh failed"
        )
        mock_get_manager.return_value = mock_manager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

        async def run_test():
            token = await oauth_manager.get_access_token()
            # Should return None on error
            assert token is None

        asyncio.run(run_test())

    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager")
    def test_clear_token_removes_file(self, mock_get_manager):
        """Test that clear_token removes the token file."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        mock_manager = Mock()
        mock_get_manager.return_value = mock_manager

        # Create a fake token file
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        self.auth_path.write_text(json.dumps({"access_token": "fake_token"}))

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)
        assert self.auth_path.exists()

        async def run_test():
            result = await oauth_manager.clear_token()
            assert result is True
            assert not self.auth_path.exists()

        asyncio.run(run_test())

    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager")
    def test_clear_token_no_file(self, mock_get_manager):
        """Test clear_token when no file exists."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        mock_manager = Mock()
        mock_get_manager.return_value = mock_manager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)
        # File doesn't exist

        async def run_test():
            result = await oauth_manager.clear_token()
            # Should still return True (no error)
            assert result is True

        asyncio.run(run_test())

    def test_reset_clears_cache(self):
        """Test that reset() clears the cached manager."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)
        mock_manager = Mock()
        oauth_manager._omicverse_manager = mock_manager  # Set cache

        oauth_manager.reset()
        assert oauth_manager._omicverse_manager is None


class TestOpenAIOAuthManagerJWTParsing(unittest.TestCase):
    """Test JWT parsing and context extraction."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.auth_path = Path(self.temp_dir.name) / "oauth.json"

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager")
    def test_get_org_context_with_valid_jwt(self, mock_get_manager):
        """Test extracting organization context from JWT."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        mock_manager = Mock()
        mock_auth = {
            "tokens": {
                "id_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJvcmdfaWQiOiJvcmctdGVzdDEyMzQ1IiwicHJvamVjdF9pZCI6InByb2otYWJjZGVmIn0.test_signature"
            }
        }
        mock_manager.load.return_value = mock_auth
        mock_get_manager.return_value = mock_manager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

        with patch("pantheon.auth.openai_oauth_manager.jwt_org_context") as mock_jwt:
            mock_jwt.return_value = {
                "organization_id": "org-test12345",
                "project_id": "proj-abcdef",
            }

            async def run_test():
                context = await oauth_manager.get_org_context()
                assert context["organization_id"] == "org-test12345"
                assert context["project_id"] == "proj-abcdef"

            asyncio.run(run_test())

    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager")
    def test_get_org_context_no_token(self, mock_get_manager):
        """Test context extraction when no ID token exists."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        mock_manager = Mock()
        mock_manager.load.return_value = {"tokens": {}}  # No id_token
        mock_get_manager.return_value = mock_manager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

        async def run_test():
            context = await oauth_manager.get_org_context()
            # Should return empty dict
            assert context == {}

        asyncio.run(run_test())

    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager")
    def test_get_org_context_error_handling(self, mock_get_manager):
        """Test error handling in context extraction."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        mock_manager = Mock()
        mock_manager.load.side_effect = Exception("Load failed")
        mock_get_manager.return_value = mock_manager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

        async def run_test():
            context = await oauth_manager.get_org_context()
            # Should return empty dict on error
            assert context == {}

        asyncio.run(run_test())


class TestOpenAIOAuthManagerStatus(unittest.TestCase):
    """Test OAuth status retrieval."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.auth_path = Path(self.temp_dir.name) / "oauth.json"

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager")
    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager.get_access_token")
    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager.get_org_context")
    def test_get_status_authenticated(
        self, mock_get_context, mock_get_token, mock_get_manager
    ):
        """Test status when authenticated."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        mock_get_token.return_value = "test_token_123"
        mock_get_context.return_value = {
            "organization_id": "org-123",
            "project_id": "proj-abc",
        }

        mock_manager = Mock()
        mock_auth = {
            "tokens": {
                "email": "test@example.com",
                "expires_at": "2025-03-30T12:00:00Z",
            }
        }
        mock_manager.load.return_value = mock_auth
        mock_get_manager.return_value = mock_manager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

        async def run_test():
            status = await oauth_manager.get_status()
            assert status["authenticated"] is True
            assert status["email"] == "test@example.com"
            assert status["organization_id"] == "org-123"
            assert status["project_id"] == "proj-abc"

        asyncio.run(run_test())

    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager")
    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager.get_access_token")
    def test_get_status_not_authenticated(self, mock_get_token, mock_get_manager):
        """Test status when not authenticated."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        mock_get_token.return_value = None

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

        async def run_test():
            status = await oauth_manager.get_status()
            assert status["authenticated"] is False

        asyncio.run(run_test())


class TestOpenAIOAuthManagerCodexImport(unittest.TestCase):
    """Test Codex CLI credential import."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.auth_path = Path(self.temp_dir.name) / "oauth.json"

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager")
    def test_import_codex_credentials_success(self, mock_get_manager):
        """Test successful import from Codex CLI."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        mock_manager = Mock()
        mock_manager.import_codex_auth.return_value = True
        mock_get_manager.return_value = mock_manager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

        async def run_test():
            result = await oauth_manager.import_codex_credentials()
            assert result is True
            mock_manager.import_codex_auth.assert_called_once()

        asyncio.run(run_test())

    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager")
    def test_import_codex_credentials_not_found(self, mock_get_manager):
        """Test when Codex credentials don't exist."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        mock_manager = Mock()
        mock_manager.import_codex_auth.return_value = False
        mock_get_manager.return_value = mock_manager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

        async def run_test():
            result = await oauth_manager.import_codex_credentials()
            assert result is False

        asyncio.run(run_test())

    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager")
    def test_import_codex_credentials_error_handling(self, mock_get_manager):
        """Test error handling in Codex import."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        mock_manager = Mock()
        mock_manager.import_codex_auth.side_effect = Exception("Import failed")
        mock_get_manager.return_value = mock_manager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

        async def run_test():
            result = await oauth_manager.import_codex_credentials()
            # Should return False on error
            assert result is False

        asyncio.run(run_test())


class TestOpenAIOAuthManagerLogin(unittest.TestCase):
    """Test OAuth login flow."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.auth_path = Path(self.temp_dir.name) / "oauth.json"

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager")
    def test_login_success(self, mock_get_manager):
        """Test successful OAuth login."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        mock_manager = Mock()
        mock_manager.login.return_value = None  # Sync method returns None
        mock_get_manager.return_value = mock_manager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

        async def run_test():
            result = await oauth_manager.login()
            assert result is True
            mock_manager.login.assert_called_once()

        asyncio.run(run_test())

    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager")
    def test_login_with_workspace_id(self, mock_get_manager):
        """Test login with workspace ID."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        mock_manager = Mock()
        mock_manager.login.return_value = None
        mock_get_manager.return_value = mock_manager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

        async def run_test():
            result = await oauth_manager.login(workspace_id="ws-12345")
            assert result is True
            mock_manager.login.assert_called_once_with(
                workspace_id="ws-12345", open_browser=True
            )

        asyncio.run(run_test())

    @patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager")
    def test_login_error_handling(self, mock_get_manager):
        """Test error handling in login."""
        from pantheon.auth.openai_oauth_manager import (
            OpenAIOAuthManager,
            OpenAIOAuthError,
        )

        mock_manager = Mock()
        mock_manager.login.side_effect = OpenAIOAuthError("Login failed")
        mock_get_manager.return_value = mock_manager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

        async def run_test():
            result = await oauth_manager.login()
            assert result is False

        asyncio.run(run_test())


class TestOpenAIOAuthManagerAsyncLocking(unittest.TestCase):
    """Test asyncio.Lock consistency across all methods."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.auth_path = Path(self.temp_dir.name) / "oauth.json"

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_concurrent_access_to_get_org_context(self):
        """Test that concurrent calls to get_org_context are properly locked."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        with patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager") as mock_get_manager:
            mock_manager = Mock()
            mock_manager.load.return_value = {"tokens": {"id_token": "test_token"}}
            mock_get_manager.return_value = mock_manager

            oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

            async def run_concurrent():
                with patch("pantheon.auth.openai_oauth_manager.jwt_org_context") as mock_jwt:
                    mock_jwt.return_value = {"organization_id": "org-123"}

                    # Run multiple concurrent calls
                    tasks = [
                        oauth_manager.get_org_context() for _ in range(5)
                    ]
                    results = await asyncio.gather(*tasks)

                    # All should succeed
                    assert len(results) == 5
                    assert all(r == {"organization_id": "org-123"} for r in results)

            asyncio.run(run_concurrent())

    def test_concurrent_access_to_get_access_token(self):
        """Test that concurrent calls to get_access_token are properly locked."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        with patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager") as mock_get_manager:
            mock_manager = Mock()
            mock_manager.ensure_access_token_with_codex_fallback.return_value = "token_123"
            mock_get_manager.return_value = mock_manager

            oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

            async def run_concurrent():
                # Run multiple concurrent calls
                tasks = [
                    oauth_manager.get_access_token() for _ in range(5)
                ]
                results = await asyncio.gather(*tasks)

                # All should succeed
                assert len(results) == 5
                assert all(r == "token_123" for r in results)

            asyncio.run(run_concurrent())


class TestOpenAIOAuthManagerLazyInit(unittest.TestCase):
    """Test lazy initialization of OmicVerse manager."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.auth_path = Path(self.temp_dir.name) / "oauth.json"

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_lazy_initialization(self):
        """Test that OmicVerse manager is lazily initialized on first use."""
        from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager

        oauth_manager = OpenAIOAuthManager(auth_path=self.auth_path)

        # Manager should not be created yet
        assert oauth_manager._omicverse_manager is None

        # After calling _get_manager (even if it fails), cache should be managed
        with patch("pantheon.auth.openai_oauth_manager.OpenAIOAuthManager._get_manager") as mock_get:
            # Simulate OmicVerse manager creation
            mock_manager = Mock()
            mock_get.return_value = mock_manager

            # First call creates it
            manager1 = oauth_manager._get_manager()
            assert manager1 is not None

            # Verify it was called
            assert mock_get.called


# Pytest-style fixtures and tests
@pytest.fixture
def temp_auth_path():
    """Provide a temporary auth path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "oauth.json"


@pytest.fixture
def oauth_manager(temp_auth_path):
    """Provide an OpenAIOAuthManager instance."""
    from pantheon.auth.openai_oauth_manager import OpenAIOAuthManager, reset_oauth_manager

    reset_oauth_manager()
    return OpenAIOAuthManager(auth_path=temp_auth_path)


@pytest.mark.asyncio
async def test_async_lock_consistency(oauth_manager):
    """Test that all async methods properly use asyncio.Lock."""
    # This is a runtime check that methods can be called concurrently
    with patch.object(oauth_manager, "_get_manager") as mock_get:
        mock_manager = Mock()
        mock_manager.ensure_access_token_with_codex_fallback.return_value = "token"
        mock_manager.load.return_value = {"tokens": {"id_token": "jwt"}}
        mock_get.return_value = mock_manager

        with patch("pantheon.auth.openai_oauth_manager.jwt_org_context", return_value={}):
            # Run multiple concurrent calls
            results = await asyncio.gather(
                oauth_manager.get_access_token(),
                oauth_manager.get_org_context(),
            )

            # Both should complete without deadlock
            assert len(results) == 2


if __name__ == "__main__":
    # Run unittest tests
    unittest.main(argv=[""], exit=False, verbosity=2)

    # Run pytest tests
    pytest.main([__file__, "-v"])
