"""Constants for the ESB Smart Meter integration."""

from datetime import timedelta

DOMAIN = "esb_smart_meter"

# Configuration keys
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_MPRN = "mprn"

# Default values
DEFAULT_SCAN_INTERVAL = timedelta(hours=24)  # Retry next day if all attempts fail
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RETRIES = 3  # Max 3 authentication attempts per day (reduced for stealth)
DEFAULT_RETRY_WAIT = 1800  # Wait 30 minutes (1800 seconds) between retries
MAX_CSV_SIZE_MB = 10  # Maximum CSV response size in MB
MAX_DATA_AGE_DAYS = 90  # Maximum age of data to keep in memory

# Stealth and timing constants
STARTUP_DELAY_MIN = 300  # Minimum delay after HA boot (5 minutes)
STARTUP_DELAY_MAX = 600  # Maximum delay after HA boot (10 minutes)
HA_UPTIME_THRESHOLD = 600  # Check if HA has been running for 10 minutes
REQUEST_DELAY_MEAN = 3.5  # Mean delay between requests (seconds)
REQUEST_DELAY_STDDEV = 1.2  # Standard deviation for request delays
MIN_REQUEST_DELAY = 1.0  # Minimum delay between requests
MAX_REQUEST_DELAY = 8.0  # Maximum delay between requests
LONG_PAUSE_PROBABILITY = 0.1  # 10% chance of longer pause
LONG_PAUSE_MIN = 10  # Minimum long pause (seconds)
LONG_PAUSE_MAX = 15  # Maximum long pause (seconds)

# Session persistence
SESSION_CACHE_MIN_HOURS = 6  # Minimum hours to cache session
SESSION_CACHE_MAX_HOURS = 12  # Maximum hours to cache session
SESSION_CACHE_KEY = "cached_session"
SESSION_TIMESTAMP_KEY = "session_timestamp"
SESSION_EXPIRY_HOURS = 336  # Session expiry: 14 days (336 hours) - aggressive reuse
SESSION_FILE_NAME = "session_cache"  # Base filename for session storage
SESSION_VALIDATION_ENDPOINTS = [
    "https://myaccount.esbnetworks.ie/Api/HistoricConsumption",
]

# CAPTCHA handling
CAPTCHA_NOTIFICATION_ID = "esb_smart_meter_captcha"
CAPTCHA_COOLDOWN_HOURS = 24  # Don't spam notifications
CONF_MANUAL_COOKIES = "manual_cookies"  # Config key for manual cookie input
CONF_SESSION_COOKIES = "session_cookies"  # Config key for stored session cookies

# Circuit breaker settings
CIRCUIT_BREAKER_FAILURES = 3  # Open circuit after N failures
CIRCUIT_BREAKER_TIMEOUT = 1800  # Wait 30 minutes before retry (seconds)
CIRCUIT_BREAKER_MAX_TIMEOUT = 43200  # Max wait time: 12 hours
MAX_AUTH_ATTEMPTS_PER_DAY = 3  # Maximum authentication attempts per day

# Error handling
CAPTCHA_BACKOFF_HOURS = 24  # Wait 24 hours after CAPTCHA detection
RATE_LIMIT_BACKOFF_MINUTES = 30  # Wait 30 minutes after rate limit

# MPRN validation
MPRN_LENGTH = 11  # Expected length of MPRN

# API URLs
ESB_LOGIN_URL = "https://myaccount.esbnetworks.ie/"
ESB_AUTH_BASE_URL = (
    "https://login.esbnetworks.ie/esbntwkscustportalprdb2c01.onmicrosoft.com"
    "/B2C_1A_signup_signin"
)
ESB_MYACCOUNT_URL = "https://myaccount.esbnetworks.ie"
ESB_CONSUMPTION_URL = "https://myaccount.esbnetworks.ie/Api/HistoricConsumption"
ESB_TOKEN_URL = "https://myaccount.esbnetworks.ie/af/t"
ESB_DOWNLOAD_URL = "https://myaccount.esbnetworks.ie/DataHub/DownloadHdfPeriodic"

# CSV columns expected from ESB
CSV_COLUMN_DATE = "Read Date and End Time"
CSV_COLUMN_VALUE = "Read Value"
CSV_DATE_FORMAT = "%d-%m-%Y %H:%M"

# Device information
MANUFACTURER = "ESB Networks"
MODEL = "Smart Meter"
