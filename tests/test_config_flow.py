"""Tests for config flow."""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.esb_smart_meter.config_flow import ESBSmartMeterConfigFlow
from custom_components.esb_smart_meter.const import CONF_MPRN, CONF_PASSWORD, CONF_USERNAME


class TestConfigFlow:
    """Test config flow."""

    @pytest.fixture
    def flow(self):
        """Create config flow instance."""
        flow = ESBSmartMeterConfigFlow()
        flow.hass = MagicMock()
        flow.hass.config_entries = MagicMock()
        flow.hass.config_entries.async_entries.return_value = []
        return flow

    @pytest.mark.asyncio
    async def test_user_form(self, flow):
        """Test user form is shown."""
        result = await flow.async_step_user(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "user"
        assert result["errors"] == {}

    @pytest.mark.asyncio
    async def test_user_form_valid_input(self, flow):
        """Test user form with valid input."""
        user_input = {
            CONF_USERNAME: "test@example.com",
            CONF_PASSWORD: "test-password",
            CONF_MPRN: "12345678901",
        }

        with patch.object(flow, "async_set_unique_id"), patch.object(flow, "_abort_if_unique_id_configured"):
            result = await flow.async_step_user(user_input=user_input)

        assert result["type"] == "create_entry"
        assert result["title"] == "ESB Smart Meter (12345678901)"
        assert result["data"] == user_input

    @pytest.mark.asyncio
    async def test_user_form_invalid_mprn_length(self, flow):
        """Test user form with invalid MPRN length."""
        user_input = {
            CONF_USERNAME: "test@example.com",
            CONF_PASSWORD: "test-password",
            CONF_MPRN: "123",  # Too short
        }

        result = await flow.async_step_user(user_input=user_input)

        assert result["type"] == "form"
        assert result["errors"] == {CONF_MPRN: "invalid_mprn"}

    @pytest.mark.asyncio
    async def test_user_form_invalid_mprn_format(self, flow):
        """Test user form with non-numeric MPRN."""
        user_input = {
            CONF_USERNAME: "test@example.com",
            CONF_PASSWORD: "test-password",
            CONF_MPRN: "abcdefghijk",  # Not digits
        }

        result = await flow.async_step_user(user_input=user_input)

        assert result["type"] == "form"
        assert result["errors"] == {CONF_MPRN: "invalid_mprn"}

    @pytest.mark.asyncio
    async def test_user_form_duplicate_mprn(self, flow):
        """Test user form with already configured MPRN."""
        existing_entry = MagicMock()
        existing_entry.data = {CONF_MPRN: "12345678901"}
        flow.hass.config_entries.async_entries.return_value = [existing_entry]

        user_input = {
            CONF_USERNAME: "test@example.com",
            CONF_PASSWORD: "test-password",
            CONF_MPRN: "12345678901",
        }

        result = await flow.async_step_user(user_input=user_input)

        assert result["type"] == "form"
        assert result["errors"] == {"base": "mprn_exists"}

    @pytest.mark.asyncio
    async def test_user_form_strips_whitespace(self, flow):
        """Test user form strips whitespace from MPRN."""
        user_input = {
            CONF_USERNAME: "test@example.com",
            CONF_PASSWORD: "test-password",
            CONF_MPRN: " 12345678901 ",  # With whitespace
        }

        with patch.object(flow, "async_set_unique_id"), patch.object(flow, "_abort_if_unique_id_configured"):
            result = await flow.async_step_user(user_input=user_input)

        assert result["type"] == "create_entry"
        assert result["data"][CONF_MPRN] == "12345678901"  # Stripped
