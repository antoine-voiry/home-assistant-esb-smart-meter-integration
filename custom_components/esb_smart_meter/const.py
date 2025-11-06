"""Constants for the ESB Smart Meter integration."""
from datetime import timedelta

DOMAIN = "esb_smart_meter"

# Configuration keys
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_MPRN = "mprn"

# Default values
DEFAULT_SCAN_INTERVAL = timedelta(hours=12)
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RETRIES = 3
MAX_CSV_SIZE_MB = 10  # Maximum CSV response size in MB
MAX_DATA_AGE_DAYS = 90  # Maximum age of data to keep in memory

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
