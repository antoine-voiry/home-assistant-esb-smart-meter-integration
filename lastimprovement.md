# ESB Smart Meter Integration - Improvement Recommendations

**Date:** November 8, 2025  
**Focus:** Security, Performance, Reliability, and Stealth

---

## **STEALTH & ANTI-DETECTION IMPROVEMENTS** ⭐

Since ESB Networks is not encouraging automation, these measures will make the integration appear more like legitimate human usage:

### 1. **Request Timing & Patterns**
- **CRITICAL**: Replace fixed delays with more human-like random patterns
  - Current: `randint(2, 5)` - too predictable
  - Better: Use normal distribution (e.g., `mean=3.5, stddev=1.2`) for delays between 1-8 seconds
  - Add occasional longer pauses (10-15 seconds) randomly to mimic user reading
  - Vary scan interval slightly (±2 hours) instead of exactly 24 hours

### 2. **Request Frequency Management**
- **HIGH PRIORITY**: Implement intelligent scheduling
  - Don't fetch data at exactly the same time each day (too robotic)
  - Add randomized offset: ±2 hours from base schedule
  - Avoid fetching during off-hours (2 AM - 6 AM) unless user was previously active
  - Prefer fetching during typical user activity windows (7 AM - 11 PM)
  - Skip occasional days randomly (1-2% chance) to mimic human forgetfulness

