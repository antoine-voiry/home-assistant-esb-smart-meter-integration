# ESB Smart Meter Authentication Test Script

This standalone script helps debug authentication issues with the ESB Smart Meter integration.

## Purpose

The script will:
1. Attempt to authenticate with ESB Networks
2. Save HTML responses at each step for inspection
3. If authentication succeeds, attempt to fetch meter data
4. Provide detailed logging to identify where failures occur

## Requirements

Make sure you have the required dependencies installed:

```bash
pip install aiohttp beautifulsoup4
```

Or install from the project requirements:

```bash
pip install -r requirements.txt
```

## Usage

```bash
python test_auth.py <username> <password> <mprn>
```

### Example

```bash
python test_auth.py your.email@example.com YourPassword123 1234567890
```

## Output Files

The script will create several files to help debug issues:

- `step1_initial_page.html` - The initial ESB login page
- `step2_login_response.html` - Response after submitting credentials
- `step3_confirm_response.html` - Response from the confirmation step
- `step4_final_response.html` - Final authentication response
- `data_response.csv` - The meter data (if authentication succeeds)

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
