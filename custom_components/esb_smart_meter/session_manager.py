"""Session persistence manager for ESB Smart Meter integration."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    SESSION_EXPIRY_HOURS,
    SESSION_FILE_NAME,
    SESSION_VALIDATION_ENDPOINTS,
)

_LOGGER = logging.getLogger(__name__)


class CaptchaRequiredException(Exception):
    """Exception raised when CAPTCHA is detected and user intervention is required."""

    def __init__(self, message: str = "CAPTCHA verification required") -> None:
        """Initialize the exception."""
        super().__init__(message)
        self.requires_user_action = True


class SessionManager:
    """Manage session persistence across Home Assistant restarts."""

    def __init__(self, hass: HomeAssistant, mprn: str) -> None:
        """Initialize the session manager.
        
        Args:
            hass: Home Assistant instance
            mprn: Meter Point Reference Number (used to namespace sessions)
        """
        self._hass = hass
        self._mprn = mprn
        self._storage_path = Path(hass.config.path(DOMAIN))
        self._session_file = self._storage_path / f"{SESSION_FILE_NAME}_{mprn}.json"
        
        # Ensure storage directory exists (with error handling for tests)
        try:
            self._storage_path.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as err:
            _LOGGER.debug("Could not create storage directory: %s", err)

    async def load_session(self) -> dict[str, Any] | None:
        """Load a previously saved session from disk.
        
        Returns:
            Session data dictionary if valid session exists, None otherwise
        """
        try:
            if not self._session_file.exists():
                _LOGGER.debug("No session file found at %s", self._session_file)
                return None

            # Read session data
            session_data = await self._hass.async_add_executor_job(
                self._read_session_file
            )
            
            if not session_data:
                return None

            # Validate session hasn't expired
            if not self._is_session_valid(session_data):
                _LOGGER.info("Stored session has expired, will need fresh login")
                await self.clear_session()
                return None

            _LOGGER.info(
                "Loaded valid session from cache (expires: %s)",
                session_data.get("expires_at"),
            )
            return session_data

        except Exception as err:
            _LOGGER.error("Error loading session: %s", err)
            return None

    def _read_session_file(self) -> dict[str, Any] | None:
        """Read session file from disk (runs in executor)."""
        try:
            with open(self._session_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as err:
            _LOGGER.error("Error reading session file: %s", err)
            return None

    async def save_session(
        self,
        cookies: dict[str, str],
        user_agent: str,
        download_token: str | None = None,
    ) -> None:
        """Save session data to disk for reuse.
        
        Args:
            cookies: Session cookies to save
            user_agent: User agent string used for this session
            download_token: Optional download token (if available)
        """
        try:
            # Calculate expiry time
            now = dt_util.utcnow()
            expires_at = now + timedelta(hours=SESSION_EXPIRY_HOURS)

            session_data = {
                "cookies": cookies,
                "user_agent": user_agent,
                "download_token": download_token,
                "created_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
                "mprn": self._mprn,
            }

            await self._hass.async_add_executor_job(
                self._write_session_file, session_data
            )

            _LOGGER.info(
                "Session saved successfully (expires: %s)",
                expires_at.strftime("%Y-%m-%d %H:%M:%S"),
            )

        except Exception as err:
            _LOGGER.error("Error saving session: %s", err)

    def _write_session_file(self, session_data: dict[str, Any]) -> None:
        """Write session file to disk (runs in executor)."""
        with open(self._session_file, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2)

    def _is_session_valid(self, session_data: dict[str, Any]) -> bool:
        """Check if session data is still valid.
        
        Args:
            session_data: Session data dictionary
            
        Returns:
            True if session is valid and not expired
        """
        try:
            # Check required fields
            if not session_data.get("cookies") or not session_data.get("expires_at"):
                _LOGGER.debug("Session missing required fields")
                return False

            # Check expiry
            expires_at_str = session_data["expires_at"]
            expires_at = datetime.fromisoformat(expires_at_str)
            now = dt_util.utcnow()

            if now >= expires_at:
                _LOGGER.debug("Session expired at %s (now: %s)", expires_at, now)
                return False

            # Check MPRN matches
            if session_data.get("mprn") != self._mprn:
                _LOGGER.warning(
                    "Session MPRN mismatch: expected %s, got %s",
                    self._mprn,
                    session_data.get("mprn"),
                )
                return False

            return True

        except (ValueError, KeyError) as err:
            _LOGGER.error("Error validating session: %s", err)
            return False

    async def clear_session(self) -> None:
        """Clear saved session data."""
        try:
            if self._session_file.exists():
                await self._hass.async_add_executor_job(self._session_file.unlink)
                _LOGGER.debug("Session cache cleared")
        except Exception as err:
            _LOGGER.error("Error clearing session: %s", err)

    async def validate_session_cookies(
        self, cookies: dict[str, str], user_agent: str
    ) -> bool:
        """Validate that session cookies are still working.
        
        Args:
            cookies: Cookies to validate
            user_agent: User agent to use for validation request
            
        Returns:
            True if cookies are valid and session is active
        """
        # This is a placeholder for actual validation
        # In production, this would make a lightweight request to ESB
        # to verify the session is still authenticated
        
        # For now, we trust the expiry time
        # TODO: Implement actual session validation request
        return True

    def extract_cookies_from_jar(self, cookie_jar) -> dict[str, str]:
        """Extract cookies from aiohttp cookie jar.
        
        Args:
            cookie_jar: aiohttp.CookieJar instance
            
        Returns:
            Dictionary of cookie name -> value
        """
        cookies = {}
        for cookie in cookie_jar:
            cookies[cookie.key] = cookie.value
        
        _LOGGER.debug("Extracted %d cookies from jar", len(cookies))
        return cookies

    def load_cookies_to_jar(self, cookie_jar, cookies: dict[str, str]) -> None:
        """Load cookies from dict into aiohttp cookie jar.
        
        Args:
            cookie_jar: aiohttp.CookieJar instance
            cookies: Dictionary of cookie name -> value
        """
        # Note: This is a simplified version
        # In production, we'd need to reconstruct full cookie objects with domain, path, etc.
        # For now, we'll let the session handle cookie management
        _LOGGER.debug("Would load %d cookies to jar", len(cookies))

    async def save_manual_cookies(self, cookie_string: str) -> bool:
        """Save manually provided cookies from user (for CAPTCHA bypass).
        
        Args:
            cookie_string: Raw cookie string from browser
            
        Returns:
            True if cookies were parsed and saved successfully
        """
        try:
            cookies = self._parse_cookie_string(cookie_string)
            
            if not cookies:
                _LOGGER.error("No cookies parsed from provided string")
                return False

            # Save with default user agent (user can update if needed)
            await self.save_session(
                cookies=cookies,
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                download_token=None,
            )
            
            _LOGGER.info("Manual cookies saved successfully (%d cookies)", len(cookies))
            return True

        except Exception as err:
            _LOGGER.error("Error saving manual cookies: %s", err)
            return False

    def _parse_cookie_string(self, cookie_string: str) -> dict[str, str]:
        """Parse cookie string from browser into dictionary.
        
        Args:
            cookie_string: Cookie string in format "name1=value1; name2=value2"
            
        Returns:
            Dictionary of cookie name -> value
        """
        cookies = {}
        
        # Handle both formats: "name=value; name2=value2" and document.cookie format
        cookie_string = cookie_string.strip()
        
        for part in cookie_string.split(";"):
            part = part.strip()
            if "=" in part:
                name, value = part.split("=", 1)
                cookies[name.strip()] = value.strip()

        return cookies
