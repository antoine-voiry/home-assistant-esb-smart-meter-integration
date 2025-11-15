"""Tests for ESB Smart Meter integration __init__.py."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from custom_components.esb_smart_meter import (
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.esb_smart_meter.const import (
    CONF_MPRN,
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    DOMAIN,
)


class TestAsyncSetup:
    """Test async_setup function."""

    @pytest.mark.asyncio
    async def test_async_setup_initializes_domain_data(self):
        """Test that async_setup initializes domain data."""
        hass = MagicMock()
        hass.data = {}
        config = {}

        result = await async_setup(hass, config)

        assert result is True
        assert DOMAIN in hass.data
        assert hass.data[DOMAIN] == {}

    @pytest.mark.asyncio
    async def test_async_setup_preserves_existing_data(self):
        """Test that async_setup doesn't overwrite existing domain data."""
        hass = MagicMock()
        existing_data = {"existing": "data"}
        hass.data = {DOMAIN: existing_data}
        config = {}

        result = await async_setup(hass, config)

        assert result is True
        assert hass.data[DOMAIN] == existing_data


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
            assert DOMAIN in mock_hass.data
            assert mock_entry.entry_id in mock_hass.data[DOMAIN]
            assert "coordinator" in mock_hass.data[DOMAIN][mock_entry.entry_id]
            assert "session" in mock_hass.data[DOMAIN][mock_entry.entry_id]

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

            stored_data = mock_hass.data[DOMAIN][mock_entry.entry_id]
            assert stored_data["coordinator"] == mock_coordinator
            assert stored_data["session"] == mock_session

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


class TestAsyncUnloadEntry:
    """Test async_unload_entry function."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock()
        hass.data = {DOMAIN: {}}
        hass.config_entries = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        return hass

    @pytest.fixture
    def mock_entry(self):
        """Create a mock config entry."""
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        return entry

    @pytest.mark.asyncio
    async def test_async_unload_entry_success(self, mock_hass, mock_entry):
        """Test successful unload of config entry."""
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()

        mock_hass.data[DOMAIN][mock_entry.entry_id] = {
            "coordinator": MagicMock(),
            "session": mock_session,
        }

        result = await async_unload_entry(mock_hass, mock_entry)

        assert result is True
        mock_hass.config_entries.async_unload_platforms.assert_called_once_with(
            mock_entry, ["sensor"]
        )
        mock_session.close.assert_called_once()
        assert mock_entry.entry_id not in mock_hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_async_unload_entry_no_session(self, mock_hass, mock_entry):
        """Test unload when no session exists."""
        mock_hass.data[DOMAIN][mock_entry.entry_id] = {
            "coordinator": MagicMock(),
        }

        result = await async_unload_entry(mock_hass, mock_entry)

        assert result is True
        assert mock_entry.entry_id not in mock_hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_async_unload_entry_session_already_closed(self, mock_hass, mock_entry):
        """Test unload when session is already closed."""
        mock_session = MagicMock()
        mock_session.closed = True
        mock_session.close = AsyncMock()

        mock_hass.data[DOMAIN][mock_entry.entry_id] = {
            "coordinator": MagicMock(),
            "session": mock_session,
        }

        result = await async_unload_entry(mock_hass, mock_entry)

        assert result is True
        mock_session.close.assert_not_called()
        assert mock_entry.entry_id not in mock_hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_async_unload_entry_session_close_error(self, mock_hass, mock_entry):
        """Test unload handles session close errors gracefully."""
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock(side_effect=Exception("Close error"))

        mock_hass.data[DOMAIN][mock_entry.entry_id] = {
            "coordinator": MagicMock(),
            "session": mock_session,
        }

        with patch("custom_components.esb_smart_meter._LOGGER") as mock_logger:
            result = await async_unload_entry(mock_hass, mock_entry)

            assert result is True
            mock_logger.warning.assert_called_once()
            assert mock_entry.entry_id not in mock_hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_async_unload_entry_no_entry_data(self, mock_hass, mock_entry):
        """Test unload when entry has no data."""
        result = await async_unload_entry(mock_hass, mock_entry)

        assert result is True
        mock_hass.config_entries.async_unload_platforms.assert_called_once_with(
            mock_entry, ["sensor"]
        )

    @pytest.mark.asyncio
    async def test_async_unload_entry_unload_fails(self, mock_hass, mock_entry):
        """Test unload when platform unload fails."""
        mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()

        mock_hass.data[DOMAIN][mock_entry.entry_id] = {
            "coordinator": MagicMock(),
            "session": mock_session,
        }

        result = await async_unload_entry(mock_hass, mock_entry)

        assert result is False
        # Session should not be closed if unload fails
        mock_session.close.assert_not_called()
        # Entry data should still exist
        assert mock_entry.entry_id in mock_hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_async_unload_entry_empty_entry_data(self, mock_hass, mock_entry):
        """Test unload with empty entry data dictionary."""
        mock_hass.data[DOMAIN][mock_entry.entry_id] = {}

        result = await async_unload_entry(mock_hass, mock_entry)

        assert result is True
        assert mock_entry.entry_id not in mock_hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_async_unload_entry_preserves_other_entries(self, mock_hass, mock_entry):
        """Test that unload doesn't affect other entries."""
        other_entry_id = "other_entry_id"
        other_entry_data = {"coordinator": MagicMock(), "session": MagicMock()}

        mock_hass.data[DOMAIN][mock_entry.entry_id] = {
            "coordinator": MagicMock(),
            "session": MagicMock(),
        }
        mock_hass.data[DOMAIN][other_entry_id] = other_entry_data

        result = await async_unload_entry(mock_hass, mock_entry)

        assert result is True
        assert mock_entry.entry_id not in mock_hass.data[DOMAIN]
        assert other_entry_id in mock_hass.data[DOMAIN]
        assert mock_hass.data[DOMAIN][other_entry_id] == other_entry_data


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
            assert "entry1" in hass.data[DOMAIN]
            assert "entry2" in hass.data[DOMAIN]
            assert hass.data[DOMAIN]["entry1"]["session"] == mock_session1
            assert hass.data[DOMAIN]["entry2"]["session"] == mock_session2
            assert hass.data[DOMAIN]["entry1"]["coordinator"] == mock_coordinator1
            assert hass.data[DOMAIN]["entry2"]["coordinator"] == mock_coordinator2
