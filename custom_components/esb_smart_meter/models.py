"""Data models for ESB Smart Meter integration."""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from .const import CSV_COLUMN_DATE, CSV_COLUMN_VALUE, CSV_DATE_FORMAT, MAX_DATA_AGE_DAYS

_LOGGER = logging.getLogger(__name__)


class ESBData:
    """Class to manipulate data retrieved from ESB with memory optimization."""

    def __init__(self, *, data: List[Dict[str, Any]]) -> None:
        """Initialize with raw CSV data, filtering old data to prevent memory leaks."""
        # Validate CSV structure
        if data:
            if not self._validate_csv_structure(data[0]):
                _LOGGER.error("CSV validation failed. First row keys: %s", list(data[0].keys()))
                _LOGGER.error("Expected columns: %s, %s", CSV_COLUMN_DATE, CSV_COLUMN_VALUE)
                _LOGGER.error("First row data: %s", data[0])
                raise ValueError(f"Invalid CSV structure. Expected columns: " f"{CSV_COLUMN_DATE}, {CSV_COLUMN_VALUE}")

        # Filter out data older than MAX_DATA_AGE_DAYS to prevent memory leaks
        cutoff_date = datetime.now() - timedelta(days=MAX_DATA_AGE_DAYS)
        self._data: List[Tuple[datetime, float]] = self._filter_and_parse_data(data, cutoff_date)
        _LOGGER.debug(
            "Loaded %d rows of data (filtered data older than %d days)",
            len(self._data),
            MAX_DATA_AGE_DAYS,
        )

    @staticmethod
    def _validate_csv_structure(row: dict[str, Any]) -> bool:
        """Validate that required CSV columns exist."""
        required_columns = [CSV_COLUMN_DATE, CSV_COLUMN_VALUE]
        available_columns = list(row.keys())
        has_required = all(col in row for col in required_columns)

        if not has_required:
            _LOGGER.error("CSV validation failed. Required: %s, Available: %s", required_columns, available_columns)

        return has_required

    def _filter_and_parse_data(self, data: list[dict[str, Any]], cutoff_date: datetime) -> list[tuple[datetime, float]]:
        """Filter old data and pre-parse for performance."""
        parsed_data = []
        for row in data:
            try:
                timestamp = datetime.strptime(row[CSV_COLUMN_DATE], CSV_DATE_FORMAT)
                if timestamp >= cutoff_date:
                    value = float(row[CSV_COLUMN_VALUE])
                    parsed_data.append((timestamp, value))
            except (ValueError, KeyError) as err:
                _LOGGER.warning("Skipping invalid row: %s", err)
                continue
        return parsed_data

    def __sum_data_since(self, *, since: datetime) -> float:
        """Sum energy usage since a specific datetime (optimized)."""
        return sum(value for timestamp, value in self._data if timestamp >= since)

    @property
    def today(self) -> float:
        """Get today's usage."""
        return self.__sum_data_since(since=datetime.now().replace(hour=0, minute=0, second=0, microsecond=0))

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
        return self.__sum_data_since(since=datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0))

    @property
    def last_30_days(self) -> float:
        """Get last 30 days usage."""
        return self.__sum_data_since(since=datetime.now() - timedelta(days=30))
