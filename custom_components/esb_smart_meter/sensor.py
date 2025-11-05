"""Support for ESB Smart Meter sensors."""
import asyncio
import csv
import json
import logging
import re
from abc import abstractmethod
from datetime import datetime, timedelta
from io import StringIO
from typing import Any
from urllib.parse import quote

import aiohttp
from bs4 import BeautifulSoup

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_MPRN,
    CONF_PASSWORD,
    CONF_USERNAME,
    CSV_COLUMN_DATE,
    CSV_COLUMN_VALUE,
    CSV_DATE_FORMAT,
    DEFAULT_MAX_RETRIES,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    ESB_AUTH_BASE_URL,
    ESB_DATA_URL,
    ESB_LOGIN_URL,
    MANUFACTURER,
    MAX_CSV_SIZE_MB,
    MAX_DATA_AGE_DAYS,
    MODEL,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ESB Smart Meter sensor based on a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    mprn = entry.data[CONF_MPRN]

    session = async_get_clientsession(hass)
    esb_api = ESBCachingApi(
        ESBDataApi(
            hass=hass,
            session=session,
            username=username,
            password=password,
            mprn=mprn,
        )
    )

    sensors = [
        TodaySensor(esb_api=esb_api, mprn=mprn, name="ESB Electricity Usage: Today"),
        Last24HoursSensor(esb_api=esb_api, mprn=mprn, name="ESB Electricity Usage: Last 24 Hours"),
        ThisWeekSensor(esb_api=esb_api, mprn=mprn, name="ESB Electricity Usage: This Week"),
        Last7DaysSensor(esb_api=esb_api, mprn=mprn, name="ESB Electricity Usage: Last 7 Days"),
        ThisMonthSensor(esb_api=esb_api, mprn=mprn, name="ESB Electricity Usage: This Month"),
        Last30DaysSensor(esb_api=esb_api, mprn=mprn, name="ESB Electricity Usage: Last 30 Days"),
    ]

    async_add_entities(sensors, True)


class BaseSensor(SensorEntity):
    """Base sensor class for ESB Smart Meter sensors."""

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:flash"

    def __init__(self, *, esb_api: "ESBCachingApi", mprn: str, name: str) -> None:
        """Initialize the sensor."""
        self._esb_api = esb_api
        self._mprn = mprn
        self._attr_name = name
        self._attr_available = True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._mprn)},
            name=f"ESB Smart Meter ({self._mprn})",
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @abstractmethod
    def _get_data(self, *, esb_data: "ESBData") -> float:
        """Get the data for this sensor."""

    async def async_update(self) -> None:
        """Update the sensor state."""
        try:
            esb_data = await self._esb_api.fetch()
            if esb_data:
                self._attr_native_value = self._get_data(esb_data=esb_data)
                self._attr_available = True
            else:
                self._attr_available = False
        except Exception as err:
            _LOGGER.error("Failed to update %s: %s", self._attr_name, err)
            self._attr_available = False


class TodaySensor(BaseSensor):
    """Sensor for today's electricity usage."""

    def __init__(self, *, esb_api: "ESBCachingApi", mprn: str, name: str) -> None:
        """Initialize the sensor."""
        super().__init__(esb_api=esb_api, mprn=mprn, name=name)
        self._attr_unique_id = f"{mprn}_today"

    def _get_data(self, *, esb_data: "ESBData") -> float:
        """Get today's data."""
        return esb_data.today


class Last24HoursSensor(BaseSensor):
    """Sensor for last 24 hours electricity usage."""

    def __init__(self, *, esb_api: "ESBCachingApi", mprn: str, name: str) -> None:
        """Initialize the sensor."""
        super().__init__(esb_api=esb_api, mprn=mprn, name=name)
        self._attr_unique_id = f"{mprn}_last_24_hours"

    def _get_data(self, *, esb_data: "ESBData") -> float:
        """Get last 24 hours data."""
        return esb_data.last_24_hours


