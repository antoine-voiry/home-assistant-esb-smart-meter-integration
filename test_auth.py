#!/usr/bin/env python3
"""Standalone test script for ESB Smart Meter authentication."""
import asyncio
import json
import logging
import re
import sys
from io import StringIO
import csv

import aiohttp
from bs4 import BeautifulSoup
## this is solve at https://github.com/badger707/esb-smart-meter-reading-automation
## sign in is https://myaccount.esbnetworks.ie/signin-oidc
# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
_LOGGER = logging.getLogger(__name__)

# Constants
ESB_LOGIN_URL = "https://myaccount.esbnetworks.ie/"
ESB_AUTH_BASE_URL = (
    "https://login.esbnetworks.ie/esbntwkscustportalprdb2c01.onmicrosoft.com"
    "/B2C_1A_signup_signin"
)
ESB_DATA_URL = "https://myaccount.esbnetworks.ie/DataHub/DownloadHdf"
TIMEOUT = 30


async def test_login(username: str, password: str) -> dict:
    """Test login to ESB and return cookies."""
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }

    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    
    async with aiohttp.ClientSession() as session:
        try:
            # Step 1: Get initial page and CSRF token
            _LOGGER.info("=" * 80)
            _LOGGER.info("STEP 1: Getting CSRF token from ESB login page")
            _LOGGER.info("=" * 80)
            
            async with session.get(
                ESB_LOGIN_URL,
                headers=headers,
                allow_redirects=True,
                timeout=timeout,
            ) as response:
                _LOGGER.info(f"Response status: {response.status}")
                _LOGGER.info(f"Response URL: {response.url}")
                _LOGGER.debug(f"Response headers: {dict(response.headers)}")
                
                response.raise_for_status()
                content = await response.text()
                
                # Save the HTML for inspection
                with open("step1_initial_page.html", "w") as f:
                    f.write(content)
                _LOGGER.info("Saved initial page to step1_initial_page.html")
                
                # Look for SETTINGS
                settings_match = re.findall(r"(?<=var SETTINGS = )\S*;", content)
                if not settings_match:
                    _LOGGER.error("Could not find SETTINGS in page")
                    _LOGGER.error("Content preview (first 1000 chars):")
                    _LOGGER.error(content[:1000])
                    raise ValueError("Could not find SETTINGS in ESB login page")
                
                settings = json.loads(settings_match[0][:-1])
                _LOGGER.info(f"Found SETTINGS: {json.dumps(settings, indent=2)}")
                
                if "csrf" not in settings or "transId" not in settings:
                    raise ValueError("Missing required authentication tokens")

            # Step 2: Submit login credentials
            _LOGGER.info("\n" + "=" * 80)
            _LOGGER.info("STEP 2: Submitting login credentials")
            _LOGGER.info("=" * 80)
            
            headers["x-csrf-token"] = settings["csrf"]
            headers["Referer"] = str(response.url)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            
            login_url = (
                f"{ESB_AUTH_BASE_URL}/SelfAsserted?"
                f"tx={settings['transId']}&p=B2C_1A_signup_signin"
            )
            _LOGGER.info(f"Login URL: {login_url}")
            
            login_data = {
                "signInName": username,
                "password": password,
                "request_type": "RESPONSE",
            }
            
            async with session.post(
                login_url,
                data=login_data,
                headers=headers,
                timeout=timeout,
            ) as response:
                _LOGGER.info(f"Response status: {response.status}")
                _LOGGER.info(f"Response URL: {response.url}")
                _LOGGER.debug(f"Response headers: {dict(response.headers)}")
                
                content = await response.text()
                with open("step2_login_response.html", "w") as f:
                    f.write(content)
                _LOGGER.info("Saved login response to step2_login_response.html")
                
                response.raise_for_status()

            # Step 3: Confirm login
            _LOGGER.info("\n" + "=" * 80)
            _LOGGER.info("STEP 3: Confirming login")
            _LOGGER.info("=" * 80)
            
            confirm_params = {
                "rememberMe": False,
                "csrf_token": settings["csrf"],
                "tx": settings["transId"],
                "p": "B2C_1A_signup_signin",
            }
            confirm_url = (
                f"{ESB_AUTH_BASE_URL}/api/CombinedSigninAndSignup/confirmed"
            )
            _LOGGER.info(f"Confirm URL: {confirm_url}")
            _LOGGER.info(f"Confirm params: {confirm_params}")
            
            async with session.get(
                confirm_url,
                params=confirm_params,
                headers=headers,
                timeout=timeout,
            ) as response:
                _LOGGER.info(f"Response status: {response.status}")
                _LOGGER.info(f"Response URL: {response.url}")
                _LOGGER.debug(f"Response headers: {dict(response.headers)}")
                
                response.raise_for_status()
                content = await response.text()
                
                with open("step3_confirm_response.html", "w") as f:
                    f.write(content)
                _LOGGER.info("Saved confirm response to step3_confirm_response.html")
                
                soup = BeautifulSoup(content, "html.parser")
                form = soup.find("form", {"id": "auto"})
                
                if not form:
                    _LOGGER.error("Could not find auto-submit form")
                    _LOGGER.error("Content preview (first 1000 chars):")
                    _LOGGER.error(content[:1000])
                    raise ValueError("Could not find auto-submit form in ESB response")
                
                state_input = form.find("input", {"name": "state"})
                client_info_input = form.find("input", {"name": "client_info"})
                code_input = form.find("input", {"name": "code"})
                
                if not state_input or not client_info_input or not code_input:
                    _LOGGER.error("Missing required form fields")
                    raise ValueError("Missing required form fields in ESB response")
                
                state = state_input.get("value")
                client_info = client_info_input.get("value")
                code = code_input.get("value")
                action_url = form.get("action")
                
                _LOGGER.info(f"Form action URL: {action_url}")
                _LOGGER.info(f"State: {state[:50]}..." if state and len(state) > 50 else f"State: {state}")
                _LOGGER.info(f"Code: {code[:50]}..." if code and len(code) > 50 else f"Code: {code}")
                
                if not all([state, client_info, code, action_url]):
                    raise ValueError("Empty values in required form fields")

            # Step 4: Submit final form to signin-oidc endpoint
            _LOGGER.info("\n" + "=" * 80)
            _LOGGER.info("STEP 4: Submitting final authentication form")
            _LOGGER.info("=" * 80)
            
            # The actual endpoint is signin-oidc, not the action URL from the form
            signin_oidc_url = "https://myaccount.esbnetworks.ie/signin-oidc"
            
            final_data = {
                "state": state,
                "client_info": client_info,
                "code": code,
            }
            
            # Update headers for this final POST
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            headers["Origin"] = "https://login.esbnetworks.ie"
            headers["Referer"] = "https://login.esbnetworks.ie/"
            
            _LOGGER.info(f"Posting to: {signin_oidc_url}")
            _LOGGER.info(f"Data keys: {list(final_data.keys())}")
            
            async with session.post(
                signin_oidc_url,
                data=final_data,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
            ) as response:
                _LOGGER.info(f"Response status: {response.status}")
                _LOGGER.info(f"Response URL: {response.url}")
                _LOGGER.debug(f"Response headers: {dict(response.headers)}")
                
                content = await response.text()
                with open("step4_final_response.html", "w") as f:
                    f.write(content)
                _LOGGER.info("Saved final response to step4_final_response.html")
                
                response.raise_for_status()
                
                _LOGGER.info("\n" + "=" * 80)
                _LOGGER.info("✅ AUTHENTICATION SUCCESSFUL!")
                _LOGGER.info("=" * 80)
                
                # Get all cookies from the session
                all_cookies = {}
                for cookie in session.cookie_jar:
                    all_cookies[cookie.key] = cookie.value
                
                _LOGGER.info(f"Cookies obtained: {list(all_cookies.keys())}")
                return all_cookies

        except aiohttp.ClientError as err:
            _LOGGER.error(f"❌ Network error: {err}")
            raise
        except Exception as err:
            _LOGGER.error(f"❌ Error: {err}", exc_info=True)
            raise


