"""Tests for ESB Data manipulation."""

from datetime import datetime, timedelta

import pytest

from custom_components.esb_smart_meter.models import ESBData


class TestESBData:
    """Test ESBData class."""

    @pytest.fixture
    def sample_data(self):
        """Create sample data for testing."""
        now = datetime.now()
        data = []

        # Add 100 days of data (some will be filtered out)
        for i in range(100):
            date = now - timedelta(days=i)
            date_str = date.strftime("%d-%m-%Y %H:%M")
            data.append(
                {
                    "Read Date and End Time": date_str,
                    "Read Value": "1.5",
                    "Read Type": "Active Import Interval (kWh)",
                    "MPRN": "12345678901",
                }
            )
            data.append(
                {
                    "Read Date and End Time": date_str,
                    "Read Value": "0.5",
                    "Read Type": "Active Export Interval (kWh)",
                    "MPRN": "12345678901",
                }
            )

        return data

    def test_esb_data_initialization(self, sample_data):
        """Test ESBData initialization."""
        esb_data = ESBData(data=sample_data)
        assert esb_data is not None
        # Should filter out data older than 90 days
        assert len(esb_data._data) <= 90

    def test_esb_data_today(self):
        """Test today's data calculation."""
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        data = [
            {
                "Read Date and End Time": today_start.strftime("%d-%m-%Y %H:%M"),
                "Read Value": "2.5",
                "Read Type": "Active Import Interval (kWh)",
            },
            {
                "Read Date and End Time": (today_start + timedelta(hours=1)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "3.0",
                "Read Type": "Active Import Interval (kWh)",
            },
        ]

        esb_data = ESBData(data=data)
        assert esb_data.today == 5.5

    def test_esb_data_last_24_hours(self):
        """Test last 24 hours data calculation."""
        now = datetime.now()

        data = [
            {
                "Read Date and End Time": (now - timedelta(hours=23)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "1.0",
                "Read Type": "Active Import Interval (kWh)",
            },
            {
                "Read Date and End Time": (now - timedelta(hours=25)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "2.0",  # Should not be included
                "Read Type": "Active Import Interval (kWh)",
            },
        ]

        esb_data = ESBData(data=data)
        assert esb_data.last_24_hours == 1.0

    def test_esb_data_this_week(self):
        """Test this week's data calculation."""
        now = datetime.now()
        week_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())

        data = [
            {
                "Read Date and End Time": week_start.strftime("%d-%m-%Y %H:%M"),
                "Read Value": "5.0",
                "Read Type": "Active Import Interval (kWh)",
            },
            {
                "Read Date and End Time": (week_start + timedelta(days=1)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "3.0",
                "Read Type": "Active Import Interval (kWh)",
            },
        ]

        esb_data = ESBData(data=data)
        assert esb_data.this_week == 8.0

    def test_esb_data_last_7_days(self):
        """Test last 7 days data calculation."""
        now = datetime.now()

        data = []
        for i in range(7):
            data.append(
                {
                    "Read Date and End Time": (now - timedelta(days=i)).strftime("%d-%m-%Y %H:%M"),
                    "Read Value": "1.0",
                    "Read Type": "Active Import Interval (kWh)",
                }
            )

        esb_data = ESBData(data=data)
        assert esb_data.last_7_days == 7.0

    def test_esb_data_this_month(self):
        """Test this month's data calculation."""
        now = datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        data = [
            {
                "Read Date and End Time": month_start.strftime("%d-%m-%Y %H:%M"),
                "Read Value": "10.0",
                "Read Type": "Active Import Interval (kWh)",
            },
            {
                "Read Date and End Time": (month_start + timedelta(days=5)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "5.0",
                "Read Type": "Active Import Interval (kWh)",
            },
        ]

        esb_data = ESBData(data=data)
        assert esb_data.this_month == 15.0

    def test_esb_data_last_30_days(self):
        """Test last 30 days data calculation."""
        now = datetime.now()

        data = []
        for i in range(30):
            data.append(
                {
                    "Read Date and End Time": (now - timedelta(days=i)).strftime("%d-%m-%Y %H:%M"),
                    "Read Value": "2.0",
                    "Read Type": "Active Import Interval (kWh)",
                }
            )

        esb_data = ESBData(data=data)
        assert esb_data.last_30_days == 60.0

    def test_esb_data_invalid_csv_structure(self):
        """Test invalid CSV structure handling."""
        data = [{"invalid": "data"}]

        with pytest.raises(ValueError, match="Invalid CSV structure"):
            ESBData(data=data)

    def test_esb_data_empty_list(self):
        """Test empty data list."""
        esb_data = ESBData(data=[])
        assert esb_data.today == 0.0
        assert esb_data.last_24_hours == 0.0

    def test_esb_data_filters_old_data(self):
        """Test that data older than MAX_DATA_AGE_DAYS is filtered."""
        now = datetime.now()

        data = [
            {
                "Read Date and End Time": (now - timedelta(days=95)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "1.0",
                "Read Type": "Active Import Interval (kWh)",
            },
            {
                "Read Date and End Time": (now - timedelta(days=50)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "2.0",
                "Read Type": "Active Import Interval (kWh)",
            },
        ]

        esb_data = ESBData(data=data)
        # Only data within 90 days should be kept
        assert len(esb_data._data) == 1

    def test_esb_data_handles_invalid_rows(self):
        """Test that invalid rows are skipped gracefully."""
        now = datetime.now()

        data = [
            {
                "Read Date and End Time": now.strftime("%d-%m-%Y %H:%M"),
                "Read Value": "5.0",
                "Read Type": "Active Import Interval (kWh)",
            },
            {
                "Read Date and End Time": "invalid-date",
                "Read Value": "1.0",
                "Read Type": "Active Import Interval (kWh)",
            },
            {
                "Read Date and End Time": now.strftime("%d-%m-%Y %H:%M"),
                "Read Value": "not-a-number",
                "Read Type": "Active Import Interval (kWh)",
            },
        ]

        esb_data = ESBData(data=data)
        # Should only have the valid row
        assert len(esb_data._data) == 1
        assert esb_data.today == 5.0

    def test_import_excludes_export(self):
        """Test that import totals exclude export rows."""
        now = datetime.now()
        ts = now.replace(hour=12, minute=0, second=0, microsecond=0).strftime("%d-%m-%Y %H:%M")
        data = [
            {"Read Date and End Time": ts, "Read Value": "2.0", "Read Type": "Active Import Interval (kWh)"},
            {"Read Date and End Time": ts, "Read Value": "0.5", "Read Type": "Active Export Interval (kWh)"},
        ]
        esb_data = ESBData(data=data)
        assert esb_data.today == 2.0
        assert esb_data.export_today == 0.5

    def test_export_totals(self):
        """Test that export totals sum only export rows."""
        now = datetime.now()
        data = []
        for i in range(7):
            ts = (now - timedelta(days=i)).strftime("%d-%m-%Y %H:%M")
            data.append({"Read Date and End Time": ts, "Read Value": "1.0", "Read Type": "Active Import Interval (kWh)"})
            data.append({"Read Date and End Time": ts, "Read Value": "0.5", "Read Type": "Active Export Interval (kWh)"})

        esb_data = ESBData(data=data)
        assert esb_data.last_7_days == 7.0
        assert esb_data.export_last_7_days == 3.5

    def test_export_last_24_hours(self):
        """Test last 24 hours export calculation."""
        now = datetime.now()
        data = [
            {"Read Date and End Time": (now - timedelta(hours=23)).strftime("%d-%m-%Y %H:%M"),
             "Read Value": "1.0", "Read Type": "Active Export Interval (kWh)"},
            {"Read Date and End Time": (now - timedelta(hours=25)).strftime("%d-%m-%Y %H:%M"),
             "Read Value": "2.0", "Read Type": "Active Export Interval (kWh)"},  # outside window
        ]
        assert ESBData(data=data).export_last_24_hours == 1.0

    def test_export_this_week(self):
        """Test this week's export calculation."""
        now = datetime.now()
        week_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
        data = [
            {"Read Date and End Time": week_start.strftime("%d-%m-%Y %H:%M"),
             "Read Value": "5.0", "Read Type": "Active Export Interval (kWh)"},
            {"Read Date and End Time": (week_start - timedelta(minutes=30)).strftime("%d-%m-%Y %H:%M"),
             "Read Value": "3.0", "Read Type": "Active Export Interval (kWh)"},  # previous week
        ]
        assert ESBData(data=data).export_this_week == 5.0

    def test_export_this_month(self):
        """Test this month's export calculation."""
        now = datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        data = [
            {"Read Date and End Time": month_start.strftime("%d-%m-%Y %H:%M"),
             "Read Value": "10.0", "Read Type": "Active Export Interval (kWh)"},
            {"Read Date and End Time": (month_start - timedelta(minutes=30)).strftime("%d-%m-%Y %H:%M"),
             "Read Value": "5.0", "Read Type": "Active Export Interval (kWh)"},  # previous month
        ]
        assert ESBData(data=data).export_this_month == 10.0

    def test_export_last_30_days(self):
        """Test last 30 days export calculation."""
        now = datetime.now()
        data = [
            {"Read Date and End Time": (now - timedelta(days=i)).strftime("%d-%m-%Y %H:%M"),
             "Read Value": "2.0", "Read Type": "Active Export Interval (kWh)"}
            for i in range(30)
        ]
        data.append(
            {"Read Date and End Time": (now - timedelta(days=31)).strftime("%d-%m-%Y %H:%M"),
             "Read Value": "9.0", "Read Type": "Active Export Interval (kWh)"}  # outside window
        )
        assert ESBData(data=data).export_last_30_days == 60.0

    def test_missing_read_type_column_raises(self):
        """Test that a missing Read Type column is rejected."""
        now = datetime.now()
        ts = now.replace(hour=12, minute=0, second=0, microsecond=0).strftime("%d-%m-%Y %H:%M")
        with pytest.raises(ValueError, match="Invalid CSV structure"):
            ESBData(data=[{"Read Date and End Time": ts, "Read Value": "3.0"}])

    def test_blank_read_type_skipped(self):
        """Test that a blank Read Type is skipped."""
        now = datetime.now()
        ts = now.replace(hour=12, minute=0, second=0, microsecond=0).strftime("%d-%m-%Y %H:%M")
        esb_data = ESBData(data=[{"Read Date and End Time": ts, "Read Value": "3.0", "Read Type": ""}])
        assert esb_data.today == 0.0
        assert esb_data.export_today == 0.0

    def test_unrecognized_read_type_skipped(self):
        """Test that unrecognized Read Type rows are skipped."""
        now = datetime.now()
        ts = now.replace(hour=12, minute=0, second=0, microsecond=0).strftime("%d-%m-%Y %H:%M")
        data = [
            {"Read Date and End Time": ts, "Read Value": "2.0", "Read Type": "Active Import Interval (kWh)"},
            {"Read Date and End Time": ts, "Read Value": "9.0", "Read Type": "Some Unrelated Read Type"},
        ]
        esb_data = ESBData(data=data)
        assert esb_data.today == 2.0
        assert esb_data.export_today == 0.0

    def test_get_export_readings_since(self):
        """Test get_export_readings_since returns only export readings."""
        now = datetime.now()
        ts = now.replace(hour=12, minute=0, second=0, microsecond=0).strftime("%d-%m-%Y %H:%M")
        data = [
            {"Read Date and End Time": ts, "Read Value": "2.0", "Read Type": "Active Import Interval (kWh)"},
            {"Read Date and End Time": ts, "Read Value": "0.5", "Read Type": "Active Export Interval (kWh)"},
        ]
        esb_data = ESBData(data=data)
        readings = esb_data.get_export_readings_since(since=now - timedelta(hours=13))
        assert len(readings) == 1
        assert readings[0]["value"] == 0.5

    def test_get_readings_since(self):
        """Test get_readings_since returns correct dictionary list."""
        from datetime import datetime, timedelta
        now = datetime.now()
        data = [
            {
                "Read Date and End Time": now.strftime("%d-%m-%Y %H:%M"),
                "Read Value": "5.0",
                "Read Type": "Active Import Interval (kWh)",
            },
            {
                "Read Date and End Time": (now - timedelta(hours=1)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "3.0",
                "Read Type": "Active Import Interval (kWh)",
            },
            {
                "Read Date and End Time": (now - timedelta(days=2)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "1.0",
                "Read Type": "Active Import Interval (kWh)",
            },
        ]
        esb_data = ESBData(data=data)

        # Readings since 12 hours ago
        readings = esb_data.get_readings_since(since=now - timedelta(hours=12))
        assert len(readings) == 2
        assert readings[0]["value"] == 5.0
        assert readings[1]["value"] == 3.0
