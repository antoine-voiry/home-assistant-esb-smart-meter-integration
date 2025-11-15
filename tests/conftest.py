"""Test configuration and fixtures."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest


def _async_create_task_handler(coro):
    """Handle async_create_task by closing the coroutine to prevent warnings.
    
    This prevents RuntimeWarning about unawaited coroutines in tests.
    """
    try:
        coro.close()
    except Exception:
        pass
    return MagicMock()


@pytest.fixture
def mock_hass():
    """Mock Home Assistant instance."""
    hass = MagicMock()
    hass.data = {}
    hass.async_add_executor_job = AsyncMock()
    # Mock async_create_task to properly close coroutines and prevent RuntimeWarnings
    hass.async_create_task = MagicMock(side_effect=_async_create_task_handler)
    return hass


@pytest.fixture
def mock_aiohttp_session():
    """Mock aiohttp ClientSession."""
    session = MagicMock()
    session.cookie_jar = MagicMock()
    session.cookie_jar.filter_cookies.return_value = {"test": "cookie"}
    return session


@pytest.fixture
def sample_csv_data():
    """Sample CSV data for testing."""
    now = datetime.now()
    csv_lines = ["Read Date and End Time,Read Value,Read Type,MPRN"]

    # Add data for last 30 days
    for i in range(30):
        date = now - timedelta(days=i)
        date_str = date.strftime("%d-%m-%Y %H:%M")
        csv_lines.append(f"{date_str},1.5,Active Import,12345678901")

    return "\n".join(csv_lines)


@pytest.fixture
def sample_esb_login_html():
    """Sample ESB login page HTML."""
    return """
    <html>
    <script>var SETTINGS = {"csrf":"test-csrf-token","transId":"test-trans-id"};</script>
    <body>Login page</body>
    </html>
    """


@pytest.fixture
def sample_esb_confirm_html():
    """Sample ESB confirmation page HTML."""
    return """
    <html>
    <body>
        <form id="auto" action="https://test.esb.ie/confirm">
            <input name="state" value="test-state" />
            <input name="client_info" value="test-client-info" />
            <input name="code" value="test-code" />
        </form>
    </body>
    </html>
    """


@pytest.fixture
def mock_config_entry():
    """Mock config entry."""
    entry = MagicMock()
    entry.data = {
        "username": "test@example.com",
        "password": "test-password",
        "mprn": "12345678901",
    }
    entry.entry_id = "test-entry-id"
    return entry
