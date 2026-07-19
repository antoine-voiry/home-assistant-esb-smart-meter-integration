"""Data models for ESB Smart Meter integration."""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from .const import (
    CSV_COLUMN_DATE,
    CSV_COLUMN_READ_TYPE,
    CSV_COLUMN_VALUE,
    CSV_DATE_FORMAT,
    MAX_DATA_AGE_DAYS,
    READ_TYPE_EXPORT,
    READ_TYPE_IMPORT,
)

_LOGGER = logging.getLogger(__name__)


class ESBData:
    """Class to manipulate data retrieved from ESB with memory optimization."""

    def __init__(self, *, data: List[Dict[str, Any]]) -> None:
        """Initialize with raw CSV data, filtering old data to prevent memory leaks."""
        # Validate CSV structure
        if data:
            if not self._validate_csv_structure(data[0]):
                _LOGGER.error("CSV validation failed. First row keys: %s", list(data[0].keys()))
                _LOGGER.error(
                    "Expected columns: %s, %s, %s", CSV_COLUMN_DATE, CSV_COLUMN_VALUE, CSV_COLUMN_READ_TYPE
                )
                _LOGGER.error("First row data: %s", data[0])
                raise ValueError(
                    f"Invalid CSV structure. Expected columns: "
                    f"{CSV_COLUMN_DATE}, {CSV_COLUMN_VALUE}, {CSV_COLUMN_READ_TYPE}"
                )

        # Filter out data older than MAX_DATA_AGE_DAYS to prevent memory leaks
        cutoff_date = datetime.now() - timedelta(days=MAX_DATA_AGE_DAYS)
        self._data, self._export_data = self._filter_and_parse_data(data, cutoff_date)
        _LOGGER.debug(
            "Loaded %d import / %d export rows (filtered data older than %d days)",
            len(self._data),
            len(self._export_data),
            MAX_DATA_AGE_DAYS,
        )

    @staticmethod
    def _validate_csv_structure(row: dict[str, Any]) -> bool:
        """Validate that required CSV columns exist."""
        required_columns = [CSV_COLUMN_DATE, CSV_COLUMN_VALUE, CSV_COLUMN_READ_TYPE]
        available_columns = list(row.keys())
        has_required = all(col in row for col in required_columns)

        if not has_required:
            _LOGGER.error("CSV validation failed. Required: %s, Available: %s", required_columns, available_columns)

        return has_required

    def _filter_and_parse_data(
        self, data: list[dict[str, Any]], cutoff_date: datetime
    ) -> tuple[list[tuple[datetime, float]], list[tuple[datetime, float]]]:
        """Filter old data and split rows into import and export series."""
        import_data: list[tuple[datetime, float]] = []
        export_data: list[tuple[datetime, float]] = []
        for row in data:
            try:
                timestamp = datetime.strptime(row[CSV_COLUMN_DATE], CSV_DATE_FORMAT)
                if timestamp < cutoff_date:
                    continue
                value = float(row[CSV_COLUMN_VALUE])
                read_type = str(row.get(CSV_COLUMN_READ_TYPE, "")).strip()
                if read_type == READ_TYPE_IMPORT:
                    import_data.append((timestamp, value))
                elif read_type == READ_TYPE_EXPORT:
                    export_data.append((timestamp, value))
                else:
                    raise ValueError(f"Unrecognized Read Type in row: {row}")
            except (ValueError, KeyError) as err:
                _LOGGER.warning("Skipping invalid row: %s", err)
                continue
        return import_data, export_data

    @staticmethod
    def _sum_since(series: list[tuple[datetime, float]], since: datetime) -> float:
        """Sum values in a series since a specific datetime."""
        return sum(value for timestamp, value in series if timestamp >= since)

    @staticmethod
    def _readings_since(series: list[tuple[datetime, float]], since: datetime) -> list[dict[str, Any]]:
        """Return readings in a series since a specific datetime."""
        if since.tzinfo is not None:
            since = since.replace(tzinfo=None)
        return [
            {"timestamp": timestamp.isoformat(), "value": value}
            for timestamp, value in series
            if timestamp >= since
        ]

    @staticmethod
    def start_of_today() -> datetime:
        return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def since_24_hours() -> datetime:
        return datetime.now() - timedelta(days=1)

    @staticmethod
    def start_of_week() -> datetime:
        now = datetime.now()
        return now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())

    @staticmethod
    def since_7_days() -> datetime:
        return datetime.now() - timedelta(days=7)

    @staticmethod
    def start_of_month() -> datetime:
        return datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def since_30_days() -> datetime:
        return datetime.now() - timedelta(days=30)

    def get_readings_since(self, *, since: datetime) -> list[dict[str, Any]]:
        """Get import readings since a specific datetime."""
        return self._readings_since(self._data, since)

    def get_export_readings_since(self, *, since: datetime) -> list[dict[str, Any]]:
        """Get export readings since a specific datetime."""
        return self._readings_since(self._export_data, since)

    @property
    def today(self) -> float:
        """Get today's usage."""
        return self._sum_since(self._data, self.start_of_today())

    @property
    def last_24_hours(self) -> float:
        """Get last 24 hours usage."""
        return self._sum_since(self._data, self.since_24_hours())

    @property
    def this_week(self) -> float:
        """Get this week's usage."""
        return self._sum_since(self._data, self.start_of_week())

    @property
    def last_7_days(self) -> float:
        """Get last 7 days usage."""
        return self._sum_since(self._data, self.since_7_days())

    @property
    def this_month(self) -> float:
        """Get this month's usage."""
        return self._sum_since(self._data, self.start_of_month())

    @property
    def last_30_days(self) -> float:
        """Get last 30 days usage."""
        return self._sum_since(self._data, self.since_30_days())

    @property
    def export_today(self) -> float:
        """Get today's export."""
        return self._sum_since(self._export_data, self.start_of_today())

    @property
    def export_last_24_hours(self) -> float:
        """Get last 24 hours export."""
        return self._sum_since(self._export_data, self.since_24_hours())

    @property
    def export_this_week(self) -> float:
        """Get this week's export."""
        return self._sum_since(self._export_data, self.start_of_week())

    @property
    def export_last_7_days(self) -> float:
        """Get last 7 days export."""
        return self._sum_since(self._export_data, self.since_7_days())

    @property
    def export_this_month(self) -> float:
        """Get this month's export."""
        return self._sum_since(self._export_data, self.start_of_month())

    @property
    def export_last_30_days(self) -> float:
        """Get last 30 days export."""
        return self._sum_since(self._export_data, self.since_30_days())
