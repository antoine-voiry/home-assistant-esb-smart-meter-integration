"""Tests for ESB Data manipulation."""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from custom_components.esb_smart_meter.models import DUBLIN_TZ, ESBData


def _dublin_now() -> datetime:
    """Naive Irish wall-clock time, matching how ESB labels its CSV rows."""
    return datetime.now(DUBLIN_TZ).replace(tzinfo=None)


class TestESBData:
    """Test ESBData class."""

    @pytest.fixture
    def sample_data(self):
        """Create sample data for testing."""
        now = _dublin_now()
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
        now = _dublin_now()
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
        now = _dublin_now()

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
        now = _dublin_now()
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
        now = _dublin_now()

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
        now = _dublin_now()
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
        now = _dublin_now()

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
        now = _dublin_now()

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
        now = _dublin_now()

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
        now = _dublin_now()
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
        now = _dublin_now()
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
        now = _dublin_now()
        data = [
            {"Read Date and End Time": (now - timedelta(hours=23)).strftime("%d-%m-%Y %H:%M"),
             "Read Value": "1.0", "Read Type": "Active Export Interval (kWh)"},
            {"Read Date and End Time": (now - timedelta(hours=25)).strftime("%d-%m-%Y %H:%M"),
             "Read Value": "2.0", "Read Type": "Active Export Interval (kWh)"},  # outside window
        ]
        assert ESBData(data=data).export_last_24_hours == 1.0

    def test_export_this_week(self):
        """Test this week's export calculation."""
        now = _dublin_now()
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
        now = _dublin_now()
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
        now = _dublin_now()
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
        now = _dublin_now()
        ts = now.replace(hour=12, minute=0, second=0, microsecond=0).strftime("%d-%m-%Y %H:%M")
        with pytest.raises(ValueError, match="Invalid CSV structure"):
            ESBData(data=[{"Read Date and End Time": ts, "Read Value": "3.0"}])

    def test_blank_read_type_skipped(self):
        """Test that a blank Read Type is skipped."""
        now = _dublin_now()
        ts = now.replace(hour=12, minute=0, second=0, microsecond=0).strftime("%d-%m-%Y %H:%M")
        esb_data = ESBData(data=[{"Read Date and End Time": ts, "Read Value": "3.0", "Read Type": ""}])
        assert esb_data.today == 0.0
        assert esb_data.export_today == 0.0

    def test_unrecognized_read_type_skipped(self):
        """Test that unrecognized Read Type rows are skipped."""
        now = _dublin_now()
        ts = now.replace(hour=12, minute=0, second=0, microsecond=0).strftime("%d-%m-%Y %H:%M")
        data = [
            {"Read Date and End Time": ts, "Read Value": "2.0", "Read Type": "Active Import Interval (kWh)"},
            {"Read Date and End Time": ts, "Read Value": "9.0", "Read Type": "Some Unrelated Read Type"},
        ]
        esb_data = ESBData(data=data)
        assert esb_data.today == 2.0
        assert esb_data.export_today == 0.0

    def test_hourly_usage_buckets(self):
        """Test 30-minute usage readings aggregate into hourly buckets."""
        h = (_dublin_now() - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)

        def ts(dt):
            return dt.strftime("%d-%m-%Y %H:%M")

        rt = "Active Import Interval (kWh)"
        data = [
            {"Read Date and End Time": ts(h + timedelta(minutes=30)), "Read Value": "0.1", "Read Type": rt},
            {"Read Date and End Time": ts(h + timedelta(minutes=60)), "Read Value": "0.2", "Read Type": rt},
            {"Read Date and End Time": ts(h + timedelta(minutes=90)), "Read Value": "0.4", "Read Type": rt},
        ]
        buckets = ESBData(data=data).hourly_usage()
        h_utc = h.replace(tzinfo=DUBLIN_TZ).astimezone(timezone.utc)
        # end 00:30 + 01:00 fall in hour h; end 01:30 falls in hour h+1
        assert buckets == [(h_utc, pytest.approx(0.3)), (h_utc + timedelta(hours=1), pytest.approx(0.4))]

    def test_hourly_export_separate_from_usage(self):
        """Test hourly buckets keep export separate from usage."""
        h = (_dublin_now() - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
        ts = (h + timedelta(minutes=30)).strftime("%d-%m-%Y %H:%M")
        data = [
            {"Read Date and End Time": ts, "Read Value": "1.5", "Read Type": "Active Import Interval (kWh)"},
            {"Read Date and End Time": ts, "Read Value": "0.5", "Read Type": "Active Export Interval (kWh)"},
        ]
        esb_data = ESBData(data=data)
        h_utc = h.replace(tzinfo=DUBLIN_TZ).astimezone(timezone.utc)
        assert esb_data.hourly_usage() == [(h_utc, pytest.approx(1.5))]
        assert esb_data.hourly_export() == [(h_utc, pytest.approx(0.5))]

    def test_october_repeated_hour_split_into_two_utc_hours(self):
        """Real ESB pattern (newest first): 01:00/01:30 appear twice on the fall-back day."""
        rt = "Active Import Interval (kWh)"
        labels_newest_first = [
            ("26-10-2025 02:00", "0.6"),
            ("26-10-2025 01:30", "0.5"),  # GMT pass
            ("26-10-2025 01:00", "0.4"),  # GMT pass
            ("26-10-2025 01:30", "0.3"),  # IST pass
            ("26-10-2025 01:00", "0.2"),  # IST pass
            ("26-10-2025 00:30", "0.1"),
        ]
        rows = [{"Read Date and End Time": d, "Read Value": v, "Read Type": rt} for d, v in labels_newest_first]
        with patch("custom_components.esb_smart_meter.models.MAX_DATA_AGE_DAYS", 100000):
            esb = ESBData(data=rows)

        utc = timezone.utc
        assert esb.hourly_usage() == [
            (datetime(2025, 10, 25, 23, 0, tzinfo=utc), pytest.approx(0.1 + 0.2)),  # 00:00-01:00 IST
            (datetime(2025, 10, 26, 0, 0, tzinfo=utc), pytest.approx(0.3 + 0.4)),  # 01:00-02:00 IST
            (datetime(2025, 10, 26, 1, 0, tzinfo=utc), pytest.approx(0.5 + 0.6)),  # 01:00-02:00 GMT
        ]

    def test_march_gap_has_no_phantom_hour(self):
        rt = "Active Import Interval (kWh)"
        labels_newest_first = [
            ("30-03-2025 02:30", "0.4"),
            ("30-03-2025 02:00", "0.3"),
            ("30-03-2025 00:30", "0.2"),
            ("30-03-2025 00:00", "0.1"),
        ]
        rows = [{"Read Date and End Time": d, "Read Value": v, "Read Type": rt} for d, v in labels_newest_first]
        with patch("custom_components.esb_smart_meter.models.MAX_DATA_AGE_DAYS", 100000):
            esb = ESBData(data=rows)

        utc = timezone.utc
        assert esb.hourly_usage() == [
            (datetime(2025, 3, 29, 23, 0, tzinfo=utc), pytest.approx(0.1)),
            (datetime(2025, 3, 30, 0, 0, tzinfo=utc), pytest.approx(0.2 + 0.3)),  # 00:30 GMT end + 02:00 IST end
            (datetime(2025, 3, 30, 1, 0, tzinfo=utc), pytest.approx(0.4)),
        ]

    def test_non_finite_and_negative_values_are_skipped(self):
        """nan/inf/negative readings would poison cumulative statistics."""
        ts = _dublin_now().strftime("%d-%m-%Y %H:%M")
        rt = "Active Import Interval (kWh)"
        rows = [
            {"Read Date and End Time": ts, "Read Value": value, "Read Type": rt}
            for value in ("nan", "inf", "-1.0", "0.5")
        ]
        assert [value for _, value in ESBData(data=rows)._data] == [0.5]

    def test_unrecognized_read_type_logged_once_without_row(self, caplog):
        """One summary warning, never the row (it contains the MPRN)."""
        ts = _dublin_now().strftime("%d-%m-%Y %H:%M")
        rows = [
            {"Read Date and End Time": ts, "Read Value": "1.0", "Read Type": "Mystery", "MPRN": "12345678901"}
            for _ in range(5)
        ]
        with caplog.at_level(logging.WARNING):
            ESBData(data=rows)
        messages = [r.getMessage() for r in caplog.records if "Read Type" in r.getMessage()]
        assert len(messages) == 1
        assert "12345678901" not in messages[0]
        assert "5" in messages[0]

    def test_invalid_structure_does_not_log_row_values(self, caplog):
        with caplog.at_level(logging.ERROR), pytest.raises(ValueError):
            ESBData(data=[{"Unexpected": "x", "MPRN": "12345678901"}])
        assert "12345678901" not in caplog.text

    def test_get_export_readings_since(self):
        """Test get_export_readings_since returns only export readings."""
        now = _dublin_now()
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
        now = _dublin_now()
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
        # Readings are returned in chronological order (oldest first)
        assert [r["value"] for r in readings] == [3.0, 5.0]