### 3. **User Agent Rotation Intelligence**
- **MEDIUM PRIORITY**: Make user agent selection smarter
  - Store selected user agent per session and reuse it (humans don't switch browsers mid-session)
  - Rotate user agents only between days, not between requests
  - Match user agent with realistic accept-language and platform headers
  - Avoid mobile user agents (unlikely someone checks meter on phone every day)
  - Prefer Windows/macOS Chrome/Firefox (most common for utility sites)

### 4. **Browser Fingerprint Consistency**
- **HIGH PRIORITY**: Ensure header consistency
  - Once a user agent is selected, derive ALL headers from it consistently:
    - Accept-Language should match user agent OS region
    - Sec-Ch-Ua headers should match Chrome version
    - Platform should match OS (Win32, MacIntel, Linux x86_64)
  - Store fingerprint per MPRN to maintain consistency across sessions
  - Add typical browser headers: DNT, Sec-GPC, Accept-Encoding: gzip, deflate, br

### 5. **Mouse & Keyboard Simulation Headers**
- **MEDIUM PRIORITY**: Add interaction simulation
  - Include `Sec-Fetch-User: ?1` only on initial navigation (not XHR)
  - Randomize `Sec-Fetch-Mode` appropriately per request type
  - Add occasional typo-like behavior in form submission (though harder with MPRN)

### 6. **Cookie Handling**
- **HIGH PRIORITY**: More realistic cookie behavior
  - Don't clear cookies between same-day requests
  - Store cookies persistently per MPRN and reuse them
  - Respect cookie expiration properly
  - Send cookies in same order browsers do (alphabetical by name)

### 7. **Traffic Pattern Obfuscation**
- **MEDIUM PRIORITY**: Blend in with normal traffic
  - Fetch multiple pages occasionally, not just the data endpoint
  - Visit the consumption page, scroll around (send scroll events if API available)
  - Occasionally fetch but don't download data (abort request)
  - Add 1-2 second pause after page load before clicking download (humans read first)

### 8. **TLS Fingerprint**
- **HIGH PRIORITY**: Match browser TLS fingerprints
  - aiohttp's TLS fingerprint differs from browsers
  - Consider using curl_cffi or httpx with impersonate parameter
  - Match cipher suites, TLS version, and extensions to selected browser

### 9. **Error Handling Stealth**
- **CRITICAL**: Don't hammer on failures
  - After authentication failure, wait minimum 30 minutes before retry
  - After 3 consecutive failures, wait 6-12 hours (exponential backoff)
  - Implement daily retry limit (max 3 attempts per day)
  - After CAPTCHA detection, stop all attempts for 24-48 hours

### 10. **Behavioral Analytics Evasion**
- **MEDIUM PRIORITY**: Avoid pattern detection
  - Track request success rate per MPRN
  - If success rate drops below 80%, increase delays and reduce frequency
  - Randomize request path order slightly (within authentication flow constraints)
  - Add occasional benign 404s (request non-existent asset) to appear more browser-like

### 11. **Delayed Startup After Home Assistant Boot**
- **CRITICAL**: Don't start immediately when HA starts
  - Add random startup delay of 5-10 minutes after Home Assistant boots
  - This mimics human behavior (users don't check their meter the instant their system starts)
  - Prevents correlation between HA restarts and ESB API requests
  - Implementation: Use `asyncio.sleep(randint(300, 600))` in `async_setup_entry` before first data fetch
  - Check if Home Assistant has been running for more than 10 minutes before considering delayed start
  - Store "first run after boot" flag to avoid delay on subsequent data refreshes

### 12. **Conditional Data Download**
- **HIGH PRIORITY**: Only download data after successful authentication
  - Current approach may attempt download even if auth fails silently
  - Implement two-phase approach:
    1. **Authentication phase**: Verify credentials and get token
    2. **Download phase**: Only proceed if authentication was successful
  - Track authentication state: `auth_success`, `auth_failed`, `auth_pending`
  - If authentication fails, mark integration unavailable and wait for next scheduled attempt
  - Don't retry download if authentication succeeded but download failed (wait for next cycle)
  - Store last authentication timestamp and result per MPRN
  - Benefits:
    - Reduces unnecessary traffic
    - Prevents hammering download endpoint after auth failure
    - Makes traffic pattern more human-like (humans don't try to download if they can't log in)

---

## **SECURITY IMPROVEMENTS**

### 1. **Credential Storage & Handling**
- **HIGH PRIORITY**: Passwords stored in plain text in `entry.data`
  - Use Home Assistant's secure credential storage mechanisms
  - Ensure passwords never appear in logs (currently logged at line 442)
  - Add password validation (minimum length, complexity)
  - Implement secure credential rotation mechanism

### 2. **Input Validation**
- **MEDIUM PRIORITY**: Enhance validation
  - MPRN validation only checks digits/length. Add checksum validation
  - Add input sanitization for username to prevent injection attacks
  - Validate all HTML form data (state, client_info, code) for XSS/injection risks
  - Reject MPRNs with suspicious patterns

### 3. **Logging Security**
- **CRITICAL**: Remove sensitive data from logs
  - Line 421: CSRF token partially logged
  - Line 442: Password logged in login_data dict
  - Redact all credentials in debug logs
  - Implement log level filtering for production

### 4. **Session Management**
- **HIGH PRIORITY**: Proper session lifecycle
  - Sessions created but never closed - implement cleanup in `async_unload_entry`
  - Add session timeout/expiry (6-12 hours)
  - Add mutual TLS verification settings
  - Validate SSL certificates strictly

### 5. **Error Message Information Disclosure**
- **MEDIUM PRIORITY**: Sanitize error responses
  - Don't expose stack traces to users
  - Generic error messages for authentication failures
  - Log details internally, show safe messages to users

### 6. **HTML Parsing Security**
- **MEDIUM PRIORITY**: Secure parsing
  - Validate content-type before parsing HTML
  - Implement maximum HTML document size limit (5MB)
  - Use lxml parser (faster, more secure than html.parser)

### 7. **Rate Limit Protection**
- **HIGH PRIORITY**: Protect from account lockout
  - Detect and respect rate limit responses (429, 503)
  - Implement exponential backoff starting at 30 minutes
  - Daily attempt limit per MPRN (max 5 authentication attempts)

---

## **PERFORMANCE IMPROVEMENTS**

### 1. **Memory Management**
- **HIGH PRIORITY**: Optimize memory usage
  - User agents list: 50+ entries loaded. Consider lazy loading or reduce to 10-15
  - CSV data loaded entirely into memory. Implement streaming for large datasets
  - `ESBData._data` stores all parsed data. Use rolling window or pagination
  - Clear old data aggressively (current 90 days may be too long)

### 2. **Caching Optimization**
- **MEDIUM PRIORITY**: Multi-level caching
  - Add ETag/Last-Modified support for conditional requests
  - Cache download tokens separately (1-6 hour TTL)
  - Implement persistent disk cache for historical data
  - Cache parsed/aggregated data, not just raw CSV

### 3. **Network Efficiency**
- **MEDIUM PRIORITY**: Reduce latency
  - 8 sequential authentication requests could be optimized
  - Use connection pooling and keep-alive
  - Compress requests where possible (Accept-Encoding: gzip, br)
  - Reuse TCP connections between requests

### 4. **Data Processing**
- **HIGH PRIORITY**: Faster processing
  - `_filter_and_parse_data` iterates all CSV rows every time. Cache results
  - Date parsing repeated. Cache parsed timestamps
  - Pre-compute aggregations (daily, weekly, monthly totals)
  - Consider using `pandas` or `polars` for large datasets

### 5. **Async Optimization**
- **LOW PRIORITY**: Better async patterns
  - Use async CSV library instead of executor
  - Batch sensor updates instead of individual calls
  - Consider asyncio.gather for parallel sensor updates

---

## **RELIABILITY IMPROVEMENTS**

### 1. **Error Handling**
- **CRITICAL**: More specific exceptions
  - Replace broad `except Exception` (lines 139, 752) with specific types
  - Create custom exception classes for ESB-specific errors
  - Distinguish between retryable and non-retryable errors

### 2. **Circuit Breaker Pattern**
- **HIGH PRIORITY**: Prevent cascade failures
  - After 3 consecutive failures, open circuit for 30 minutes
  - After 5 failures in 24 hours, open for 6 hours
  - Gradual recovery: test with one request before full operation
  - Track failure reasons (auth, network, parsing, rate limit)

### 3. **Retry Logic**
- **HIGH PRIORITY**: Smarter retry strategy
  - Implement exponential backoff with jitter
  - Different strategies for different error types:
    - 4xx client errors: no retry (except 429)
    - 5xx server errors: retry with backoff
    - Network errors: immediate retry once, then backoff
  - Max total retry time limit (e.g., 4 hours)
  - Respect Retry-After headers

### 4. **Timeout Management**
- **MEDIUM PRIORITY**: Operation-specific timeouts
  - Login requests: 30s
  - File download: 120s (depends on data size)
  - Token requests: 15s
  - HTML page loads: 20s
  - Add read timeout separate from connect timeout

### 5. **State Management**
- **HIGH PRIORITY**: Prevent race conditions
  - Add locks for concurrent update prevention
  - Validate state in `async_setup_entry`
  - Handle config entry reload gracefully
  - Implement state machine for authentication flow

### 6. **Data Validation**
- **MEDIUM PRIORITY**: Comprehensive validation
  - Validate CSV schema strictly
  - Check date ranges (reject future dates, dates > 2 years old)
  - Validate energy values (reject negative, reject > 1000 kWh per half-hour)
  - Checksum validation for critical data

### 7. **Rate Limiting**
- **HIGH PRIORITY**: Handle ESB rate limits
  - Detect 429 responses and respect them
  - Implement backoff strategy (start at 30 minutes)
  - Track requests per day per MPRN
  - Add configurable max requests per day setting

### 8. **Monitoring & Observability**
- **MEDIUM PRIORITY**: Better visibility
  - Add health check endpoint/sensor
  - Metrics for:
    - Authentication success/failure rates
    - Data fetch duration
    - Cache hit rates
    - Error rates by type
    - Last successful fetch timestamp
  - Add diagnostic sensor for integration status

### 9. **Configuration Validation**
- **HIGH PRIORITY**: Test during setup
  - Validate MPRN actually works during config flow
  - Test authentication before creating config entry
  - Provide clear error messages for common issues
  - Allow retry during setup if transient failure

### 10. **Session Cleanup**
- **CRITICAL**: Prevent resource leaks
  - Close sessions in `async_unload_entry`
  - Implement context managers for session lifecycle
  - Clear cookies and cached data on unload
  - Cancel pending requests on shutdown

---

## **CODE QUALITY IMPROVEMENTS**

### 1. **Type Hints**
- Add return type hints to all methods
- Use `typing.Optional` consistently
- Use `Protocol` for interface definitions

### 2. **Documentation**
- Add docstring examples for complex methods
- Document expected exceptions
- Document ESB API rate limits and behavior
- Add architecture diagram

### 3. **Testing**
- Increase coverage for error scenarios (currently missing)
- Add integration tests for full authentication flow
- Mock external HTTP calls properly
- Test stealth features (timing, user agents, etc.)

### 4. **Constants**
- Move magic numbers to constants:
  - 11 for MPRN length
  - 90 days max data age
  - Various timing values
- URL patterns in separate config file

### 5. **Separation of Concerns**
- **MEDIUM PRIORITY**: Split `sensor.py` (800+ lines)
  - `auth.py` - authentication flow
  - `api.py` - data fetching
  - `sensors.py` - sensor entities
  - `data.py` - data models
  - `stealth.py` - stealth utilities

### 6. **Dependency Management**
- Remove `homeassistant>=2025.11.1` from `requirements.txt`
- Pin dependencies more strictly
- Add `lxml` parser for BeautifulSoup
- Consider `curl_cffi` for better TLS fingerprinting

---

## **IMPLEMENTATION PRIORITY**

### **Phase 1 - Critical Security & Stealth (Week 1)**
1. ✅ Remove password logging
2. ✅ Implement session cleanup
3. ✅ Fix overly broad exception handling
4. ✅ Add delayed startup (5-10 minutes after HA boot)
5. ✅ Implement conditional data download (only after successful auth)
6. ✅ Add intelligent request timing (random delays with normal distribution)
7. ✅ Implement session persistence (reuse cookies for 6-12 hours)
8. ✅ Add exponential backoff on failures with minimum 30-minute wait

### **Phase 2 - High Priority Reliability & Stealth (Week 2)**
9. ✅ Implement circuit breaker pattern
10. ✅ Add browser fingerprint consistency
11. ✅ Implement smart scheduling (avoid off-hours, vary times)
12. ✅ Add rate limit handling
13. ✅ Optimize memory usage
14. ✅ Test during config flow setup

### **Phase 3 - Medium Priority Features (Week 3-4)**
15. ✅ Improve caching strategy
16. ✅ Split large files
17. ✅ Add monitoring metrics
18. ✅ User agent rotation intelligence
19. ✅ Traffic pattern obfuscation
20. ✅ Better timeout management

### **Phase 4 - Long-term Improvements (Ongoing)**
21. ✅ TLS fingerprint matching (requires library change)
22. ✅ Update user agent list management
23. ✅ Improve documentation
22. ✅ Comprehensive testing suite
23. ✅ IP rotation support (optional)

---

## **STEALTH BEST PRACTICES SUMMARY**

### Do's ✅
- Use realistic, human-like timing patterns
- Maintain consistent browser fingerprint per session
- Reuse authentication cookies as long as possible
- Add randomness to scheduling (±2 hours)
- Back off aggressively on failures
- Prefer fetching during normal hours (7 AM - 11 PM)
- Rotate user agents between days, not requests
- Match all headers to selected user agent
- Delay startup by 5-10 minutes after HA boots
- Only attempt data download after successful authentication
- Track authentication state separately from download state

### Don'ts ❌
- Don't fetch at exact same time every day
- Don't retry immediately on authentication failure
- Don't make requests during 2 AM - 6 AM regularly
- Don't use predictable timing patterns
- Don't ignore rate limit signals
- Don't switch user agents mid-session
- Don't use mobile user agents for daily automation
- Don't log sensitive authentication data
- Don't make more than 3-5 authentication attempts per day
- Don't start fetching immediately when Home Assistant boots
- Don't attempt download if authentication failed

### Detection Risk Factors 🚨
- **High Risk**: Fixed timing, immediate retries, missing headers
- **Medium Risk**: Too frequent polling, inconsistent fingerprints
- **Low Risk**: Occasional failures, human-like patterns

---

## **TESTING CHECKLIST**

Before deploying improvements:

- [ ] Delayed startup works (5-10 min random delay after HA boot)
- [ ] Startup delay is skipped if HA has been running >10 minutes
- [ ] Authentication state is tracked separately from download state
- [ ] Data download only proceeds after successful authentication
- [ ] No download attempts when authentication fails
- [ ] Authentication works with new timing patterns
- [ ] Session reuse reduces authentication frequency
- [ ] Circuit breaker prevents hammering on failures
- [ ] User agent rotation maintains consistency
- [ ] Headers match selected browser fingerprint
- [ ] Scheduling varies appropriately (±2 hours)
- [ ] Rate limits are detected and respected
- [ ] No sensitive data in logs
- [ ] Memory usage stays under control
- [ ] Error handling is specific and informative
- [ ] Cleanup happens on unload

---

## **MONITORING RECOMMENDATIONS**

Track these metrics to ensure stealth and reliability:

1. **Startup delay timing** (should vary between 5-10 minutes after HA boot)
2. **Authentication success rate** (target: >95%)
3. **Average authentications per day** (target: <2)
4. **Failed authentication rate** (alert if >10%)
5. **Average request timing** (should vary, not fixed)
6. **Session reuse rate** (target: >80% of data fetches use existing session)
7. **Circuit breaker trips** (monitor for patterns)
8. **Download attempts without authentication** (should be 0)
9. **Rate limit encounters** (should be rare)
10. **Memory usage over time** (check for leaks)

---

## **RISK ASSESSMENT**

### Current State
- **Detection Risk**: MEDIUM-HIGH (predictable patterns, fixed timing)
- **Security Risk**: MEDIUM (password logging, no session cleanup)
- **Reliability Risk**: MEDIUM (basic error handling, no circuit breaker)

### After Phase 1-2 Implementation
- **Detection Risk**: LOW (human-like patterns, smart timing)
- **Security Risk**: LOW (secure logging, proper cleanup)
- **Reliability Risk**: LOW (robust error handling, circuit breaker)

---

**Note:** ESB Networks may update their authentication flow or add additional anti-automation measures at any time. This integration should be considered for personal use only, and users should respect any rate limits or terms of service imposed by ESB Networks.
