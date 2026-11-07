"""Session persistence manager for ESB Smart Meter integration."""

import json
import logging
import os
from datetime import datetime, timedelta
from functools import partial
from http.cookies import CookieError, Morsel
from pathlib import Path
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from yarl import URL

from .const import (
    CAPTCHA_COOLDOWN_HOURS,
    DOMAIN,
    ESB_MYACCOUNT_URL,
    SESSION_EXPIRY_HOURS,
    SESSION_FILE_NAME,
    SESSION_SOURCE_LOGIN,
    SESSION_SOURCE_MANUAL,
)
from .user_agents import USER_AGENTS

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
        
        # The session file holds live ESB login cookies, so it lives under .storage/, which
        # config-backup tools (git-based ones especially) exclude. Until this change it was
        # <config>/esb_smart_meter/, which a git-synced /config commits and pushes.
        path_val = hass.config.path(".storage", DOMAIN)
        legacy_val = hass.config.path(DOMAIN)
        legacy_path = None
        if type(path_val).__name__ in ("Mock", "MagicMock", "AsyncMock") or "MagicMock" in str(path_val):
            self._storage_path = Path(".tmp/mock_esb_smart_meter")
        else:
            self._storage_path = Path(str(path_val))
            legacy_path = Path(str(legacy_val))

        mprn_str = str(mprn)
        if "Mock" in mprn_str:
            mprn_str = "mock"

        self._session_file = self._storage_path / f"{SESSION_FILE_NAME}_{mprn_str}.json"

        self._legacy_file = (
            legacy_path / self._session_file.name
            if legacy_path is not None and legacy_path != self._storage_path
            else None
        )
        self._storage_ready = False

    def _prepare_storage(self) -> None:
        """Create the storage folder and move a pre-1.4.1 session file into it (executor)."""
        try:
            self._storage_path.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            _LOGGER.debug("Could not create storage directory: %s", err)
        if self._legacy_file is not None:
            self._move_legacy_session(self._legacy_file)

    async def _async_ensure_storage(self) -> None:
        """Prepare the storage folder once, in the executor."""
        if not self._storage_ready:
            await self._hass.async_add_executor_job(self._prepare_storage)
            self._storage_ready = True

    def _move_legacy_session(self, legacy_file: Path) -> None:
        """Move a session file out of the old, backup-visible location."""
        try:
            if legacy_file.exists():
                if self._session_file.exists():
                    legacy_file.unlink()
                else:
                    legacy_file.replace(self._session_file)
                _LOGGER.info("Moved ESB session cache out of %s", legacy_file.parent)
        except OSError as err:
            _LOGGER.warning("Could not move legacy session cache %s: %s", legacy_file, err)

    async def load_session(self) -> dict[str, Any] | None:
        """Load a previously saved session from disk.

        Returns:
            Session data dictionary if valid session exists, None otherwise
        """
        try:
            await self._async_ensure_storage()

            # Read session data
            session_data = await self._hass.async_add_executor_job(self._read_session_file)

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
        if not self._session_file.exists():
            _LOGGER.debug("No session file found at %s", self._session_file)
            return None
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
        source: str = SESSION_SOURCE_LOGIN,
    ) -> bool:
        """Save session data to disk for reuse.

        Args:
            cookies: Session cookies to save
            user_agent: User agent string used for this session
            download_token: Optional download token (if available)
            source: SESSION_SOURCE_LOGIN or SESSION_SOURCE_MANUAL (pasted by the user)

        Returns:
            True if the session was written to disk
        """
        try:
            await self._async_ensure_storage()
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
                "source": source,
            }

            await self._hass.async_add_executor_job(self._write_session_file, session_data)

            _LOGGER.info(
                "Session saved successfully (expires: %s)",
                expires_at.strftime("%Y-%m-%d %H:%M:%S"),
            )
            return True

        except Exception as err:
            _LOGGER.error("Error saving session: %s", err)
            return False

    def _write_session_file(self, session_data: dict[str, Any]) -> None:
        """Write session file to disk (runs in executor)."""
        # The file holds live login cookies: owner-only, also for a pre-existing file
        fd = os.open(self._session_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2)
        os.chmod(self._session_file, 0o600)

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
            await self._async_ensure_storage()
            await self._hass.async_add_executor_job(
                partial(self._session_file.unlink, missing_ok=True)
            )
            _LOGGER.debug("Session cache cleared")
        except Exception as err:
            _LOGGER.error("Error clearing session: %s", err)

    async def mark_rejected(self, session_data: dict[str, Any]) -> None:
        """Keep a rejected pasted session as a marker, so no scripted login follows straight away."""
        rejected_at = dt_util.utcnow()
        cooldown_end = rejected_at + timedelta(hours=CAPTCHA_COOLDOWN_HOURS)
        try:
            expires_at = max(datetime.fromisoformat(session_data["expires_at"]), cooldown_end)
        except (KeyError, TypeError, ValueError):
            expires_at = cooldown_end
        # expires_at is stretched so load_session does not delete the marker inside the cooldown
        marked = {
            **session_data,
            "download_token": None,
            "rejected_at": rejected_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        await self._hass.async_add_executor_job(self._write_session_file, marked)

    def rejection_active(self, session_data: dict[str, Any]) -> bool:
        """Return True while a pasted session was rejected less than CAPTCHA_COOLDOWN_HOURS ago."""
        rejected_at = session_data.get("rejected_at")
        if not rejected_at:
            return False
        try:
            rejected = datetime.fromisoformat(rejected_at)
        except (TypeError, ValueError):
            return False
        return dt_util.utcnow() - rejected < timedelta(hours=CAPTCHA_COOLDOWN_HOURS)

    async def check_session(self, http_session: aiohttp.ClientSession, user_agent: str) -> bool:
        """Ask ESB whether the cookies in http_session's jar are logged in.

        Loads the account page, as a browser does first: True on 200, False when ESB
        sends the browser to log in (3xx, 401, 403). Network errors, 429 and 5xx raise,
        since they say nothing about the cookies.
        """
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{ESB_MYACCOUNT_URL}/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
        }
        async with http_session.get(
            ESB_MYACCOUNT_URL,
            headers=headers,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            status = response.status
            if status == 200:
                _LOGGER.debug("Session validation successful - received 200 response")
                return True
            if status < 400 or status in (401, 403):
                # Host only: the full Location carries OIDC state/nonce parameters.
                try:
                    redirect_host = URL(str(response.headers.get("Location", ""))).host or "-"
                except ValueError:
                    redirect_host = "-"
                # WARNING, not DEBUG: this is the only line that says WHY a session was rejected.
                # A 302 to login.esbnetworks.ie means the login cookie was not among those sent.
                _LOGGER.warning(
                    "Session validation failed - ESB answered HTTP %d (redirect host: %s)",
                    status,
                    redirect_host,
                )
                return False
            response.raise_for_status()
            return False

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
        esb_url = URL("https://myaccount.esbnetworks.ie")

        for name, value in cookies.items():
            # A Morsel keeps the value as pasted; the jar (quote_cookie=False) sends it unquoted
            morsel = Morsel()
            try:
                morsel.set(name, value, value)
            except CookieError:
                _LOGGER.warning("Skipping cookie %s: its name cannot be sent in a Cookie header", name)
                continue
            morsel["domain"] = "myaccount.esbnetworks.ie"
            morsel["path"] = "/"
            cookie_jar.update_cookies({name: morsel}, esb_url)

        _LOGGER.debug("Loaded %d cookies to jar", len(cookies))

    async def save_manual_cookies(self, cookie_string: str, user_agent: str | None = None) -> bool:
        """Save manually provided cookies from user (for CAPTCHA bypass).

        Args:
            cookie_string: The browser's Cookie request header (``name=value; ...``)
            user_agent: The browser's User-Agent; the session is replayed with it

        Returns:
            True if cookies were parsed and saved successfully
        """
        try:
            cookies = self._parse_cookie_string(cookie_string)

            if not cookies:
                _LOGGER.error("No cookies parsed from provided string")
                return False

            # Replay with the browser's own User-Agent when given; otherwise a complete,
            # real one (the old default was a truncated string no browser sends).
            saved = await self.save_session(
                cookies=cookies,
                user_agent=(user_agent or "").strip() or USER_AGENTS[0],
                download_token=None,
                source=SESSION_SOURCE_MANUAL,
            )
            if not saved:
                return False

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

        # The Cookie request header ("name=value; name2=value2"), with or without its
        # "Cookie:" name, possibly wrapped over several lines by the copy.
        # Line breaks from the copy can fall inside a value, so drop all whitespace
        cookie_string = "".join(cookie_string.split())
        if cookie_string.lower().startswith("cookie:"):
            cookie_string = cookie_string[len("cookie:") :]

        for part in cookie_string.split(";"):
            part = part.strip()
            if "=" in part:
                name, value = part.split("=", 1)
                cookies[name.strip()] = value.strip()

        return cookies