class ThisWeekSensor(BaseSensor):
    """Sensor for this week's electricity usage."""

    def __init__(self, *, esb_api: "ESBCachingApi", mprn: str, name: str) -> None:
        """Initialize the sensor."""
        super().__init__(esb_api=esb_api, mprn=mprn, name=name)
        self._attr_unique_id = f"{mprn}_this_week"

    def _get_data(self, *, esb_data: "ESBData") -> float:
        """Get this week's data."""
        return esb_data.this_week


class Last7DaysSensor(BaseSensor):
    """Sensor for last 7 days electricity usage."""

    def __init__(self, *, esb_api: "ESBCachingApi", mprn: str, name: str) -> None:
        """Initialize the sensor."""
        super().__init__(esb_api=esb_api, mprn=mprn, name=name)
        self._attr_unique_id = f"{mprn}_last_7_days"

    def _get_data(self, *, esb_data: "ESBData") -> float:
        """Get last 7 days data."""
        return esb_data.last_7_days


class ThisMonthSensor(BaseSensor):
    """Sensor for this month's electricity usage."""

    def __init__(self, *, esb_api: "ESBCachingApi", mprn: str, name: str) -> None:
        """Initialize the sensor."""
        super().__init__(esb_api=esb_api, mprn=mprn, name=name)
        self._attr_unique_id = f"{mprn}_this_month"

    def _get_data(self, *, esb_data: "ESBData") -> float:
        """Get this month's data."""
        return esb_data.this_month


class Last30DaysSensor(BaseSensor):
    """Sensor for last 30 days electricity usage."""

    def __init__(self, *, esb_api: "ESBCachingApi", mprn: str, name: str) -> None:
        """Initialize the sensor."""
        super().__init__(esb_api=esb_api, mprn=mprn, name=name)
        self._attr_unique_id = f"{mprn}_last_30_days"

    def _get_data(self, *, esb_data: "ESBData") -> float:
        """Get last 30 days data."""
        return esb_data.last_30_days


class ESBData:
    """Class to manipulate data retrieved from ESB with memory optimization."""

    def __init__(self, *, data: list[dict[str, Any]]) -> None:
        """Initialize with raw CSV data, filtering old data to prevent memory leaks."""
        # Validate CSV structure
        if data and not self._validate_csv_structure(data[0]):
            raise ValueError(
                f"Invalid CSV structure. Expected columns: "
                f"{CSV_COLUMN_DATE}, {CSV_COLUMN_VALUE}"
            )

        # Filter out data older than MAX_DATA_AGE_DAYS to prevent memory leaks
        cutoff_date = datetime.now() - timedelta(days=MAX_DATA_AGE_DAYS)
        self._data = self._filter_and_parse_data(data, cutoff_date)
        _LOGGER.debug(
            "Loaded %d rows of data (filtered data older than %d days)",
            len(self._data), MAX_DATA_AGE_DAYS
        )

    @staticmethod
    def _validate_csv_structure(row: dict[str, Any]) -> bool:
        """Validate that required CSV columns exist."""
        return CSV_COLUMN_DATE in row and CSV_COLUMN_VALUE in row

    def _filter_and_parse_data(
        self, data: list[dict[str, Any]], cutoff_date: datetime
    ) -> list[tuple[datetime, float]]:
        """Filter old data and pre-parse for performance."""
        parsed_data = []
        for row in data:
            try:
                timestamp = datetime.strptime(
                    row[CSV_COLUMN_DATE], CSV_DATE_FORMAT
                )
                if timestamp >= cutoff_date:
                    value = float(row[CSV_COLUMN_VALUE])
                    parsed_data.append((timestamp, value))
            except (ValueError, KeyError) as err:
                _LOGGER.warning("Skipping invalid row: %s", err)
                continue
        return parsed_data

    def __sum_data_since(self, *, since: datetime) -> float:
        """Sum energy usage since a specific datetime (optimized)."""
        return sum(
            value for timestamp, value in self._data if timestamp >= since
        )

    @property
    def today(self) -> float:
        """Get today's usage."""
        return self.__sum_data_since(
            since=datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        )

    @property
    def last_24_hours(self) -> float:
        """Get last 24 hours usage."""
        return self.__sum_data_since(since=datetime.now() - timedelta(days=1))

    @property
    def this_week(self) -> float:
        """Get this week's usage."""
        return self.__sum_data_since(
            since=datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(days=datetime.now().weekday())
        )

    @property
    def last_7_days(self) -> float:
        """Get last 7 days usage."""
        return self.__sum_data_since(since=datetime.now() - timedelta(days=7))

    @property
    def this_month(self) -> float:
        """Get this month's usage."""
        return self.__sum_data_since(
            since=datetime.now().replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
        )

    @property
    def last_30_days(self) -> float:
        """Get last 30 days usage."""
        return self.__sum_data_since(since=datetime.now() - timedelta(days=30))


