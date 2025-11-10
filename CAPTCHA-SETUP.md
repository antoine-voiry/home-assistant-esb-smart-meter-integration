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

### Step 2: Extract Cookies

#### Option A: Using Browser Console (Recommended)

1. With the ESB website still open and logged in, press **F12** (or **Cmd+Option+I** on Mac) to open Developer Tools
2. Click on the **Console** tab
3. Type the following command and press Enter:
   ```javascript
   document.cookie
   ```
4. The console will display a long string of text - this is your cookie data
5. **Right-click** on the output and select **Copy**

#### Option B: Using the Bookmarklet (Easier!)

1. Create a new bookmark in your browser
2. Set the bookmark URL to:
   ```javascript
   javascript:(function(){const c=document.cookie;if(!c){alert('No cookies found. Make sure you are logged in.');return;}prompt('Copy these cookies (Ctrl+C or Cmd+C):',c);})();
   ```
3. Name it "Copy ESB Cookies" or similar
4. After logging in to ESB Networks, click this bookmark
5. A popup will appear with your cookies - copy them

### Step 3: Provide Cookies to Home Assistant

1. In Home Assistant, go to **Settings** → **Devices & Services**
2. Find **ESB Smart Meter** in your integrations list
3. Click the **three dots** menu (⋮) next to ESB Smart Meter
4. Select **Configure**
5. Choose **"Provide Session Cookies (CAPTCHA Bypass)"**
6. Paste the cookie string you copied in Step 2
7. Click **Submit**

### Step 4: Verify It's Working

- The persistent notification should disappear
- Your ESB Smart Meter sensors should update within a few minutes
- Check the sensor entities to see if they show energy data

## Cookie String Format

A valid cookie string looks like this:
```
.AspNetCore.Antiforgery.abcd1234=xyz789...; ARRAffinity=abcd1234...; .AspNetCore.Session=xyz789...
```

**Key points:**
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
1. Try again, making sure to copy the entire cookie string
2. Use the bookmarklet method (Option B) instead
3. Make sure you're still logged in when copying cookies

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
- Cookies are stored locally in your Home Assistant installation
- They automatically expire after ~7 days for security
- Home Assistant uses these cookies exactly as your browser would

## Bookmarklet Code (Advanced)

For reference, here's the full bookmarklet code with comments:

```javascript
javascript:(function(){
  // Get all cookies from current page
  const cookies = document.cookie;
  
  // Check if any cookies exist
  if (!cookies || cookies.length === 0) {
    alert('No cookies found. Make sure you are logged in to ESB Networks.');
    return;
  }
  
  // Display cookies in a prompt dialog for easy copying
  prompt(
    'Copy these cookies (Ctrl+C or Cmd+C):\n\nThen paste in Home Assistant integration settings.',
    cookies
  );
})();
```

To install:
1. Create new bookmark
2. Paste the entire code above as the bookmark URL
3. Name it appropriately

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
