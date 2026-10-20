"""Tests for ESB Smart Meter integration __init__.py."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.esb_smart_meter import (
    _async_reload_on_update,
    async_remove_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.esb_smart_meter.coordinator import ESBRuntimeData
from custom_components.esb_smart_meter.const import (
    CONF_MPRN,
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    DOMAIN,
)


class TestAsyncSetupEntry:
    """Test async_setup_entry function."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {}
        hass.config_entries = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        return hass

    @pytest.fixture
    def mock_entry(self):
        """Create a mock config entry."""
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {
            CONF_USERNAME: "test@example.com",
            CONF_PASSWORD: "test_password",
            CONF_MPRN: "12345678901",
        }
        entry.options = {}
        return entry

    @pytest.mark.asyncio
    async def test_async_setup_entry_success(self, mock_hass, mock_entry):
        """Test successful setup of config entry."""
        with patch(
            "custom_components.esb_smart_meter.create_esb_session"
        ) as mock_create_session, patch(
            "custom_components.esb_smart_meter.ESBDataApi"
        ) as mock_api_class, patch(
            "custom_components.esb_smart_meter.ESBDataUpdateCoordinator"
        ) as mock_coordinator_class:
            # Setup mocks
            mock_session = MagicMock()
            mock_create_session.return_value = mock_session

            mock_api = MagicMock()
            mock_api_class.return_value = mock_api

            mock_coordinator = MagicMock()
            mock_coordinator.async_config_entry_first_refresh = AsyncMock()
            mock_coordinator_class.return_value = mock_coordinator

            # Call function
            result = await async_setup_entry(mock_hass, mock_entry)

            # Assertions
            assert result is True
            assert isinstance(mock_entry.runtime_data, ESBRuntimeData)
            assert DOMAIN not in mock_hass.data

            # Verify API client was created with correct parameters
            mock_api_class.assert_called_once_with(
                hass=mock_hass,
                session=mock_session,
                username="test@example.com",
                password="test_password",
                mprn="12345678901",
            )

            # Verify coordinator was created
            mock_coordinator_class.assert_called_once()
            call_kwargs = mock_coordinator_class.call_args.kwargs
            assert call_kwargs["hass"] == mock_hass
            assert call_kwargs["esb_api"] == mock_api
            assert call_kwargs["mprn"] == "12345678901"
            assert call_kwargs["update_interval"] == timedelta(hours=24)

            # Verify first refresh was called
            mock_coordinator.async_config_entry_first_refresh.assert_called_once()

            # Verify platforms were forwarded
            mock_hass.config_entries.async_forward_entry_setups.assert_called_once_with(
                mock_entry, ["sensor"]
            )

    @pytest.mark.asyncio
    async def test_async_setup_entry_custom_update_interval(self, mock_hass, mock_entry):
        """Test setup with custom update interval from options."""
        mock_entry.options = {CONF_UPDATE_INTERVAL: 12}

        with patch(
            "custom_components.esb_smart_meter.create_esb_session"
        ) as mock_create_session, patch(
            "custom_components.esb_smart_meter.ESBDataApi"
        ), patch(
            "custom_components.esb_smart_meter.ESBDataUpdateCoordinator"
        ) as mock_coordinator_class:
            mock_create_session.return_value = MagicMock()

            mock_coordinator = MagicMock()
            mock_coordinator.async_config_entry_first_refresh = AsyncMock()
            mock_coordinator_class.return_value = mock_coordinator

            result = await async_setup_entry(mock_hass, mock_entry)

            assert result is True
            call_kwargs = mock_coordinator_class.call_args.kwargs
            assert call_kwargs["update_interval"] == timedelta(hours=12)

    @pytest.mark.asyncio
    async def test_async_setup_entry_stores_coordinator_and_session(self, mock_hass, mock_entry):
        """Test that coordinator and session are stored correctly."""
        with patch(
            "custom_components.esb_smart_meter.create_esb_session"
        ) as mock_create_session, patch(
            "custom_components.esb_smart_meter.ESBDataApi"
        ), patch(
            "custom_components.esb_smart_meter.ESBDataUpdateCoordinator"
        ) as mock_coordinator_class:
            mock_session = MagicMock()
            mock_create_session.return_value = mock_session

            mock_coordinator = MagicMock()
            mock_coordinator.async_config_entry_first_refresh = AsyncMock()
            mock_coordinator_class.return_value = mock_coordinator

            await async_setup_entry(mock_hass, mock_entry)

            assert mock_entry.runtime_data.coordinator == mock_coordinator
            assert mock_entry.runtime_data.session == mock_session

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_session(self, mock_hass, mock_entry):
        """Test that a session is created for the entry."""
        with patch(
            "custom_components.esb_smart_meter.create_esb_session"
        ) as mock_create_session, patch(
            "custom_components.esb_smart_meter.ESBDataApi"
        ), patch(
            "custom_components.esb_smart_meter.ESBDataUpdateCoordinator"
        ) as mock_coordinator_class:
            mock_session = MagicMock()
            mock_create_session.return_value = mock_session

            mock_coordinator = MagicMock()
            mock_coordinator.async_config_entry_first_refresh = AsyncMock()
            mock_coordinator_class.return_value = mock_coordinator

            await async_setup_entry(mock_hass, mock_entry)

            mock_create_session.assert_called_once_with(mock_hass)

    @pytest.mark.asyncio
    async def test_async_setup_entry_closes_session_when_first_refresh_fails(self, mock_hass, mock_entry):
        """A failed first refresh must not leak the aiohttp session on every setup retry."""
        with patch(
            "custom_components.esb_smart_meter.create_esb_session"
        ) as mock_create_session, patch(
            "custom_components.esb_smart_meter.ESBDataApi"
        ), patch(
            "custom_components.esb_smart_meter.ESBDataUpdateCoordinator"
        ) as mock_coordinator_class:
            mock_session = MagicMock()
            mock_session.close = AsyncMock()
            mock_create_session.return_value = mock_session
            mock_coordinator = MagicMock()
            mock_coordinator.async_config_entry_first_refresh = AsyncMock(side_effect=ConfigEntryNotReady("down"))
            mock_coordinator_class.return_value = mock_coordinator

            with pytest.raises(ConfigEntryNotReady):
                await async_setup_entry(mock_hass, mock_entry)

            mock_session.close.assert_awaited_once()
            mock_hass.config_entries.async_forward_entry_setups.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_setup_entry_reloads_on_update(self, mock_hass, mock_entry):
        """Option and tariff subentry changes reload the entry so they take effect."""
        with patch("custom_components.esb_smart_meter.create_esb_session"), patch(
            "custom_components.esb_smart_meter.ESBDataApi"
        ), patch("custom_components.esb_smart_meter.ESBDataUpdateCoordinator") as mock_coordinator_class:
            mock_coordinator_class.return_value.async_config_entry_first_refresh = AsyncMock()
            await async_setup_entry(mock_hass, mock_entry)

        mock_entry.add_update_listener.assert_called_once_with(_async_reload_on_update)

    @pytest.mark.asyncio
    async def test_reload_on_update(self, mock_hass, mock_entry):
        mock_hass.config_entries.async_reload = AsyncMock()
        await _async_reload_on_update(mock_hass, mock_entry)
        mock_hass.config_entries.async_reload.assert_awaited_once_with(mock_entry.entry_id)


