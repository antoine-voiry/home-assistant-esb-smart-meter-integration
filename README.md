# ESB Smart Meter Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub Release](https://img.shields.io/github/release/antoine-voiry/home-assistant-esb-smart-meter-integration.svg)](https://github.com/antoine-voiry/home-assistant-esb-smart-meter-integration/releases)
[![License](https://img.shields.io/github/license/antoine-voiry/home-assistant-esb-smart-meter-integration.svg)](LICENSE)

A comprehensive Home Assistant integration for monitoring your electricity usage from ESB Networks Smart Meters in Ireland. Track your consumption across multiple time periods with automatic data retrieval and smart caching.

> **Credits:** Heavily inspired by [badger707's ESB automation](https://github.com/badger707/esb-smart-meter-reading-automation) and originally forked from [RobinJ1995's integration](https://github.com/RobinJ1995/home-assistant-esb-smart-meter-integration).

---

## 📋 Table of Contents

- [Features](#-features)
- [Prerequisites](#-prerequisites)
- [Installation](#-installation)
  - [HACS (Recommended)](#hacs-recommended)
  - [Manual Installation](#manual-installation)
- [Configuration](#-configuration)
- [Sensors](#-sensors)
- [How It Works](#-how-it-works)
- [Known Limitations](#-known-limitations)
- [Troubleshooting](#-troubleshooting)
- [Advanced Configuration](#-advanced-configuration)
- [Contributing](#-contributing)
- [Changelog](#-changelog)

---

## ✨ Features

- **📊 Six Time-Period Sensors**: Track usage for today, last 24 hours, this week, last 7 days, this month, and last 30 days
- **🔄 Smart Caching**: Automatic data updates every 24 hours to minimize API calls and respect ESB's systems
- **🔁 Robust Retry Logic**: 5 automatic retry attempts with 2-minute intervals on network failures
- **⚡ Async Implementation**: Non-blocking async/await design using aiohttp for optimal Home Assistant performance
- **🔐 Secure Authentication**: Handles ESB's complete 8-step OAuth2-like authentication flow
- **📱 Device Grouping**: All sensors grouped under a single device in Home Assistant UI
- **🆔 Unique Entity IDs**: Proper entity management with MPRN-based unique identifiers
- **🔍 Comprehensive Logging**: Detailed debug logging for troubleshooting authentication and data retrieval
- **🛡️ CAPTCHA Detection**: Automatically detects if ESB has enabled CAPTCHA protection with clear error messages
- **🎭 User Agent Rotation**: 70+ diverse browser user agents to minimize detection as automated access
- **💾 Memory Optimized**: Filters old data beyond 90 days to prevent memory leaks
- **📏 Size Limits**: Protects against excessive CSV data with 50MB limit

---

## 📋 Prerequisites

Before installing this integration, ensure you have:

1. **ESB Networks Account**: Active account at [myaccount.esbnetworks.ie](https://myaccount.esbnetworks.ie/)
   - You should be able to log in and view your electricity usage on their website
   
2. **MPRN Number**: Your meter's 11-digit Meter Point Reference Number (MPRN)
   - Find this on your electricity bill or ESB Networks account
   - Format: `XXXXXXXXXXX` (11 digits)
   
3. **Home Assistant**: Version 2023.1.0 or later
   - Required for modern config entry and async support

4. **Smart Meter**: You must have an ESB Networks smart meter installed
   - Data typically available from the day after installation

---

## 💾 Installation

### HACS (Recommended)

[HACS](https://hacs.xyz/) is the easiest way to install and manage custom integrations.

1. **Add Custom Repository**
   - Open HACS in Home Assistant
   - Click the three dots menu (⋮) in the top right
   - Select "Custom repositories"
   - Add repository URL: `https://github.com/antoine-voiry/home-assistant-esb-smart-meter-integration`
   - Category: "Integration"
   - Click "Add"

2. **Install Integration**
   - Search for "ESB Smart Meter" in HACS
   - Click "Download"
   - Select the latest version
   - Restart Home Assistant

### Manual Installation

For advanced users who prefer manual installation:

1. **Download Latest Release**
   - Go to [Releases](https://github.com/antoine-voiry/home-assistant-esb-smart-meter-integration/releases)
   - Download the latest `Source code (zip)` or `Source code (tar.gz)`

2. **Extract Files**
   - Extract the downloaded archive
   - Locate the `custom_components/esb_smart_meter` folder

3. **Copy to Home Assistant**
   ```bash
   # Navigate to your Home Assistant config directory
   cd /config  # or wherever your configuration.yaml is located
   
   # Create custom_components folder if it doesn't exist
   mkdir -p custom_components
   
   # Copy the integration
   cp -r /path/to/extracted/custom_components/esb_smart_meter custom_components/
   ```

4. **Restart Home Assistant**
   - Restart for the integration to be recognized

---

## ⚙️ Configuration

### Initial Setup

1. **Navigate to Integrations**
   - Go to **Settings** → **Devices & Services**
   - Click the **"+ ADD INTEGRATION"** button in the bottom right

2. **Search for ESB Smart Meter**
   - Type "ESB Smart Meter" in the search box
   - Click on the integration when it appears

3. **Enter Credentials**
   - **Username**: Your ESB Networks account email address
   - **Password**: Your ESB Networks account password
   - **MPRN**: Your 11-digit meter number (e.g., `10012345678`)

4. **Submit**
   - Click "Submit" and wait for authentication to complete
   - This may take 30-60 seconds as it authenticates with ESB

### Configuration Validation

The integration validates:
- ✅ MPRN is exactly 11 digits
- ✅ Credentials can authenticate with ESB Networks
- ✅ User has access to the specified MPRN

---

## 📊 Sensors

After successful setup, you'll have **six sensors** created under a single device:

| Sensor Entity ID | Description | Time Period |
|-----------------|-------------|-------------|
| `sensor.esb_electricity_usage_today` | Usage since midnight today | 00:00 today → now |
| `sensor.esb_electricity_usage_last_24_hours` | Rolling 24-hour usage | Last 24 hours |
| `sensor.esb_electricity_usage_this_week` | Usage since Monday this week | Monday 00:00 → now |
| `sensor.esb_electricity_usage_last_7_days` | Rolling 7-day usage | Last 7 days |
| `sensor.esb_electricity_usage_this_month` | Usage since 1st of this month | 1st 00:00 → now |
| `sensor.esb_electricity_usage_last_30_days` | Rolling 30-day usage | Last 30 days |

**All sensors report in kilowatt-hours (kWh)** with the `⚡` icon.

### Device Information

All sensors are grouped under:
- **Device Name**: `ESB Smart Meter (XXXXXXXXXXX)` where X's are your MPRN
- **Manufacturer**: ESB Networks
- **Model**: Smart Meter

---

## 🔧 How It Works

### Data Flow

1. **Authentication** (8-step OAuth2-like flow):
   - Retrieves CSRF token and transaction ID
   - Submits login credentials
   - Confirms authentication
   - Obtains session cookies
   - Gets file download token

2. **Data Retrieval**:
   - Downloads half-hourly electricity usage data in CSV format
   - Parses and filters data (keeps last 90 days)
   - Calculates totals for each sensor's time period

3. **Caching**:
   - Data is cached for **24 hours** to minimize API calls
   - Automatic refresh every 24 hours
   - Manual refresh possible via Home Assistant Developer Tools

4. **Error Handling**:
   - **5 retry attempts** on network failures
   - **2-minute wait** between retries
   - **Next-day retry** if all attempts fail (24-hour cache expiry)

### Update Frequency

- **ESB Data Updates**: ESB Networks typically updates smart meter data once per day
- **Integration Polling**: Every 24 hours
- **Data Freshness**: Usually 1-2 days behind real-time (ESB processing delay)

---

## ⚠️ Known Limitations

### CAPTCHA Protection

ESB Networks has implemented CAPTCHA protection on their login system. This integration includes:
- ✅ **70+ diverse user agents** to minimize detection
- ✅ **Random delays** between requests (2-20 seconds)
- ✅ **CAPTCHA detection** with clear error messages

**If CAPTCHA is triggered:**
- The integration will log a clear error message
- You may see: `"ESB Networks requires CAPTCHA verification"`
- This could be temporary rate limiting or permanent security change
- Currently **no automated solution** exists for CAPTCHA bypass

### Data Availability

- ESB Networks data is typically **1-2 days behind** real-time
- Data updates **once per day** on ESB's servers (usually overnight)
- First data may take **24-48 hours** after smart meter installation

### API Limitations

- This integration uses ESB's **consumer-facing web interface** (not an official API)
- ESB may change their authentication flow at any time
- Integration may break if ESB makes website changes

---

## 🔍 Troubleshooting


### Common Issues

#### Integration Fails to Load

**Symptoms**: Integration doesn't appear or shows as failed in integrations page

**Solutions**:

1. Check Home Assistant logs for detailed error messages:
   - Go to **Settings** → **System** → **Logs**
   - Look for messages containing `esb_smart_meter`

2. Verify Home Assistant version:
   - Minimum required: **2023.1.0**
   - Check current version in **Settings** → **About**

3. Ensure dependencies are installed:
   - Restart Home Assistant to trigger dependency installation
   - Check `custom_components/esb_smart_meter/manifest.json` for required packages

#### Authentication Fails

**Symptoms**: Setup fails with "Invalid credentials" or authentication error

**Solutions**:

1. **Verify ESB Account Credentials**:
   - Try logging into [myaccount.esbnetworks.ie](https://myaccount.esbnetworks.ie/) manually
   - Use the exact same email and password in the integration
   - Check for extra spaces in username/password

2. **Validate MPRN Format**:
   - Must be exactly **11 digits**
   - No spaces, hyphens, or other characters
   - Example: `10012345678`
   - Find on electricity bill or ESB account page

3. **Check for CAPTCHA**:
   - If logs show `"CAPTCHA detected"`, ESB is blocking automated login
   - This may be temporary rate limiting or permanent security change
   - Wait 24 hours and try again
   - Check integration logs for detailed CAPTCHA error messages

4. **Network Issues**:
   - Ensure Home Assistant can reach `esbnetworks.ie`
   - Check firewall/proxy settings
   - Try accessing ESB website from Home Assistant host

#### No Data Showing

**Symptoms**: Sensors created but show "Unknown" or "Unavailable"

**Solutions**:

1. **Wait for Data Update**:
   - ESB data updates **once per day** (usually overnight)
   - Integration polls **every 24 hours**
   - First data may take 24-48 hours after setup

2. **Check Data Availability**:
   - Log into ESB website and verify you can see usage data
   - New smart meter installations may have 1-2 day delay

3. **Review Logs**:
   - Enable debug logging (see below)
   - Look for errors during data fetch
   - Check for CSV parsing errors or size limit warnings

4. **Force Update**:
   - Delete and re-add the integration
   - Or wait for next 24-hour refresh cycle

#### Sensors Show Stale Data

**Symptoms**: Data doesn't update daily

**Solutions**:

1. Check cache status in logs (enable debug mode)
2. Verify ESB website shows newer data
3. Restart Home Assistant to force cache refresh
4. Check for errors in logs preventing data updates

### Enabling Debug Logging

To get detailed diagnostic information:

1. **Add to `configuration.yaml`**:

   ```yaml
   logger:
     default: info
     logs:
       custom_components.esb_smart_meter: debug
   ```

2. **Restart Home Assistant**

3. **View Logs**:
   - Go to **Settings** → **System** → **Logs**
   - Download full logs if needed for issue reporting

**Debug logs include**:
- Complete authentication flow (8 steps)
- URLs accessed and response status codes
- HTML response previews
- CAPTCHA detection details
- CSV parsing information
- Cache hit/miss events
- Retry attempt details

---

## 🔧 Advanced Configuration

### Customizing Sensor Names

Rename sensors in the Home Assistant UI:

1. Go to **Settings** → **Devices & Services**
2. Click on the **ESB Smart Meter** integration
3. Click on any sensor entity
4. Click the gear icon (⚙️)
5. Change **Name** and **Entity ID** as desired

### Creating Custom Sensors

Use template sensors for custom time periods:

```yaml
template:
  - sensor:
      - name: "ESB Yesterday"
        unit_of_measurement: "kWh"
        state: >
          {% set today = states('sensor.esb_electricity_usage_today') | float %}
          {% set last24 = states('sensor.esb_electricity_usage_last_24_hours') | float %}
          {{ (last24 - today) | round(2) }}
        icon: "mdi:flash"
```

### Energy Dashboard Integration

Add ESB sensors to Home Assistant Energy Dashboard:

1. Go to **Settings** → **Dashboards** → **Energy**
2. Click **Add Consumption**
3. Select `sensor.esb_electricity_usage_today` or another sensor
4. Configure tariff pricing if desired

**Note**: For energy dashboard, `sensor.esb_electricity_usage_today` works best as it resets at midnight.

### Automation Examples

**Alert on high daily usage**:

```yaml
automation:
  - alias: "High Electricity Usage Alert"
    trigger:
      - platform: numeric_state
        entity_id: sensor.esb_electricity_usage_today
        above: 30  # kWh
    action:
      - service: notify.mobile_app
        data:
          title: "High Electricity Usage"
          message: "Today's usage: {{ states('sensor.esb_electricity_usage_today') }} kWh"
```

**Monthly usage report**:

```yaml
automation:
  - alias: "Monthly Electricity Report"
    trigger:
      - platform: time
        at: "09:00:00"
      - platform: template
        value_template: "{{ now().day == 1 }}"
    action:
      - service: notify.email
        data:
          title: "Monthly Electricity Report"
          message: "Last 30 days usage: {{ states('sensor.esb_electricity_usage_last_30_days') }} kWh"
```

---

## 🤝 Contributing

Contributions are welcome! Here's how you can help:

### Reporting Issues

1. Check [existing issues](https://github.com/antoine-voiry/home-assistant-esb-smart-meter-integration/issues) first
2. Include Home Assistant version and integration version
3. Enable debug logging and include relevant log excerpts
4. Describe expected vs actual behavior

### Submitting Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Test thoroughly with Home Assistant
5. Update documentation if needed
6. Submit a pull request with clear description

### Development Setup

```bash
# Clone repository
git clone https://github.com/antoine-voiry/home-assistant-esb-smart-meter-integration.git
cd home-assistant-esb-smart-meter-integration

# Install development dependencies
pip install -r requirements-test.txt

# Run tests
pytest tests/
```

---

## 📝 Changelog

### Version 1.2.0 (2024)

**🛡️ Security & Reliability**
- Added CAPTCHA detection with clear error messages
- Expanded user agent pool to 70+ diverse browser/OS combinations
- Implemented random delays between authentication requests

**🐛 Bug Fixes**
- Fixed boolean parameter bug in rememberMe field (False → "false")
- Improved form parsing error handling
- Added missing field validation

**🔧 Configuration**
- Updated retry logic: 5 attempts (was 3)
- Fixed wait time: 2 minutes between retries (was exponential backoff)
- Increased scan interval: 24 hours (was 12 hours)

**📊 Debugging**
- Added comprehensive URL logging
- Added HTML response preview logging (first 500 chars)
- Added detailed form detection debugging
- Full HTML dump on errors for troubleshooting

**🏗️ Code Quality**
- Refactored user agents to separate module (`user_agents.py`)
- Added CodeQL configuration to exclude user agent file
- Improved code organization and maintainability

### Version 1.1.0 (2023)

**🏗️ Architecture**
- Refactored from synchronous `requests` to async `aiohttp`
- Eliminated blocking operations in Home Assistant event loop
- Added proper async/await throughout codebase

**🔐 Reliability & Security**
- Implemented retry logic with exponential backoff (3 attempts)
- Improved cookie and session management
- Added comprehensive error handling for network/parsing/auth failures

**📦 HACS Compatibility**
- Created `requirements.txt` with pinned dependencies
- Created `hacs.json` for repository configuration
- Updated `manifest.json` to current HA standards

**✨ Features**
- Added device grouping in Home Assistant UI
- Added unique entity IDs based on MPRN
- Improved config flow with MPRN validation
- Centralized configuration in `const.py`

**📚 Documentation & Code Quality**
- Added comprehensive type hints
- Enhanced logging at all levels (DEBUG, INFO, WARNING, ERROR)
- Improved README with detailed instructions
- Added proper integration lifecycle handling

**⚡ Performance**
- Optimized data caching with better timestamp management
- Memory optimization with data filtering

---

## 📄 License

This project is licensed under the terms specified in the [LICENSE](LICENSE) file.

---

## 🙏 Acknowledgments

- **[badger707](https://github.com/badger707/esb-smart-meter-reading-automation)** - Original automation inspiration
- **[RobinJ1995](https://github.com/RobinJ1995/home-assistant-esb-smart-meter-integration)** - Original integration fork
- **ESB Networks** - For providing smart meter infrastructure
- **Home Assistant Community** - For support and feedback

---

## ⚖️ Disclaimer

This is an unofficial integration and is not affiliated with, endorsed by, or connected to ESB Networks. Use at your own risk. The integration relies on ESB's consumer web interface which may change at any time without notice.

**For official ESB Networks support, visit**: [esbnetworks.ie/help-centre](https://www.esbnetworks.ie/help-centre)
