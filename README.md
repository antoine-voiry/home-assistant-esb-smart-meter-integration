# ESB Smart Meter integration for Home Assistant

Heavily inspired by https://github.com/badger707/esb-smart-meter-reading-automation

Originally forked from https://github.com/RobinJ1995/home-assistant-esb-smart-meter-integration

## Requirements

- Account at https://myaccount.esbnetworks.ie/
- Your meter's MPRN (11-digit number)
- Home Assistant 2023.1.0 or later

## Installation

### HACS (Recommended)

1. Add this repository to HACS as a custom repository
2. Search for "ESB Smart Meter" in HACS
3. Click "Install"
4. Restart Home Assistant

### Manual Installation

1. Download the latest release
2. Copy the `custom_components/esb_smart_meter` folder into your Home Assistant's `custom_components` folder
3. Restart Home Assistant

## Setup

1. Go to Settings → Devices & Services
2. Click "+ ADD INTEGRATION"
3. Search for "ESB Smart Meter"
4. Enter your ESB account credentials:
   - Username (email address)
   - Password
   - MPRN (11-digit meter number)

If all went well, you should now have the following entities in Home Assistant:
- `sensor.esb_electricity_usage_today`
- `sensor.esb_electricity_usage_last_24_hours`
- `sensor.esb_electricity_usage_this_week`
- `sensor.esb_electricity_usage_last_7_days`
- `sensor.esb_electricity_usage_this_month`
- `sensor.esb_electricity_usage_last_30_days`

## Features

- 📊 Six different time-period sensors for electricity usage tracking
- 🔄 Automatic data caching (updates every 12 hours by default)
- 🔁 Automatic retry with exponential backoff on failures
- 🔐 Proper async implementation using aiohttp
- 📱 Device grouping in Home Assistant UI
- 🆔 Unique entity IDs for better entity management
- 🔍 Detailed logging for troubleshooting

## Changelog

| Category | Change | Description | Version |
|----------|--------|-------------|---------|
| **HACS Compatibility** | Created `requirements.txt` | Added proper dependency management with version pinning (requests>=2.31.0, beautifulsoup4>=4.12.0, aiohttp>=3.8.0) | 1.1.0 |
| **HACS Compatibility** | Created `hacs.json` | Added HACS metadata file for proper repository configuration and discovery | 1.1.0 |
| **HACS Compatibility** | Updated `manifest.json` | Updated to current Home Assistant standards with semantic versioning (1.1.0), added documentation links, issue tracker, iot_class, and homeassistant version requirements | 1.1.0 |
| **Architecture** | Refactored to async/await | Replaced synchronous `requests` library with `aiohttp` for proper async support, eliminating blocking operations in the Home Assistant event loop | 1.1.0 |
| **Reliability** | Added retry logic | Implemented exponential backoff retry mechanism (3 attempts) for transient network failures | 1.1.0 |
| **Error Handling** | Improved exception handling | Added comprehensive error handling with specific error messages for network issues, parsing errors, and authentication failures | 1.1.0 |
| **Logging** | Enhanced logging | Added detailed logging at appropriate levels (DEBUG, INFO, WARNING, ERROR) for better troubleshooting | 1.1.0 |
| **Code Quality** | Added type hints | Added comprehensive type hints throughout all Python files for better IDE support and code maintainability | 1.1.0 |
| **User Experience** | Added device information | Sensors now grouped under a device in Home Assistant UI with proper manufacturer and model information | 1.1.0 |
| **Entity Management** | Added unique IDs | Each sensor now has a unique ID based on MPRN for better entity management and customization | 1.1.0 |
| **Configuration** | Improved config flow | Added MPRN validation (11-digit format check), better error messages, and unique ID configuration | 1.1.0 |
| **Configuration** | Added constants | Centralized configuration keys, URLs, and default values in `const.py` for easier maintenance | 1.1.0 |
| **Setup/Unload** | Improved integration lifecycle | Updated `__init__.py` with proper async entry setup and unload handling following current HA patterns | 1.1.0 |
| **Documentation** | Updated README | Enhanced documentation with installation instructions, features list, and comprehensive changelog | 1.1.0 |
| **Security** | Better session management | Improved cookie and session handling with proper async context managers | 1.1.0 |
| **Performance** | Optimized data caching | Improved caching logic with better timestamp management and debug logging | 1.1.0 |

## Troubleshooting

### Integration fails to load
- Check Home Assistant logs for detailed error messages
- Ensure you're running Home Assistant 2023.1.0 or later
- Verify all dependencies are installed correctly

### Authentication fails
- Double-check your ESB account credentials
- Ensure your MPRN is exactly 11 digits
- Try logging into the ESB website manually to verify credentials

### No data showing
- ESB data typically updates once per day
- The integration caches data for 12 hours to avoid excessive polling
- Check logs for any error messages during data fetch

### Enabling debug logging
Add the following to your `configuration.yaml`:
```yaml
logger:
  default: info
  logs:
    custom_components.esb_smart_meter: debug
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

See [LICENSE](LICENSE) file for details.
