# CAPTCHA Handling Strategy

## Overview

This document describes how the ESB Smart Meter integration handles CAPTCHA protection from ESB Networks and the design decisions behind the implementation.

## Problem Statement

ESB Networks occasionally enables CAPTCHA protection on their login page, typically triggered by:
- Multiple login attempts in a short period
- Automated access patterns detected by their systems
- Geographic or IP-based security policies

When CAPTCHA is active, automated login becomes impossible without human intervention.

## Detection Mechanism

The integration detects CAPTCHA in the authentication response by checking for specific indicators:

```python
# Location: api_client.py, __login() method
if (
    "g-recaptcha-response" in content
    or "captcha.html" in content
    or 'error_requiredFieldMissing":"Please confirm you are not a robot' in content
):
    raise CaptchaRequiredException(...)
```

**Detection Points:**
1. Presence of `g-recaptcha-response` field (Google reCAPTCHA)
2. Reference to `captcha.html` in response
3. Specific error message about robot verification

## Response Strategy

When CAPTCHA is detected, the integration follows a graceful degradation approach rather than aggressive retry:

### 1. **Raise Custom Exception**
```python
raise CaptchaRequiredException(
    "ESB Networks requires CAPTCHA verification. "
    "Please log in manually via the ESB website..."
)
```

### 2. **Coordinator Catches and Handles**
Located in `coordinator.py`, the `_async_update_data()` method catches this exception:

```python
except CaptchaRequiredException as err:
    if not self._captcha_notification_sent:
        await self._send_captcha_notification()
        self._captcha_notification_sent = True
        self.update_interval = timedelta(days=7)  # Reduce polling frequency
    
    return None  # Return None instead of raising UpdateFailed
```

**Key Design Decision:** Returns `None` instead of raising `UpdateFailed` to prevent:
- Exponential backoff hammering from coordinator retry logic
- Multiple notification spam
- Aggressive polling that could worsen CAPTCHA detection

### 3. **User Notification**
The integration sends two types of notifications:

#### Persistent Notification (Desktop/Web)
```python
await self.hass.services.async_call(
    "persistent_notification",
    "create",
    {
        "notification_id": CAPTCHA_NOTIFICATION_ID,
        "title": "ESB Smart Meter: CAPTCHA Detected",
        "message": "..."  # Detailed instructions
    },
)
```

#### Mobile Notification (if available)
```python
await self.hass.services.async_call(
    "notify",
    "notify",
    {
        "title": "ESB Smart Meter: CAPTCHA",
        "message": "...",
        "data": {
            "actions": [{
                "action": "URI",
                "title": "Open ESB Account",
                "uri": ESB_MYACCOUNT_URL,
            }]
        },
    },
)
```

### 4. **Polling Frequency Adjustment**
```python
self.update_interval = timedelta(days=7)  # From 24 hours to 7 days
```

This dramatic reduction:
- Prevents triggering additional CAPTCHA challenges
- Conserves system resources
- Gives ESB's security system time to "forget" the integration
- Still allows periodic retry to check if CAPTCHA has cleared

### 5. **Recovery Mechanism**
When data fetch succeeds again:
```python
if self._captcha_notification_sent:
    self._captcha_notification_sent = False
    self.update_interval = DEFAULT_SCAN_INTERVAL  # Restore 24-hour polling
    await self._dismiss_captcha_notification()
```

## User Options

Users have two paths to resolve CAPTCHA:

### Option 1: Manual Browser Login (Recommended)
1. User logs into ESB website through browser
2. Solves CAPTCHA manually
3. This helps "clear" the account for future automated access
4. Integration will retry in 7 days and may succeed

### Option 2: Manual Cookie Provision (Advanced)
1. User logs into ESB website and solves CAPTCHA
2. Extracts session cookies from browser DevTools
3. Provides cookies through integration's Options Flow
4. Integration uses provided cookies for authenticated requests

**Implementation:**
```python
# config_flow.py - ESBSmartMeterOptionsFlow
async def async_step_manual_cookies(self, user_input):
    cookie_string = user_input.get(CONF_MANUAL_COOKIES, "").strip()
    session_manager = SessionManager(self.hass, mprn)
    success = await session_manager.save_manual_cookies(cookie_string)
```

## Why Not Aggressive Retry?

The integration deliberately **avoids** aggressive retry when CAPTCHA is detected because:

1. **Detection Risk**: Repeated automated attempts increase likelihood of:
   - Longer CAPTCHA duration
   - Account flagging/blocking
   - IP-based rate limiting

2. **Resource Conservation**: 
   - Prevents unnecessary API calls
   - Reduces Home Assistant CPU/network usage
   - Respects ESB's infrastructure

3. **User Experience**:
   - Single clear notification vs. spam
   - Actionable guidance vs. repeated errors
   - Managed expectations about resolution time

4. **Stealth Considerations**:
   - Mimics human behavior (humans don't retry immediately after CAPTCHA)
   - Allows "cooldown" period for ESB's detection systems
   - Maintains lower profile for the integration

## Circuit Breaker Interaction

CAPTCHA detection **does not trigger the circuit breaker**:

```python
# api_client.py
except CaptchaRequiredException:
    # Don't record as circuit breaker failure
    _LOGGER.warning("CAPTCHA detected - user intervention required")
    raise
```

**Rationale:** 
- CAPTCHA is not a "failure" - it's a security measure requiring user action
- Circuit breaker is for transient network/server errors
- CAPTCHA requires different handling (notification, not backoff)

## Configuration Constants

```python
# const.py
CAPTCHA_NOTIFICATION_ID = "esb_smart_meter_captcha"
CAPTCHA_COOLDOWN_HOURS = 24
CAPTCHA_BACKOFF_HOURS = 24
```

## Testing Considerations

To test CAPTCHA handling:

1. **Mock the detection**:
   ```python
   mock_response.text.return_value = '<input name="g-recaptcha-response">'
   ```

2. **Verify notification sent**: Check `persistent_notification.create` was called

3. **Verify polling adjustment**: Assert `coordinator.update_interval == timedelta(days=7)`

4. **Verify no circuit breaker**: Ensure circuit breaker state unchanged

## Future Enhancements

Potential improvements to CAPTCHA handling:

1. **Session Validation**: Validate cached sessions before use to detect earlier if re-auth needed
2. **Smart Retry Timing**: Use time-of-day intelligence (retry during off-peak hours)
3. **User Preference**: Allow users to configure retry interval (conservative vs aggressive)
4. **Manual Trigger**: Add service call to manually trigger auth attempt without waiting for schedule
5. **CAPTCHA Solver Integration**: Integrate with external CAPTCHA solving services (ethical considerations apply)

## Related Files

- `custom_components/esb_smart_meter/api_client.py` - Detection logic
- `custom_components/esb_smart_meter/coordinator.py` - Response handling
- `custom_components/esb_smart_meter/config_flow.py` - Manual cookie input
- `custom_components/esb_smart_meter/session_manager.py` - Cookie management
- `custom_components/esb_smart_meter/const.py` - Configuration constants

## References

- [ESB Networks MyAccount](https://myaccount.esbnetworks.ie)
- [Home Assistant Notifications](https://www.home-assistant.io/integrations/persistent_notification/)
- [DataUpdateCoordinator](https://developers.home-assistant.io/docs/integration_fetching_data)