class ESBCachingApi:
    """Caching layer to avoid polling ESB constantly."""

    def __init__(self, esb_api: "ESBDataApi") -> None:
        """Initialize the caching API."""
        self._esb_api = esb_api
        self._cached_data: ESBData | None = None
        self._cached_data_timestamp: datetime | None = None

    async def fetch(self) -> ESBData | None:
        """Fetch data with caching."""
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


class ESBDataApi:
    """Class for handling the data retrieval from ESB using async aiohttp."""

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        mprn: str,
    ) -> None:
        """Initialize the data object."""
        self._hass = hass
        self._session = session
        self._username = username
        self._password = password
        self._mprn = mprn
        self._timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)

    async def __login(self) -> dict[str, str]:
        """Login to ESB and return cookies."""
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0"
        }

        try:
            # Get CSRF token and settings
            _LOGGER.debug("Getting CSRF token from ESB")
            async with self._session.get(
                ESB_LOGIN_URL,
                headers=headers,
                allow_redirects=True,
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()
                content = await response.text()
                settings_match = re.findall(r"(?<=var SETTINGS = )\S*;", content)
                if not settings_match:
                    raise ValueError("Could not find SETTINGS in ESB login page")
                settings = json.loads(settings_match[0][:-1])

                # Validate required settings fields
                if "csrf" not in settings or "transId" not in settings:
                    raise ValueError("Missing required authentication tokens")

                _LOGGER.debug("Got CSRF token and transaction ID")

            # Update headers with CSRF token
            headers["x-csrf-token"] = settings["csrf"]

            # Login - Note: password is intentionally not logged
            login_url = (
                f"{ESB_AUTH_BASE_URL}/SelfAsserted?"
                f"tx={settings['transId']}&p=B2C_1A_signup_signin"
            )
            login_data = {
                "signInName": self._username,
                "password": self._password,
                "request_type": "RESPONSE",
            }
            _LOGGER.debug(
                "Submitting login credentials for user: %s", self._username
            )
            async with self._session.post(
                login_url,
                data=login_data,
                headers=headers,
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()
                _LOGGER.debug("Login successful")

            # Confirm login
            confirm_params = {
                "rememberMe": False,
                "csrf_token": settings["csrf"],
                "tx": settings["transId"],
                "p": "B2C_1A_signup_signin",
            }
            confirm_url = (
                f"{ESB_AUTH_BASE_URL}/api/CombinedSigninAndSignup/confirmed"
            )
            _LOGGER.debug("Confirming login")
            async with self._session.get(
                confirm_url,
                params=confirm_params,
                headers=headers,
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()
                content = await response.text()
                soup = BeautifulSoup(content, "html.parser")
                form = soup.find("form", {"id": "auto"})
                if not form:
                    raise ValueError("Could not find auto-submit form in ESB response")

                # Safely extract form fields with null checks
                state_input = form.find("input", {"name": "state"})
                client_info_input = form.find("input", {"name": "client_info"})
                code_input = form.find("input", {"name": "code"})

                if (not state_input or not client_info_input
                        or not code_input):
                    raise ValueError(
                        "Missing required form fields in ESB response"
                    )

                state = state_input.get("value")
                client_info = client_info_input.get("value")
                code = code_input.get("value")
                action_url = form.get("action")

                if not all([state, client_info, code, action_url]):
                    raise ValueError("Empty values in required form fields")

                _LOGGER.debug("Extracted form data")

            # Submit final form
            final_data = {
                "state": state,
                "client_info": client_info,
                "code": code,
            }
            _LOGGER.debug("Submitting final authentication form")
            async with self._session.post(
                action_url,
                data=final_data,
                headers=headers,
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()
                _LOGGER.info(
                    "Authentication completed successfully for user: %s",
                    self._username
                )
                return dict(
                    self._session.cookie_jar.filter_cookies(ESB_LOGIN_URL)
                )

        except aiohttp.ClientError as err:
            _LOGGER.error("Network error during login: %s", err)
            raise
        except json.JSONDecodeError as err:
            _LOGGER.error("Invalid JSON in ESB response: %s", err)
            raise ValueError(
                "Invalid authentication response from ESB"
            ) from err
        except (KeyError, ValueError, AttributeError) as err:
            _LOGGER.error("Error parsing ESB response: %s", err)
            raise

    async def __fetch_data(self) -> str:
        """Fetch the power usage data from ESB with size limits."""
        try:
            # URL encode MPRN to prevent injection attacks
            encoded_mprn = quote(self._mprn, safe='')
            data_url = f"{ESB_DATA_URL}?mprn={encoded_mprn}"
            _LOGGER.debug("Fetching CSV data for MPRN %s", self._mprn)

            async with self._session.get(
                data_url,
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()

                # Check content size to prevent memory exhaustion
                content_length = response.headers.get('Content-Length')
                if content_length:
                    size_mb = int(content_length) / (1024 * 1024)
                    if size_mb > MAX_CSV_SIZE_MB:
                        raise ValueError(
                            f"CSV response too large: {size_mb:.2f}MB "
                            f"exceeds {MAX_CSV_SIZE_MB}MB limit"
                        )

                csv_data = await response.text()

                # Double-check actual size after download
                actual_size_mb = len(csv_data.encode('utf-8')) / (1024 * 1024)
                if actual_size_mb > MAX_CSV_SIZE_MB:
                    raise ValueError(
                        f"CSV data too large: {actual_size_mb:.2f}MB "
                        f"exceeds {MAX_CSV_SIZE_MB}MB limit"
                    )

                _LOGGER.debug(
                    "CSV data fetched successfully (%.2f MB)", actual_size_mb
                )
                return csv_data

        except aiohttp.ClientError as err:
            _LOGGER.error("Error fetching data: %s", err)
            raise

    def __csv_to_dict(self, csv_data: str) -> list[dict[str, Any]]:
        """Convert CSV data to list of dictionaries."""
        try:
            reader = csv.DictReader(StringIO(csv_data))
            data = list(reader)
            _LOGGER.debug("Parsed %d rows from CSV data", len(data))
            return data
        except Exception as err:
            _LOGGER.error("Error parsing CSV data: %s", err)
            raise

    async def fetch(self) -> ESBData:
        """Fetch data with retry logic and proper error handling."""
        last_error = None
        for attempt in range(DEFAULT_MAX_RETRIES):
            try:
                _LOGGER.debug("Fetch attempt %d of %d", attempt + 1, DEFAULT_MAX_RETRIES)
                await self.__login()
                csv_data = await self.__fetch_data()
                data = await self._hass.async_add_executor_job(
                    self.__csv_to_dict, csv_data
                )
                return ESBData(data=data)
            except ValueError as err:
                # Don't retry on data validation errors (invalid CSV, size limits, etc)
                _LOGGER.error("Data validation error: %s", err)
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                # Retry on network errors
                last_error = err
                if attempt < DEFAULT_MAX_RETRIES - 1:
                    wait_time = 2**attempt
                    _LOGGER.warning(
                        "Fetch attempt %d failed: %s. Retrying in %d seconds...",
                        attempt + 1,
                        err,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                else:
                    _LOGGER.error(
                        "All %d fetch attempts failed. Last error: %s",
                        DEFAULT_MAX_RETRIES,
                        err,
                    )
            except Exception as err:
                # Log and re-raise unexpected errors immediately
                _LOGGER.error("Unexpected error during fetch: %s", err, exc_info=True)
                raise

        if last_error:
            raise last_error
        raise RuntimeError("Failed to fetch data after all retry attempts")
