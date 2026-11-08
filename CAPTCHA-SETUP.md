# ESB Smart Meter - Manual Cookie Setup

This guide explains how to manually provide session cookies when ESB Networks requires CAPTCHA verification.

## When Do I Need This?

If you see a notification saying "ESB Smart Meter: Manual Login Required", it means ESB Networks is requiring CAPTCHA verification for login. This typically happens:

- On first setup
- After extended periods of inactivity
- If ESB detects unusual access patterns
- Periodically as part of their security measures

## Quick Setup (5 minutes)

### Step 1: Log in to ESB Networks

1. Open your web browser (Chrome, Firefox, Safari, or Edge)
2. Go to: https://myaccount.esbnetworks.ie
3. Log in with your ESB Networks credentials
4. Complete the CAPTCHA challenge if shown

### Step 2: Copy the Cookie and User-Agent Request Headers

ESB's login cookies are HttpOnly, so `document.cookie` (and any bookmarklet built on it) cannot see
them. Copy them from the Network tab instead:

1. With the ESB website still open and logged in, press **F12** (or **Cmd+Option+I** on Mac) to open Developer Tools
2. Click on the **Network** tab, then reload the page
3. Click the first request to `myaccount.esbnetworks.ie`
4. Under **Request Headers**, copy the whole **Cookie** value
5. From the same request, copy the **User-Agent** value

### Step 3: Provide Cookies to Home Assistant

1. In Home Assistant, go to **Settings** → **Devices & Services**
2. Find **ESB Smart Meter** in your integrations list
3. Click the **three dots** menu (⋮) next to ESB Smart Meter
4. Select **Configure**
5. Choose **"Provide Session Cookies (CAPTCHA Bypass)"**
6. Paste the Cookie value into the first field and the User-Agent into the second
7. Click **Submit**

If ESB rejects the pasted session, the integration does not fall back to a scripted login (that would
bring the CAPTCHA back) for 24 hours, even across restarts. It shows the CAPTCHA notification again,
so copy a fresh Cookie header. A timeout or an ESB outage does not count as a rejection: the session
is kept and tried again on the next update.

### Step 4: Verify It's Working

- The persistent notification should disappear
- Your ESB Smart Meter sensors should update within a few minutes
- Check the sensor entities to see if they show energy data

## Cookie String Format

A valid cookie string looks like this:
```
.AspNetCore.Antiforgery.abcd1234=xyz789...; ARRAffinity=abcd1234...; .AspNetCore.Cookies=xyz789...
```

**Key points:**
- It must include `.AspNetCore.Cookies`, the login cookie that `document.cookie` cannot see
- A leading `Cookie:` and line breaks from the copy are fine
- Multiple cookies separated by semicolons (`;`)
- Each cookie has format: `name=value`
- The string can be quite long (100-500+ characters)
- Contains special characters like dots, dashes, equals signs

## Troubleshooting

### "Invalid cookies" error

**Possible causes:**
- Cookies weren't copied completely
- You logged out or cookies expired before copying
- Browser auto-formatted the text when copying

**Solutions:**
1. Try again, making sure to copy the entire Cookie request header from the Network tab
2. Make sure you're still logged in when copying cookies
3. In the logs, `Session validation failed - ESB answered HTTP 302 (redirect host: login.esbnetworks.ie)`
   means the login cookie was not among those pasted

### Sensors still not updating

**Wait time:** It can take 5-10 minutes for the first update after providing cookies.

**Check logs:**
1. Go to Settings → System → Logs
2. Look for messages from `custom_components.esb_smart_meter`
3. If you see "Session saved successfully", cookies were accepted
4. If you see authentication errors, try providing fresh cookies

### Cookies expire too quickly

**Solution:** This is normal. Cookies typically last 7 days. After they expire:
1. You'll receive another notification
2. Simply repeat the manual cookie process
3. The integration will automatically use the new cookies

## Security Notes

- **Cookies contain authentication tokens** - treat them like passwords
- Never share your cookie string with others
- Cookies are stored locally in `<config>/.storage/esb_smart_meter/`, which config backups usually exclude
- Before 1.4.1 they were stored in `<config>/esb_smart_meter/`. If you back up `/config` with git,
  remove `esb_smart_meter/session_cache_*.json` from your repository history and log out of
  ESB Networks to invalidate the session
- They automatically expire after ~7 days for security
- Home Assistant uses these cookies exactly as your browser would

## Privacy & Legal

This method is:
- ✅ **Legal** - You're using your own credentials
- ✅ **Safe** - Cookies stay on your local Home Assistant
- ✅ **Private** - No third-party services involved
- ✅ **Ethical** - Respects ESB's security measures (you solve the CAPTCHA)

You are essentially doing what your browser does naturally - storing and reusing authentication cookies.

## Need More Help?

- Check Home Assistant logs for detailed error messages
- Verify your ESB Networks credentials are correct
- Try logging in manually through a browser first
- Open an issue on GitHub if problems persist

## Session Persistence

Once you provide valid cookies, the integration will:
- ✅ Save them securely for reuse
- ✅ Automatically use them for all future requests
- ✅ Persist them across Home Assistant restarts
- ✅ Keep them valid for up to 7 days
- ✅ Notify you when they expire and new ones are needed

This means you typically only need to do this process once every week or so!
