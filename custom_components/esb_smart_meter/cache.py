"""
DEPRECATED: Caching layer for ESB Smart Meter integration.

This module is deprecated and replaced by the DataUpdateCoordinator pattern.
It is kept for backward compatibility but should not be used in new code.

Use coordinator.ESBDataUpdateCoordinator instead.
"""

import asyncio
import logging
from datetime import datetime

from .const import DEFAULT_SCAN_INTERVAL
from .models import ESBData

_LOGGER = logging.getLogger(__name__)


class ESBCachingApi:
    """Caching layer to avoid polling ESB constantly."""

    def __init__(self, esb_api: "ESBDataApi") -> None:  # noqa: F821
        """Initialize the caching API."""
        self._esb_api = esb_api
        self._cached_data: ESBData | None = None
        self._cached_data_timestamp: datetime | None = None
        self._fetch_lock = asyncio.Lock()

    async def fetch(self) -> ESBData | None:
        """Fetch data with caching. Thread-safe to prevent multiple simultaneous fetches."""
        # Use lock to ensure only one fetch happens at a time
        async with self._fetch_lock:
            # Check again inside the lock in case another call already fetched
            if (
                self._cached_data_timestamp is None
                or self._cached_data_timestamp < datetime.now() - DEFAULT_SCAN_INTERVAL
            ):
                try:
                    _LOGGER.debug("Fetching new data from ESB")
                    self._cached_data = await self._esb_api.fetch()
                    self._cached_data_timestamp = datetime.now()
                    _LOGGER.info("Successfully fetched data from ESB")
                except Exception as err:
                    _LOGGER.error("Error fetching data: %s", err)
                    self._cached_data = None
                    self._cached_data_timestamp = None
                    raise
            else:
                _LOGGER.debug("Using cached data")

        return self._cached_data
