"""Tests for ESB Smart Meter constants."""
import pytest
from datetime import timedelta

from custom_components.esb_smart_meter.const import (
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_MPRN,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    MAX_CSV_SIZE_MB,
    MAX_DATA_AGE_DAYS,
    CSV_COLUMN_DATE,
    CSV_COLUMN_VALUE,
    CSV_DATE_FORMAT,
    MANUFACTURER,
    MODEL,
)


def test_domain():
    """Test domain constant."""
    assert DOMAIN == "esb_smart_meter"


def test_configuration_keys():
    """Test configuration key constants."""
    assert CONF_USERNAME == "username"
    assert CONF_PASSWORD == "password"
    assert CONF_MPRN == "mprn"


def test_default_values():
    """Test default value constants."""
    assert DEFAULT_SCAN_INTERVAL == timedelta(hours=24)
    assert DEFAULT_TIMEOUT == 30
    assert DEFAULT_MAX_RETRIES == 3  # Changed for stealth (max 3 attempts per day)
    assert MAX_CSV_SIZE_MB == 10
    assert MAX_DATA_AGE_DAYS == 90


def test_csv_constants():
    """Test CSV-related constants."""
    assert CSV_COLUMN_DATE == "Read Date and End Time"
    assert CSV_COLUMN_VALUE == "Read Value"
    assert CSV_DATE_FORMAT == "%d-%m-%Y %H:%M"


def test_device_info():
    """Test device information constants."""
    assert MANUFACTURER == "ESB Networks"
    assert MODEL == "Smart Meter"