class TestAsyncUnloadEntry:
    """Test async_unload_entry function."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock()
        hass.config_entries = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        return hass

    @staticmethod
    def _entry(session):
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.runtime_data = ESBRuntimeData(coordinator=MagicMock(), session=session)
        return entry

    @staticmethod
    def _session(*, closed=False, close_error=None):
        session = MagicMock()
        session.closed = closed
        session.close = AsyncMock(side_effect=close_error)
        return session

    @pytest.mark.asyncio
    async def test_async_unload_entry_success(self, mock_hass):
        """Test successful unload closes the entry's session."""
        session = self._session()
        entry = self._entry(session)

        result = await async_unload_entry(mock_hass, entry)

        assert result is True
        mock_hass.config_entries.async_unload_platforms.assert_called_once_with(entry, ["sensor"])
        session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_unload_entry_session_already_closed(self, mock_hass):
        """Test unload when session is already closed."""
        session = self._session(closed=True)

        assert await async_unload_entry(mock_hass, self._entry(session)) is True
        session.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_unload_entry_session_close_error(self, mock_hass):
        """Test unload handles session close errors gracefully."""
        session = self._session(close_error=Exception("Close error"))

        with patch("custom_components.esb_smart_meter._LOGGER") as mock_logger:
            result = await async_unload_entry(mock_hass, self._entry(session))

        assert result is True
        mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_unload_entry_unload_fails(self, mock_hass):
        """Session stays open if the platforms fail to unload."""
        mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
        session = self._session()

        assert await async_unload_entry(mock_hass, self._entry(session)) is False
        session.close.assert_not_called()


class TestIntegrationSetup:
    """Test integration-level setup scenarios."""

    @pytest.mark.asyncio
    async def test_multiple_entries_setup(self):
        """Test setting up multiple config entries."""
        hass = MagicMock()
        hass.data = {}
        hass.config_entries = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock()

        entry1 = MagicMock()
        entry1.entry_id = "entry1"
        entry1.data = {
            CONF_USERNAME: "user1@example.com",
            CONF_PASSWORD: "pass1",
            CONF_MPRN: "11111111111",
        }
        entry1.options = {}

        entry2 = MagicMock()
        entry2.entry_id = "entry2"
        entry2.data = {
            CONF_USERNAME: "user2@example.com",
            CONF_PASSWORD: "pass2",
            CONF_MPRN: "22222222222",
        }
        entry2.options = {}

        with patch("custom_components.esb_smart_meter.create_esb_session") as mock_create_session, patch(
            "custom_components.esb_smart_meter.ESBDataApi"
        ), patch("custom_components.esb_smart_meter.ESBDataUpdateCoordinator") as mock_coordinator_class:
            # Create different mocks for each entry
            mock_session1 = MagicMock()
            mock_session2 = MagicMock()
            mock_create_session.side_effect = [mock_session1, mock_session2]

            mock_coordinator1 = MagicMock()
            mock_coordinator1.async_config_entry_first_refresh = AsyncMock()
            mock_coordinator2 = MagicMock()
            mock_coordinator2.async_config_entry_first_refresh = AsyncMock()
            mock_coordinator_class.side_effect = [mock_coordinator1, mock_coordinator2]

            # Setup both entries
            result1 = await async_setup_entry(hass, entry1)
            result2 = await async_setup_entry(hass, entry2)

            assert result1 is True
            assert result2 is True
            assert entry1.runtime_data.session == mock_session1
            assert entry2.runtime_data.session == mock_session2
            assert entry1.runtime_data.coordinator == mock_coordinator1
            assert entry2.runtime_data.coordinator == mock_coordinator2


@pytest.mark.asyncio
async def test_remove_entry_deletes_import_state():
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry1"
    with patch("custom_components.esb_smart_meter.ImportState") as state_cls:
        state_cls.return_value.async_remove = AsyncMock()
        await async_remove_entry(hass, entry)

    assert [c.args for c in state_cls.call_args_list] == [(hass, "entry1", "usage_cost"), (hass, "entry1", "export_credit")]
