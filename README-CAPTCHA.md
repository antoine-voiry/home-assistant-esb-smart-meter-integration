# reCAPTCHA Handling Options for ESB Smart Meter Integration

**Date:** November 10, 2025  
**Status:** Research & Analysis  
**Purpose:** Evaluate options for handling reCAPTCHA challenges from ESB Networks

---

## Table of Contents

1. [Current Situation](#current-situation)
2. [Available Solutions](#available-solutions)
3. [Detailed Analysis](#detailed-analysis)
4. [Legal & Ethical Considerations](#legal--ethical-considerations)
5. [Recommendation](#recommendation)
6. [Alternative Approaches](#alternative-approaches)

---

## Current Situation

ESB Networks has implemented reCAPTCHA protection on their login system to prevent automated access. The integration currently:

- ✅ Detects reCAPTCHA presence (`g-recaptcha-response`, `captcha.html`)
- ✅ Provides clear error messages to users
- ✅ Implements 24-hour backoff after detection
- ❌ Cannot bypass or solve reCAPTCHA automatically

**Detection Logic (api_client.py:200-221):**
```python
if (
    "g-recaptcha-response" in content
    or "captcha.html" in content
    or 'error_requiredFieldMissing":"Please confirm you are not a robot' in content
):
    raise ValueError(
        "ESB Networks requires CAPTCHA verification. "
        "Automated login is currently not possible."
    )
```

---

## Available Solutions

### 1. **Playwright-reCAPTCHA** ⭐ (Primary Option)

**Repository:** https://github.com/Xewdy444/Playwright-reCAPTCHA

**Description:** Python library for solving reCAPTCHA v2 and v3 using Playwright browser automation.

#### Features:
- **reCAPTCHA v2 Solving:**
  - Audio challenge: Transcribes audio using Google Speech Recognition API (free)
  - Image challenge: Uses CapSolver API for image classification (paid)
- **reCAPTCHA v3 Solving:**
  - Waits for browser to make POST request to Google's reload endpoint
  - Parses response to extract `g-recaptcha-response` token
- **Async Support:** Compatible with asyncio (matches Home Assistant architecture)
- **Multi-language:** Supports 9 languages including English
- **Active Development:** 429 stars, regularly updated (last commit: 2 months ago)
- **MIT License:** Permissive open-source license

#### Technical Requirements:
```bash
pip install playwright-recaptcha
# Requires FFmpeg for audio transcription
apt-get install ffmpeg  # Debian/Ubuntu
brew install ffmpeg     # macOS
```

#### Implementation Approach:
```python
from playwright.async_api import async_playwright
from playwright_recaptcha import recaptchav2

async with async_playwright() as p:
    browser = await p.firefox.launch()
    page = await browser.new_page()
    
    async with recaptchav2.AsyncSolver(page) as solver:
        await page.goto("https://login.esbnetworks.ie/...")
        token = await solver.solve_recaptcha(wait=True)
        # Use token in form submission
```

#### Pros:
- ✅ Free for audio challenge method
- ✅ Async/await compatible with Home Assistant
- ✅ Well-documented with examples
- ✅ Active community (60 forks, 6 contributors)
- ✅ Handles both v2 and v3 reCAPTCHA
- ✅ MIT license allows commercial use

#### Cons:
- ❌ **Requires full browser automation** (Playwright)
  - Heavy dependency (~100MB+ with browser binaries)
  - Not suitable for headless Home Assistant installations
  - Resource intensive (memory, CPU)
- ❌ **Requires FFmpeg** (additional system dependency)
- ❌ **Image challenge requires paid API** (CapSolver)
- ❌ **Google may detect and block** automated audio transcription
- ❌ **Slower than aiohttp** (browser overhead)
- ❌ **Architecture mismatch:** Current integration uses aiohttp, not browser automation

---

### 2. **CapSolver API** 💰 (Commercial Service)

**Website:** https://www.capsolver.com/

**Description:** AI-powered CAPTCHA solving service with REST API.

#### Features:
- Supports reCAPTCHA v2, v3, Enterprise
- Also handles hCaptcha, FunCaptcha, DataDome
- REST API integration (no browser required)
- Browser extensions available
- 99.9% success rate (claimed)

#### Pricing:
- **Pay-per-solve model:**
  - reCAPTCHA v2: ~$0.90 per 1000 solves
  - reCAPTCHA v3: ~$0.50 per 1000 solves
  - Enterprise: ~$2.00 per 1000 solves
- **Packages:** $6 to $200+ with volume discounts
- **Free trial:** Usually 100-200 free solves

#### Implementation:
```python
import aiohttp

async def solve_recaptcha(site_key: str, page_url: str, api_key: str) -> str:
    """Solve reCAPTCHA using CapSolver API."""
    async with aiohttp.ClientSession() as session:
        # 1. Create task
        async with session.post(
            "https://api.capsolver.com/createTask",
            json={
                "clientKey": api_key,
                "task": {
                    "type": "ReCaptchaV2TaskProxyless",
                    "websiteURL": page_url,
                    "websiteKey": site_key,
                }
            }
        ) as resp:
            data = await resp.json()
            task_id = data["taskId"]
        
        # 2. Poll for solution
        while True:
            await asyncio.sleep(3)
            async with session.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id}
            ) as resp:
                result = await resp.json()
                if result["status"] == "ready":
                    return result["solution"]["gRecaptchaResponse"]
```

#### Pros:
- ✅ **No browser required** - works with aiohttp
- ✅ **Lightweight** - just API calls
- ✅ **High success rate** (AI-powered)
- ✅ **Supports all reCAPTCHA versions**
- ✅ **Minimal code changes needed**
- ✅ **Compatible with current architecture**

#### Cons:
- ❌ **Costs money** - ongoing operational cost
- ❌ **External dependency** - service availability risk
- ❌ **Privacy concerns** - sending user credentials/session to third party
- ❌ **Against reCAPTCHA ToS** - explicitly prohibited
- ❌ **Ethical issues** - circumventing intended security

---

### 3. **2Captcha / Anti-Captcha Services** 💰

**Similar services:** 2Captcha, Anti-Captcha, DeathByCaptcha, ImageTyperz

#### Features:
- Human-powered CAPTCHA solving
- API integration
- Similar pricing to CapSolver ($1-3 per 1000 solves)
- 10-30 second solve times

#### Pros:
- ✅ Very high success rate (humans solving)
- ✅ API-based (no browser needed)
- ✅ Multiple service options

#### Cons:
- ❌ **Costs money**
- ❌ **Slower** (human solving takes 10-30 seconds)
- ❌ **Ethical concerns** (exploiting low-wage labor)
- ❌ **Privacy concerns**
- ❌ **Against ToS**

---

### 4. **Browser Extension Approach** 🌐

**Concept:** Run Home Assistant with browser automation using extensions like:
- CapSolver Browser Extension
- Buster: Captcha Solver for Humans
- NopeCHA

#### Implementation:
- Install Playwright/Puppeteer in Home Assistant
- Load browser with extension
- Extension auto-solves CAPTCHAs

#### Pros:
- ✅ Some extensions are free (Buster)
- ✅ Can work with paid services

#### Cons:
- ❌ **Requires full browser** (Playwright/Puppeteer)
- ❌ **Very heavy for Home Assistant**
- ❌ **Complex setup** for users
- ❌ **Not suitable for headless systems**
- ❌ **Maintenance burden**

---

### 5. **Machine Learning / AI Models** 🤖

**Concept:** Train or use pre-trained models to solve CAPTCHAs.

#### Options:
- TensorFlow/PyTorch models for image recognition
- Speech-to-text models for audio challenges
- Open-source CAPTCHA solvers

#### Pros:
- ✅ No external service dependency
- ✅ No recurring costs
- ✅ Privacy-friendly

#### Cons:
- ❌ **Very complex** to implement and maintain
- ❌ **Large model files** (100MB-1GB+)
- ❌ **High computational requirements**
- ❌ **Low success rate** for modern reCAPTCHA
- ❌ **Google actively detects and blocks** ML-based solvers
- ❌ **Not practical for Home Assistant**

---

### 6. **Token Persistence / Session Reuse** 🔄

**Concept:** Extend session lifetime to avoid repeated logins.

#### Implementation:
- Save authentication cookies/tokens
- Reuse sessions for extended periods
- Only login when session expires

#### Current State:
The integration already implements session reuse via `aiohttp.ClientSession` cookie jar.

#### Enhancement Ideas:
- Persist cookies to disk across Home Assistant restarts
- Extend session lifetime to weeks/months
- Monitor session validity before making requests

#### Pros:
- ✅ **No CAPTCHA solving needed** if session stays valid
- ✅ **Lightweight** and simple
- ✅ **No external dependencies**
- ✅ **Privacy-friendly**
- ✅ **No ethical concerns**

#### Cons:
- ❌ **Doesn't solve initial login** CAPTCHA
- ❌ **Session will eventually expire**
- ❌ **ESB may invalidate sessions aggressively**

---

### 7. **User-Assisted Flow** 👤

**Concept:** Prompt user to solve CAPTCHA when needed.

#### Implementation Options:

**Option A: Notification + Manual Web Login**
```python
# When CAPTCHA detected:
# 1. Send Home Assistant notification
# 2. User manually logs in via browser
# 3. User provides session cookie to integration
# 4. Integration uses cookie
```

**Option B: Embedded Browser View**
```python
# When CAPTCHA detected:
# 1. Open Home Assistant Lovelace panel with embedded browser
# 2. User solves CAPTCHA in embedded view
# 3. Integration captures token
# 4. Continues authentication
```

**Option C: OAuth-like Flow**
```python
# When CAPTCHA detected:
# 1. Generate authentication request URL
# 2. Send persistent notification with link
# 3. User opens link in their browser
# 4. User solves CAPTCHA and logs in
# 5. Integration receives callback with token
```

#### Pros:
- ✅ **Completely legal and ethical**
- ✅ **No additional costs**
- ✅ **Respects ESB's security measures**
- ✅ **Simple to implement**
- ✅ **No heavy dependencies**
- ✅ **Privacy-friendly** (no third parties)
- ✅ **Complies with ToS**

#### Cons:
- ❌ **Not fully automated** (requires user intervention)
- ❌ **User experience impact** (occasional manual action needed)
- ❌ **Users must be available** when CAPTCHA triggered

---

## Detailed Analysis

### Architecture Compatibility Matrix

| Solution | aiohttp Compatible | Home Assistant Friendly | Headless Support | Lightweight |
|----------|-------------------|------------------------|------------------|-------------|
| Playwright-reCAPTCHA | ❌ No (requires browser) | ❌ No (too heavy) | ⚠️ Possible (but slow) | ❌ No (~150MB) |
| CapSolver API | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes (~1KB code) |
| 2Captcha/Anti-Captcha | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes (~1KB code) |
| Browser Extensions | ❌ No | ❌ No | ❌ No | ❌ No (~150MB) |
| ML Models | ⚠️ Possible | ❌ No (models too large) | ⚠️ Possible | ❌ No (100MB-1GB) |
| Session Reuse | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes (0 bytes) |
| User-Assisted | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Yes (~2KB code) |

### Cost Analysis (Annual for Daily Logins)

Assuming 365 logins per year (once per day) and 50% CAPTCHA rate:

| Solution | Setup Cost | Annual Cost | Notes |
|----------|-----------|-------------|-------|
| Playwright-reCAPTCHA (audio) | $0 | $0 | Free, but may get blocked |
| Playwright-reCAPTCHA (image) | $0 | ~$0.16 | 182 solves × $0.90/1000 |
| CapSolver | $0 | ~$0.16 - $0.36 | Depends on v2 vs v3 |
| 2Captcha | $0 | ~$0.18 - $0.55 | Slightly higher prices |
| Browser Extensions | Varies | $0 - $50/yr | Some free, some paid |
| ML Models | High (dev time) | $0 | One-time development cost |
| Session Reuse | $0 | $0 | Free enhancement |
| User-Assisted | $0 | $0 | Completely free |

**Cost is minimal**, but ethical/legal concerns are more significant.

### Success Rate Comparison

| Solution | Expected Success Rate | Speed (avg) |
|----------|----------------------|-------------|
| Playwright-reCAPTCHA (audio) | 60-80% | 15-30s |
| Playwright-reCAPTCHA (image/CapSolver) | 95-99% | 10-20s |
| CapSolver API | 95-99% | 5-15s |
| 2Captcha (human) | 98-99% | 15-40s |
| Browser Extensions | 90-95% | 10-30s |
| ML Models | 20-50% | 5-10s |
| Session Reuse | 100% (when session valid) | <1s |
| User-Assisted | 100% | 30-120s (human time) |

---

## Legal & Ethical Considerations

### Terms of Service (ToS)

**reCAPTCHA Terms (Google):**
> You may not [...] use any automated means to access, use or scrape the CAPTCHA Service, or attempt to circumvent the CAPTCHA Service or any security features.

**ESB Networks Likely Position:**
- ESB implemented CAPTCHA specifically to prevent automation
- Circumventing CAPTCHA likely violates their acceptable use policy
- Could potentially lead to account suspension

### Ethical Considerations

#### ❌ **Against Automation:**
- **Playwright-reCAPTCHA**: Directly circumvents intended security
- **CapSolver/2Captcha**: Explicitly violates ToS
- **ML Models**: Same as above, plus technically challenging

#### ⚠️ **Gray Area:**
- **Browser Extensions**: User-initiated but still automated
- **Session Reuse**: Extends valid sessions (probably acceptable)

#### ✅ **Ethical:**
- **User-Assisted Flow**: User solves CAPTCHA themselves
- Respects ESB's security measures
- No ToS violations
- No third-party privacy concerns

### Legal Risks

| Solution | Legal Risk | Account Ban Risk | Enforcement Likelihood |
|----------|-----------|------------------|----------------------|
| Playwright-reCAPTCHA | Medium | Medium-High | Medium |
| CapSolver/2Captcha | High | High | High |
| Browser Extensions | Medium | Medium | Medium |
| ML Models | High | High | High |
| Session Reuse | Low | Low | Low |
| User-Assisted | None | None | None |

**Note:** While unlikely to result in legal action, account suspension is a real risk. Users losing access to their energy data is a significant concern.

---

## Recommendation

### 🎯 **Recommended Approach: Multi-Layered Strategy**

#### **Phase 1: Session Persistence (Immediate - No Code Yet)** ✅

**Implementation:**
1. Enhance cookie persistence across restarts
2. Store session tokens in Home Assistant data directory
3. Implement session validity checking
4. Extend session reuse window to maximum possible

**Benefits:**
- Reduces login frequency by 90%+
- No CAPTCHA solving needed for existing sessions
- Zero cost, lightweight, ethical
- Respects ESB security

**Code Location:** `custom_components/esb_smart_meter/cache.py` or new `session.py`

---

#### **Phase 2: User-Assisted CAPTCHA Flow (Recommended)** ⭐

**Implementation:**
1. Detect CAPTCHA as currently done
2. Send persistent Home Assistant notification with:
   - Clear explanation
   - Deep link to ESB login page
   - Instructions to copy session cookie
3. Provide config flow UI for cookie input
4. Resume integration with provided session

**User Experience:**
```
Notification Title: "ESB Smart Meter - Manual Login Required"
Message: "ESB Networks requires CAPTCHA verification. 
         Please visit https://myaccount.esbnetworks.ie 
         and log in manually. Then provide your session 
         cookie in the integration settings."
Actions: [Open ESB Website] [Configure Integration]
```

**Alternative (More Advanced):**
- Home Assistant panel with embedded iframe
- User logs in directly in HA UI
- Integration captures session automatically

**Benefits:**
- ✅ Completely legal and ethical
- ✅ Respects ESB security measures
- ✅ No external dependencies or costs
- ✅ No account ban risk
- ✅ Privacy-friendly
- ✅ Lightweight implementation
- ✅ Works on all Home Assistant installations

**Drawbacks:**
- Requires user intervention (maybe once per month)
- Not fully automated

---

#### **Phase 3: Optional Advanced Session Management**

**Features:**
1. Detect session expiry proactively
2. Send notification before session expires
3. Intelligent retry with exponential backoff
4. Multi-session support for redundancy

---

### ⚠️ **NOT Recommended: Automated CAPTCHA Solving**

**Reasons:**
1. **Legal/Ethical:** Violates ToS and ethical guidelines
2. **Reliability:** ESB can detect and block these methods
3. **Complexity:** Browser automation is too heavy for HA
4. **Cost:** Ongoing operational costs (if using paid services)
5. **Privacy:** Third-party services see user data
6. **Maintenance:** High risk of breakage with ESB changes

**When to Reconsider:**
- If ESB provides an official API (no CAPTCHA needed)
- If ESB explicitly permits automation
- If ESB implements OAuth or similar user-friendly auth

---

### 🤔 **Conditional Option: CapSolver API as Opt-In**

**If you must provide automated CAPTCHA solving:**

**Implementation:**
1. Make it **opt-in** via configuration
2. Require explicit user acknowledgment of risks:
   - ToS violation
   - Account ban risk  
   - Privacy implications
3. Document all risks clearly in README
4. Use only as fallback after user-assisted flow

**Configuration:**
```yaml
esb_smart_meter:
  captcha_solving:
    enabled: false  # Disabled by default
    method: "manual"  # Options: manual, capsolver
    capsolver_api_key: !secret capsolver_key  # Only if method=capsolver
    accept_risks: false  # Must be explicitly set to true
```

**Benefits:**
- Gives advanced users the option
- Default behavior remains ethical
- Clear risk disclosure

**Risks:**
- May encourage ToS violations
- Could reflect poorly on the integration
- Account ban liability

---

## Alternative Approaches

### 1. **ESB API Request** 📧

**Action:** Contact ESB Networks to request official API access.

**Benefits:**
- ✅ Official support
- ✅ No CAPTCHA issues
- ✅ Stable, documented API
- ✅ No ToS concerns

**Challenges:**
- ❌ May not respond or may decline
- ❌ Could take months/years
- ❌ May require business relationship

**Recommendation:** Worth attempting regardless of chosen technical solution.

---

### 2. **Community Advocacy** 👥

**Action:** Build user base requesting API access.

**Approach:**
1. Collect user stories about benefits
2. Demonstrate responsible use
3. Coordinate formal request to ESB
4. Highlight smart home integration benefits

**Timeline:** Long-term (6-12+ months)

---

### 3. **Browser Bookmarklet / Cookie Export** 🔖

**Action:** Provide users with bookmarklet to easily extract session cookies.

**Implementation:**
```javascript
// Bookmarklet code
javascript:(function(){
  const cookies = document.cookie;
  alert('Copy this cookie:\n\n' + cookies);
  navigator.clipboard.writeText(cookies);
})();
```

**User Experience:**
1. User logs into ESB manually (solving CAPTCHA)
2. Clicks bookmarklet
3. Cookie copied to clipboard
4. Pastes into Home Assistant integration config
5. Integration uses cookie for authentication

**Benefits:**
- ✅ Very simple for users
- ✅ Completely legal
- ✅ Works on all platforms
- ✅ One-time setup (cookie lasts weeks/months)

---

## Implementation Roadmap

### ✅ **Immediate Actions (Don't Implement Yet, Per User Request)**

1. **Enhanced Session Persistence**
   - Add session token storage to disk
   - Implement session validation checks
   - Extend cookie lifetime where possible

2. **User-Assisted Flow Foundation**
   - Design notification system
   - Create cookie input UI in config flow
   - Document manual login process

### 📋 **Short-term (1-2 months)**

1. Test session persistence improvements
2. Implement basic user-assisted CAPTCHA flow
3. Create user documentation with screenshots
4. Add helpful error messages and recovery instructions

### 🔮 **Long-term (3-6 months)**

1. Monitor CAPTCHA frequency and patterns
2. Evaluate if automated solving becomes necessary
3. Consider opt-in CapSolver integration if needed
4. Reach out to ESB for official API access

---

## Conclusion

### **Best Solution: User-Assisted Flow + Enhanced Session Persistence**

**Rationale:**
1. **Legal & Ethical:** Fully compliant with ToS
2. **Reliable:** 100% success rate
3. **Lightweight:** Minimal dependencies
4. **Privacy:** No third-party involvement
5. **Sustainable:** Won't break with ESB changes
6. **User-Friendly:** Clear instructions and notifications
7. **Low Maintenance:** Simple code, fewer moving parts

**Trade-off:**
- Users must manually authenticate when CAPTCHA appears
- Frequency: Likely once per week/month, reduced to rare events with session persistence

**Implementation Complexity:** Low to Medium
**Maintenance Burden:** Low
**Risk Level:** None

---

### **Conditional: CapSolver API (Opt-In Only)**

**Only if:**
- Users strongly demand full automation
- Implemented as opt-in with clear risk warnings
- Default behavior remains user-assisted

**Not Recommended As Primary Solution Due To:**
- Legal/ethical concerns
- Account ban risk
- Privacy implications
- Goes against ESB's clear intent

---

### **NOT Recommended: Playwright-reCAPTCHA**

**Despite being technically interesting:**
- ❌ Too heavy for Home Assistant (150MB+ dependencies)
- ❌ Requires browser binaries and FFmpeg
- ❌ Not compatible with current aiohttp architecture
- ❌ Still violates ToS
- ❌ Will likely be detected and blocked by ESB
- ❌ Overkill for this use case

**When it might make sense:**
- Desktop application (not Home Assistant integration)
- User explicitly running browser automation
- One-off scripts, not long-running service

---

## Final Recommendation Summary

### 🏆 **Implement This:**

```
Priority 1: Enhanced Session Persistence
├─ Store cookies across restarts
├─ Validate sessions before use
└─ Maximize session lifetime

Priority 2: User-Assisted CAPTCHA Flow
├─ Persistent notification on CAPTCHA detection
├─ Config flow for manual cookie input
├─ Cookie extraction bookmarklet
└─ Clear documentation with screenshots

Priority 3 (Optional): Opt-in CapSolver
├─ Only if community demands it
├─ Disabled by default
├─ Require explicit risk acknowledgment
└─ Clear documentation of risks
```

### ❌ **Don't Implement:**
- Playwright-reCAPTCHA (too heavy, wrong architecture)
- Browser extensions (not suitable for HA)
- ML models (overkill, unreliable)

### 📧 **Parallel Action:**
- Contact ESB Networks requesting official API access
- Document integration benefits and responsible use
- Build case for smart home integration value

---

**This approach balances:**
- ✅ User experience (mostly automated)
- ✅ Reliability (proven, simple)
- ✅ Ethics (respects ESB security)
- ✅ Legality (no ToS violations)
- ✅ Maintainability (lightweight, stable)
- ✅ Privacy (no third parties)

**Users accept:**
- Occasional manual intervention (rare with session persistence)
- Following ESB's intended security model

**You avoid:**
- Account bans
- Legal issues
- Heavy dependencies
- Ongoing costs
- Complex maintenance
- Ethical compromises