async def test_fetch_data(session: aiohttp.ClientSession, mprn: str) -> str:
    """Test fetching data from ESB."""
    _LOGGER.info("\n" + "=" * 80)
    _LOGGER.info("FETCHING DATA")
    _LOGGER.info("=" * 80)
    
    data_url = f"{ESB_DATA_URL}?mprn={mprn}"
    _LOGGER.info(f"Data URL: {data_url}")
    
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    
    async with session.get(data_url, timeout=timeout) as response:
        _LOGGER.info(f"Response status: {response.status}")
        _LOGGER.info(f"Response headers: {dict(response.headers)}")
        
        response.raise_for_status()
        csv_data = await response.text()
        
        with open("data_response.csv", "w") as f:
            f.write(csv_data)
        _LOGGER.info("Saved data to data_response.csv")
        
        # Parse and show first few rows
        reader = csv.DictReader(StringIO(csv_data))
        rows = list(reader)
        _LOGGER.info(f"Total rows: {len(rows)}")
        if rows:
            _LOGGER.info(f"First row: {rows[0]}")
            if len(rows) > 1:
                _LOGGER.info(f"Last row: {rows[-1]}")
        
        return csv_data


async def main():
    """Main test function."""
    if len(sys.argv) < 4:
        print("Usage: python test_auth.py <username> <password> <mprn>")
        print("Example: python test_auth.py user@example.com mypassword 123456789")
        sys.exit(1)
    
    username = sys.argv[1]
    password = sys.argv[2]
    mprn = sys.argv[3]
    
    _LOGGER.info("Starting ESB Smart Meter authentication test")
    _LOGGER.info(f"Username: {username}")
    _LOGGER.info(f"MPRN: {mprn}")
    
    try:
        # Test login - returns cookies from the authenticated session
        cookies = await test_login(username, password)
        
        _LOGGER.info("\n" + "=" * 80)
        _LOGGER.info("✅ ALL TESTS PASSED!")
        _LOGGER.info("=" * 80)
        
    except Exception as err:
        _LOGGER.error(f"\n❌ TEST FAILED: {err}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
