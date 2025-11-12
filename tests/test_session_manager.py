"""Tests for session_manager module."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.esb_smart_meter.const import SESSION_EXPIRY_HOURS
from custom_components.esb_smart_meter.session_manager import (
    CaptchaRequiredException,
    SessionManager,
)


@pytest.fixture
def mock_hass(tmp_path):
    """Create a mock Home Assistant instance."""
    hass = Mock()
    # Use tmp_path for test storage
    hass.config.path.return_value = str(tmp_path / "esb_smart_meter")
    # Make async_add_executor_job actually async
    async def async_executor_job(func, *args):
        return func(*args)
    hass.async_add_executor_job = async_executor_job
    return hass


@pytest.fixture
def session_manager(mock_hass):
    """Create a SessionManager instance."""
    return SessionManager(mock_hass, "12345678901")


class TestSessionManager:
    """Tests for SessionManager class."""

    def test_init(self, session_manager, mock_hass):
        """Test SessionManager initialization."""
        assert session_manager._hass == mock_hass
        assert session_manager._mprn == "12345678901"
        assert "12345678901" in str(session_manager._session_file)

    @pytest.mark.asyncio
    async def test_load_session_no_file(self, session_manager):
        """Test loading session when no file exists."""
        with patch.object(Path, "exists", return_value=False):
            result = await session_manager.load_session()
            assert result is None

    @pytest.mark.asyncio
    async def test_load_session_valid(self, session_manager):
        """Test loading a valid session."""
        now = dt_util.utcnow()
        expires_at = now + timedelta(hours=24)
        
        session_data = {
            "cookies": {"session_id": "abc123", "auth_token": "xyz789"},
            "user_agent": "Mozilla/5.0",
            "download_token": "token123",
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "mprn": "12345678901",
        }

        with patch.object(Path, "exists", return_value=True):
            with patch.object(
                session_manager, "_read_session_file", return_value=session_data
            ):
                result = await session_manager.load_session()
                
                assert result is not None
                assert result["cookies"] == session_data["cookies"]
                assert result["user_agent"] == "Mozilla/5.0"
                assert result["download_token"] == "token123"

    @pytest.mark.asyncio
    async def test_load_session_expired(self, session_manager):
        """Test loading an expired session."""
        now = dt_util.utcnow()
        expires_at = now - timedelta(hours=1)  # Expired 1 hour ago
        
        session_data = {
            "cookies": {"session_id": "abc123"},
            "user_agent": "Mozilla/5.0",
            "download_token": "token123",
            "created_at": (now - timedelta(hours=25)).isoformat(),
            "expires_at": expires_at.isoformat(),
            "mprn": "12345678901",
        }

        with patch.object(Path, "exists", return_value=True):
            with patch.object(
                session_manager, "_read_session_file", return_value=session_data
            ):
                with patch.object(session_manager, "clear_session") as mock_clear:
                    result = await session_manager.load_session()
                    
                    assert result is None
                    mock_clear.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_session(self, session_manager):
        """Test saving a session."""
        cookies = {"session_id": "abc123", "auth_token": "xyz789"}
        user_agent = "Mozilla/5.0"
        download_token = "token123"

        with patch.object(session_manager, "_write_session_file") as mock_write:
            await session_manager.save_session(cookies, user_agent, download_token)
            
            mock_write.assert_called_once()
            saved_data = mock_write.call_args[0][0]
            
            assert saved_data["cookies"] == cookies
            assert saved_data["user_agent"] == user_agent
            assert saved_data["download_token"] == download_token
            assert saved_data["mprn"] == "12345678901"
            assert "created_at" in saved_data
            assert "expires_at" in saved_data

    def test_is_session_valid_missing_fields(self, session_manager):
        """Test validation of session with missing fields."""
        session_data = {"cookies": {}}  # Missing expires_at
        assert not session_manager._is_session_valid(session_data)

    def test_is_session_valid_expired(self, session_manager):
        """Test validation of expired session."""
        now = dt_util.utcnow()
        expires_at = now - timedelta(hours=1)
        
        session_data = {
            "cookies": {"session_id": "abc123"},
            "expires_at": expires_at.isoformat(),
            "mprn": "12345678901",
        }
        
        assert not session_manager._is_session_valid(session_data)

    def test_is_session_valid_mprn_mismatch(self, session_manager):
        """Test validation of session with wrong MPRN."""
        now = dt_util.utcnow()
        expires_at = now + timedelta(hours=24)
        
        session_data = {
            "cookies": {"session_id": "abc123"},
            "expires_at": expires_at.isoformat(),
            "mprn": "99999999999",  # Different MPRN
        }
        
        assert not session_manager._is_session_valid(session_data)

    def test_is_session_valid_success(self, session_manager):
        """Test validation of valid session."""
        now = dt_util.utcnow()
        expires_at = now + timedelta(hours=24)
        
        session_data = {
            "cookies": {"session_id": "abc123"},
            "expires_at": expires_at.isoformat(),
            "mprn": "12345678901",
        }
        
        assert session_manager._is_session_valid(session_data)

    @pytest.mark.asyncio
    async def test_clear_session(self, session_manager):
        """Test clearing session."""
        mock_file = Mock()
        session_manager._session_file = mock_file
        mock_file.exists.return_value = True

        with patch.object(session_manager._hass, "async_add_executor_job") as mock_job:
            await session_manager.clear_session()
            mock_job.assert_called_once()

    def test_extract_cookies_from_jar(self, session_manager):
        """Test extracting cookies from cookie jar."""
        mock_cookie1 = Mock()
        mock_cookie1.key = "session_id"
        mock_cookie1.value = "abc123"
        
        mock_cookie2 = Mock()
        mock_cookie2.key = "auth_token"
        mock_cookie2.value = "xyz789"
        
        mock_jar = [mock_cookie1, mock_cookie2]
        
        cookies = session_manager.extract_cookies_from_jar(mock_jar)
        
        assert cookies == {"session_id": "abc123", "auth_token": "xyz789"}

    def test_parse_cookie_string(self, session_manager):
        """Test parsing cookie string."""
        cookie_string = "session_id=abc123; auth_token=xyz789; user_pref=dark_mode"
        
        cookies = session_manager._parse_cookie_string(cookie_string)
        
        assert cookies == {
            "session_id": "abc123",
            "auth_token": "xyz789",
            "user_pref": "dark_mode",
        }

    def test_parse_cookie_string_with_spaces(self, session_manager):
        """Test parsing cookie string with extra spaces."""
        cookie_string = "  session_id = abc123 ;  auth_token = xyz789  "
        
        cookies = session_manager._parse_cookie_string(cookie_string)
        
        assert cookies == {
            "session_id": "abc123",
            "auth_token": "xyz789",
        }

    @pytest.mark.asyncio
    async def test_save_manual_cookies_success(self, session_manager):
        """Test saving manual cookies successfully."""
        cookie_string = "session_id=abc123; auth_token=xyz789"
        
        with patch.object(session_manager, "save_session") as mock_save:
            result = await session_manager.save_manual_cookies(cookie_string)
            
            assert result is True
            mock_save.assert_called_once()
            
            call_args = mock_save.call_args
            assert call_args[1]["cookies"]["session_id"] == "abc123"
            assert call_args[1]["cookies"]["auth_token"] == "xyz789"

    @pytest.mark.asyncio
    async def test_save_manual_cookies_empty(self, session_manager):
        """Test saving empty cookie string."""
        cookie_string = ""
        
        result = await session_manager.save_manual_cookies(cookie_string)
        
        assert result is False

    @pytest.mark.asyncio
    async def test_save_manual_cookies_invalid(self, session_manager):
        """Test saving invalid cookie string."""
        cookie_string = "not a valid cookie string"
        
        result = await session_manager.save_manual_cookies(cookie_string)
        
        # Should fail if no valid cookies can be parsed
        assert result is False


    @pytest.mark.asyncio
    async def test_load_session_corrupt_json(self, session_manager):
        """Test loading session with corrupt JSON file."""
        with patch.object(Path, "exists", return_value=True), \
             patch.object(session_manager, "_read_session_file", return_value=None):
            result = await session_manager.load_session()
            assert result is None

    @pytest.mark.asyncio
    async def test_load_session_exception(self, session_manager):
        """Test loading session with unexpected exception."""
        with patch.object(Path, "exists", side_effect=OSError("Permission denied")):
            result = await session_manager.load_session()
            assert result is None

    def test_read_session_file_io_error(self, session_manager, tmp_path):
        """Test reading session file with IO error."""
        session_manager._session_file = tmp_path / "nonexistent" / "session.json"
        result = session_manager._read_session_file()
        assert result is None

    def test_read_session_file_json_decode_error(self, session_manager, tmp_path):
        """Test reading session file with invalid JSON."""
        session_file = tmp_path / "session.json"
        session_file.write_text("not valid json {]")
        session_manager._session_file = session_file
        
        result = session_manager._read_session_file()
        assert result is None

    @pytest.mark.asyncio
    async def test_save_session_write_error(self, session_manager):
        """Test saving session when write fails."""
        cookies = {"session_id": "abc123"}
        user_agent = "Mozilla/5.0"

        with patch.object(session_manager, "_write_session_file", side_effect=OSError("Disk full")):
            # Should not raise, just log error
            await session_manager.save_session(cookies, user_agent)

    def test_write_session_file_permission_error(self, session_manager, tmp_path):
        """Test writing session file with permission error."""
        session_data = {"cookies": {"test": "data"}}
        
        # Create a read-only directory
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        session_manager._session_file = readonly_dir / "session.json"
        
        with patch("builtins.open", side_effect=PermissionError("Permission denied")):
            with pytest.raises(PermissionError):
                session_manager._write_session_file(session_data)

    def test_is_session_valid_invalid_datetime(self, session_manager):
        """Test validation with invalid datetime format."""
        session_data = {
            "cookies": {"session_id": "abc123"},
            "expires_at": "not a valid datetime",
            "mprn": "12345678901",
        }
        
        assert not session_manager._is_session_valid(session_data)

    def test_is_session_valid_missing_cookies(self, session_manager):
        """Test validation with missing cookies field."""
        now = dt_util.utcnow()
        expires_at = now + timedelta(hours=24)
        
        session_data = {
            "expires_at": expires_at.isoformat(),
            "mprn": "12345678901",
        }
        
        assert not session_manager._is_session_valid(session_data)

    def test_is_session_valid_empty_cookies(self, session_manager):
        """Test validation with empty cookies dict."""
        now = dt_util.utcnow()
        expires_at = now + timedelta(hours=24)
        
        session_data = {
            "cookies": {},
            "expires_at": expires_at.isoformat(),
            "mprn": "12345678901",
        }
        
        assert not session_manager._is_session_valid(session_data)

    @pytest.mark.asyncio
    async def test_clear_session_file_not_exists(self, session_manager, tmp_path):
        """Test clearing session when file doesn't exist."""
        # Use a non-existent path
        session_manager._session_file = tmp_path / "nonexistent.json"
        # Should not raise
        await session_manager.clear_session()

    @pytest.mark.asyncio
    async def test_clear_session_unlink_error(self, session_manager, tmp_path):
        """Test clearing session when unlink fails."""
        # Create a file that exists
        session_file = tmp_path / "session.json"
        session_file.write_text("{}")
        session_manager._session_file = session_file
        
        with patch.object(session_manager._hass, "async_add_executor_job", side_effect=OSError("Cannot delete")):
            # Should not raise, just log error
            await session_manager.clear_session()

    def test_extract_cookies_empty_jar(self, session_manager):
        """Test extracting cookies from empty jar."""
        mock_jar = []
        cookies = session_manager.extract_cookies_from_jar(mock_jar)
        assert cookies == {}

    def test_parse_cookie_string_empty(self, session_manager):
        """Test parsing empty cookie string."""
        cookies = session_manager._parse_cookie_string("")
        assert cookies == {}

    def test_parse_cookie_string_whitespace_only(self, session_manager):
        """Test parsing whitespace-only cookie string."""
        cookies = session_manager._parse_cookie_string("   ;  ;  ")
        assert cookies == {}

    def test_parse_cookie_string_malformed(self, session_manager):
        """Test parsing malformed cookie string."""
        cookie_string = "malformed;;;===;;cookie"
        cookies = session_manager._parse_cookie_string(cookie_string)
        # Should handle gracefully, may return empty or partial
        assert isinstance(cookies, dict)

    def test_parse_cookie_string_no_equals(self, session_manager):
        """Test parsing cookie string without equals signs."""
        cookie_string = "session_id; auth_token"
        cookies = session_manager._parse_cookie_string(cookie_string)
        # Should skip invalid entries
        assert isinstance(cookies, dict)

    @pytest.mark.asyncio
    async def test_save_manual_cookies_whitespace(self, session_manager):
        """Test saving manual cookies with only whitespace."""
        cookie_string = "   "
        result = await session_manager.save_manual_cookies(cookie_string)
        assert result is False

    @pytest.mark.asyncio
    async def test_save_manual_cookies_with_save_error(self, session_manager):
        """Test saving manual cookies when save_session fails."""
        cookie_string = "session_id=abc123"
        
        with patch.object(session_manager, "save_session", side_effect=Exception("Save failed")):
            result = await session_manager.save_manual_cookies(cookie_string)
            assert result is False

    def test_session_manager_storage_path_creation_error(self, mock_hass, tmp_path):
        """Test SessionManager when storage directory creation fails."""
        mock_hass.config.path.return_value = str(tmp_path / "nonexistent" / "readonly" / "path")
        
        with patch.object(Path, "mkdir", side_effect=OSError("Cannot create directory")):
            # Should not raise, just log debug message
            session_manager = SessionManager(mock_hass, "12345678901")
            assert session_manager._mprn == "12345678901"

    def test_session_manager_storage_path_permission_error(self, mock_hass, tmp_path):
        """Test SessionManager when storage directory has permission error."""
        mock_hass.config.path.return_value = str(tmp_path / "readonly")
        
        with patch.object(Path, "mkdir", side_effect=PermissionError("Permission denied")):
            # Should not raise
            session_manager = SessionManager(mock_hass, "12345678901")
            assert session_manager._mprn == "12345678901"


class TestCaptchaRequiredException:
    """Tests for CaptchaRequiredException."""

    def test_exception_creation(self):
        """Test creating exception."""
        exc = CaptchaRequiredException("Test message")
        assert str(exc) == "Test message"
        assert exc.requires_user_action is True

    def test_exception_default_message(self):
        """Test exception with default message."""
        exc = CaptchaRequiredException()
        assert "CAPTCHA verification required" in str(exc)
        assert exc.requires_user_action is True
