"""Tests for ESB Data manipulation."""
import pytest
from datetime import datetime, timedelta
from custom_components.esb_smart_meter.sensor import ESBData


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
            data.append({
                "Read Date and End Time": date.strftime("%d-%m-%Y %H:%M"),
                "Read Value": "1.5",
                "Read Type": "Active Import",
                "MPRN": "12345678901"
            })
        
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
            },
            {
                "Read Date and End Time": (today_start + timedelta(hours=1)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "3.0",
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
            },
            {
                "Read Date and End Time": (now - timedelta(hours=25)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "2.0",  # Should not be included
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
            },
            {
                "Read Date and End Time": (week_start + timedelta(days=1)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "3.0",
            },
        ]
        
        esb_data = ESBData(data=data)
        assert esb_data.this_week == 8.0

    def test_esb_data_last_7_days(self):
        """Test last 7 days data calculation."""
        now = datetime.now()
        
        data = []
        for i in range(7):
            data.append({
                "Read Date and End Time": (now - timedelta(days=i)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "1.0",
            })
        
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
            },
            {
                "Read Date and End Time": (month_start + timedelta(days=5)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "5.0",
            },
        ]
        
        esb_data = ESBData(data=data)
        assert esb_data.this_month == 15.0

    def test_esb_data_last_30_days(self):
        """Test last 30 days data calculation."""
        now = datetime.now()
        
        data = []
        for i in range(30):
            data.append({
                "Read Date and End Time": (now - timedelta(days=i)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "2.0",
            })
        
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
            },
            {
                "Read Date and End Time": (now - timedelta(days=50)).strftime("%d-%m-%Y %H:%M"),
                "Read Value": "2.0",
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
            },
            {
                "Read Date and End Time": "invalid-date",
                "Read Value": "1.0",
            },
            {
                "Read Date and End Time": now.strftime("%d-%m-%Y %H:%M"),
                "Read Value": "not-a-number",
            },
        ]
        
        esb_data = ESBData(data=data)
        # Should only have the valid row
        assert len(esb_data._data) == 1
        assert esb_data.today == 5.0
