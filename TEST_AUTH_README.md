# ESB Smart Meter Authentication Test Script

This standalone script helps test and debug authentication with the ESB Smart Meter integration using the actual production code.

## Purpose

The script will:
1. Use the actual `ESBDataApi` class from `sensor.py` (no code duplication)
2. Run the complete 8-step authentication flow
3. Download and parse meter data
4. Display usage statistics (today, last 24h, this week, month, etc.)
5. Provide detailed debug logging to identify any issues

## Requirements

Make sure you have the required dependencies installed:

```bash
pip install aiohttp beautifulsoup4 python-dotenv
```

Or install from the project requirements:

```bash
pip install -r requirements.txt
```

## Usage

### Method 1: Command Line Arguments

```bash
python test_auth.py <username> <password> <mprn>
```

**Example:**

```bash
python test_auth.py your.email@example.com YourPassword123 12345678901
```

### Method 2: Environment Variables (Recommended for esb-smart-meter-reader.py)

For the `esb-smart-meter-reader.py` script, use environment variables:

1. Copy the example environment file:
```bash
cp .env.example .env
```

2. Edit `.env` and add your credentials:
```bash
ESB_USERNAME=your.email@example.com
ESB_PASSWORD=YourPassword123
ESB_MPRN=12345678901
```

3. Run the script:
```bash
python esb-smart-meter-reader.py
```

**Note:** The `.env` file is in `.gitignore` and will never be committed to git.

## Output

The script will display:
- Authentication progress through all 8 steps
- Success/failure status
- Energy usage statistics:
  - Today's usage
  - Last 24 hours
  - This week
  - Last 7 days
  - This month
  - Last 30 days

## Understanding the Output

### Success
If everything works, you'll see:
```
✅ AUTHENTICATION SUCCESSFUL!
✅ ALL TESTS PASSED!
```

### Failure
If there's a failure, check:

1. **400 Bad Request** - Usually means:
   - The authentication flow has changed on ESB's side
   - Missing or incorrect parameters
   - Check the saved HTML files for error messages

2. **401 Unauthorized** - Wrong credentials

3. **Form not found** - The page structure has changed
   - Open the saved HTML files to see what's actually being returned

## Current Issue (HTTP 400)

The error you're experiencing:
```
400, message='Bad Request', url='https://login.esbnetworks.ie/.../oauth2/v2.0/authorize?...'
```

This suggests the OAuth2 flow may have changed. The test script will help identify:
- What parameters are expected
- What's actually being sent
- Whether the initial login page structure has changed

## Troubleshooting

1. **Check saved HTML files** - Look for error messages or different form structures
2. **Compare working vs non-working** - If it worked before, compare the URLs and parameters
3. **Network issues** - Make sure you can access https://myaccount.esbnetworks.ie/ in a browser
4. **Credentials** - Verify you can log in manually through a web browser

## Next Steps

Once you run the test script:

1. Check which step it fails at
2. Examine the corresponding HTML file
3. Look for error messages in the HTML
4. Check if the form structure has changed
5. Share the log output and HTML files for further debugging

## Security Note

⚠️ The saved HTML files may contain session tokens. Don't share them publicly!
