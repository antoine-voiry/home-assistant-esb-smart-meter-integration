"""Tests for CAPTCHA handling in config flow."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant import config_entries

from custom_components.esb_smart_meter.config_flow import (
    ESBSmartMeterConfigFlow,
    ESBSmartMeterOptionsFlow,
)
from custom_components.esb_smart_meter.const import (
    CONF_MANUAL_COOKIES,
    CONF_MPRN,
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    DOMAIN,
)


@pytest.fixture
def mock_hass(tmp_path):
    """Create a mock Home Assistant instance."""
    hass = Mock()
    hass.config_entries = Mock()
    hass.config_entries.async_entries.return_value = []
    hass.config.path.return_value = str(tmp_path / "esb_smart_meter")
    return hass


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    entry = Mock(spec=[])  # Don't allow attribute access by default
    entry.data = {
        CONF_USERNAME: "test@example.com",
        CONF_PASSWORD: "password123",
        CONF_MPRN: "12345678901",
    }
    entry.entry_id = "test_entry_id"
    entry.options = {}  # Add options attribute
    return entry


class TestOptionsFlow:
    """Tests for the options flow."""

    @pytest.mark.asyncio
    async def test_init_step(self, mock_hass, mock_config_entry):
        """Test the init step shows menu."""
        flow = ESBSmartMeterOptionsFlow(mock_config_entry)
        flow.hass = mock_hass

        result = await flow.async_step_init()

        assert result["type"] == "menu"
        assert "update_interval" in result["menu_options"]
        assert "manual_cookies" in result["menu_options"]

    @pytest.mark.asyncio
    async def test_update_interval_step_form(self, mock_hass, mock_config_entry):
        """Test the update interval step shows form."""
        flow = ESBSmartMeterOptionsFlow(mock_config_entry)
        flow.hass = mock_hass

        result = await flow.async_step_update_interval()

        assert result["type"] == "form"
        assert result["step_id"] == "update_interval"
        assert CONF_UPDATE_INTERVAL in result["data_schema"].schema

    @pytest.mark.asyncio
    async def test_update_interval_step_valid_input(self, mock_hass, mock_config_entry):
        """Test update interval step with valid input."""
        flow = ESBSmartMeterOptionsFlow(mock_config_entry)
        flow.hass = mock_hass

        user_input = {CONF_UPDATE_INTERVAL: 6}  # 6 hours

        result = await flow.async_step_update_interval(user_input=user_input)

        assert result["type"] == "create_entry"
        assert result["data"] == user_input

    @pytest.mark.asyncio
    async def test_manual_cookies_step_form(self, mock_hass, mock_config_entry):
        """Test the manual cookies step shows form."""
        flow = ESBSmartMeterOptionsFlow(mock_config_entry)
        flow.hass = mock_hass

        result = await flow.async_step_manual_cookies()

        assert result["type"] == "form"
        assert result["step_id"] == "manual_cookies"
        assert CONF_MANUAL_COOKIES in result["data_schema"].schema

    @pytest.mark.asyncio
    async def test_manual_cookies_empty_input(self, mock_hass, mock_config_entry):
        """Test validation fails with empty cookies."""
        flow = ESBSmartMeterOptionsFlow(mock_config_entry)
        flow.hass = mock_hass

        result = await flow.async_step_manual_cookies(
            user_input={CONF_MANUAL_COOKIES: ""}
        )

        assert result["type"] == "form"
        assert "empty_cookies" in result["errors"].get(CONF_MANUAL_COOKIES, "")

    @pytest.mark.asyncio
    async def test_manual_cookies_success(self, mock_hass, mock_config_entry):
        """Test successful cookie submission."""
        flow = ESBSmartMeterOptionsFlow(mock_config_entry)
        flow.hass = mock_hass

        cookie_string = "session_id=abc123; auth_token=xyz789"

        with patch(
            "custom_components.esb_smart_meter.config_flow.SessionManager"
        ) as mock_session_class:
            mock_session = Mock()
            mock_session.save_manual_cookies = AsyncMock(return_value=True)
            mock_session_class.return_value = mock_session

            result = await flow.async_step_manual_cookies(
                user_input={CONF_MANUAL_COOKIES: cookie_string}
            )

            assert result["type"] == "create_entry"
            mock_session.save_manual_cookies.assert_called_once_with(cookie_string)

    @pytest.mark.asyncio
    async def test_manual_cookies_invalid(self, mock_hass, mock_config_entry):
        """Test invalid cookies are rejected."""
        flow = ESBSmartMeterOptionsFlow(mock_config_entry)
        flow.hass = mock_hass

        cookie_string = "invalid cookie format"

        with patch(
            "custom_components.esb_smart_meter.config_flow.SessionManager"
        ) as mock_session_class:
            mock_session = Mock()
            mock_session.save_manual_cookies = AsyncMock(return_value=False)
            mock_session_class.return_value = mock_session

            result = await flow.async_step_manual_cookies(
                user_input={CONF_MANUAL_COOKIES: cookie_string}
            )

            assert result["type"] == "form"
            assert "invalid_cookies" in result["errors"].get(CONF_MANUAL_COOKIES, "")


class TestConfigFlowWithOptions:
    """Tests for config flow with options."""

    def test_config_flow_has_options(self):
        """Test that config flow provides options flow."""
        config_entry = Mock()
        options_flow = ESBSmartMeterConfigFlow.async_get_options_flow(config_entry)
        
        assert isinstance(options_flow, ESBSmartMeterOptionsFlow)
        assert options_flow._config_entry == config_entry
