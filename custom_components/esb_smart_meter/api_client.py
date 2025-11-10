"""API client for ESB Smart Meter integration."""

import asyncio
import csv
import json
import logging
import re
from io import StringIO
from typing import Any
from urllib.parse import urlencode

import aiohttp
from bs4 import BeautifulSoup
from homeassistant.core import HomeAssistant

from .circuit_breaker import CircuitBreaker
from .const import (DEFAULT_TIMEOUT, ESB_AUTH_BASE_URL, ESB_CONSUMPTION_URL,
                    ESB_DOWNLOAD_URL, ESB_LOGIN_URL, ESB_MYACCOUNT_URL,
                    ESB_TOKEN_URL, MAX_CSV_SIZE_MB, RATE_LIMIT_BACKOFF_MINUTES)
from .models import ESBData
from .utils import get_human_like_delay, get_random_user_agent

_LOGGER = logging.getLogger(__name__)


class ESBDataApi:
    """Class for handling the data retrieval from ESB using async aiohttp."""

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        mprn: str,
    ) -> None:
        """Initialize the data object."""
        self._hass = hass
        self._session = session
        self._username = username
        self._password = password
        self._mprn = mprn
        self._timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)
        self._circuit_breaker = CircuitBreaker()

    async def __login(self) -> dict[str, str]:
        """Login to ESB and return cookies (following the complete 8-step flow)."""
        # Select a random user agent and use it consistently throughout the session
        user_agent = get_random_user_agent()
        _LOGGER.debug("Using User-Agent: %s", user_agent)
        _LOGGER.debug("Session cookie jar type: %s", type(self._session.cookie_jar))
        _LOGGER.debug(
            "Session cookie jar unsafe: %s",
            getattr(self._session.cookie_jar, "unsafe", "N/A"),
        )

        headers = {"User-Agent": user_agent}

        try:
            # REQUEST 1: Get CSRF token and settings
            _LOGGER.debug("Request 1: Getting CSRF token from ESB")
            _LOGGER.debug("Request 1 URL: %s", ESB_LOGIN_URL)

            # Add Referer header for the initial request
            initial_headers = {
                **headers,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }

            async with self._session.get(
                ESB_LOGIN_URL,
                headers=initial_headers,
                allow_redirects=True,
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()
                _LOGGER.debug("Request 1 response status: %s", response.status)
                _LOGGER.debug("Request 1 final URL: %s", response.url)
                _LOGGER.debug(
                    "Request 1 cookies set: %s",
                    [f"{c.key}={c.value[:20]}..." for c in self._session.cookie_jar],
                )
                content = await response.text()
                _LOGGER.debug("Request 1 response length: %d bytes", len(content))
                settings_match = re.findall(r"(?<=var SETTINGS = )\S*;", content)
                if not settings_match:
                    raise ValueError("Could not find SETTINGS in ESB login page")
                settings = json.loads(settings_match[0][:-1])

                # Validate required settings fields
                if "csrf" not in settings or "transId" not in settings:
                    raise ValueError("Missing required authentication tokens")

                _LOGGER.debug("Got CSRF token and transaction ID")
                # Security: Do not log sensitive tokens
                _LOGGER.debug("CSRF token length: %d", len(settings.get("csrf", "")))
                _LOGGER.debug("Transaction ID: [REDACTED]")

            # Add human-like delay between requests
            delay = get_human_like_delay()
            await asyncio.sleep(delay)

            # REQUEST 2: POST SelfAsserted - Login with credentials
            # Construct URL with proper query parameters
            login_params = {"tx": settings["transId"], "p": "B2C_1A_signup_signin"}
            login_url = f"{ESB_AUTH_BASE_URL}/SelfAsserted?{urlencode(login_params)}"
            login_headers = {
                **headers,
                "x-csrf-token": settings["csrf"],
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "en-US,en;q=0.5",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": "https://login.esbnetworks.ie",
                "Referer": str(response.url),  # Add Referer from Request 1
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            }
            login_data = {
                "signInName": self._username,
                "password": self._password,
                "request_type": "RESPONSE",
            }
            _LOGGER.debug("Request 2: Submitting login credentials")
            _LOGGER.debug("Request 2 URL: %s", login_url)
            _LOGGER.debug(
                "Request 2 cookies available: %s",
                [f"{c.key}" for c in self._session.cookie_jar],
            )
            # Security: Do not log credentials
            _LOGGER.debug(
                "Request 2 data: signInName=[REDACTED], password=[REDACTED], request_type=RESPONSE"
            )
            _LOGGER.debug(
                "Request 2 headers: %s",
                {
                    k: v
                    for k, v in login_headers.items()
                    if k.lower() not in ("user-agent", "x-csrf-token")
                },
            )
            async with self._session.post(
                login_url,
                data=login_data,
                headers=login_headers,
                timeout=self._timeout,
            ) as response:
                _LOGGER.debug("Request 2 response status: %s", response.status)
                _LOGGER.debug("Request 2 response URL: %s", response.url)
                if response.status != 200:
                    error_content = await response.text()
                    _LOGGER.error("Request 2 failed with status %s", response.status)
                    _LOGGER.error("Request 2 error response: %s", error_content[:1000])
                response.raise_for_status()
                login_response = await response.text()
                _LOGGER.debug("Request 2 response preview: %s", login_response[:500])
                _LOGGER.debug("Login successful")

            # REQUEST 3: GET CombinedSigninAndSignup/confirmed
            confirm_params = {
                "rememberMe": "false",
                "csrf_token": settings["csrf"],
                "tx": settings["transId"],
                "p": "B2C_1A_signup_signin",
            }
            confirm_url = f"{ESB_AUTH_BASE_URL}/api/CombinedSigninAndSignup/confirmed"
            confirm_headers = {
                **headers,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
            }
            _LOGGER.debug("Request 3: Confirming login")
            _LOGGER.debug("Request 3 URL: %s", confirm_url)
            _LOGGER.debug("Request 3 params: %s", confirm_params)
            async with self._session.get(
                confirm_url,
                params=confirm_params,
                headers=confirm_headers,
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()
                _LOGGER.debug("Request 3 response status: %s", response.status)
                _LOGGER.debug("Request 3 response URL: %s", response.url)
                content = await response.text()
                _LOGGER.debug("Request 3 response length: %d bytes", len(content))
                _LOGGER.debug(
                    "Request 3 response preview (first 500 chars): %s", content[:500]
                )

                # Check if CAPTCHA is present
                if (
                    "g-recaptcha-response" in content
                    or "captcha.html" in content
                    or 'error_requiredFieldMissing":"Please confirm you are not a robot'
                    in content
                ):
                    _LOGGER.error("CAPTCHA detected in ESB response!")
                    _LOGGER.error(
                        "ESB Networks has added CAPTCHA protection to their login flow."
                    )
                    _LOGGER.error("This prevents automated authentication.")
                    _LOGGER.error(
                        "Response contains: captcha.html or g-recaptcha-response"
                    )
                    raise ValueError(
                        "ESB Networks requires CAPTCHA verification. "
                        "Automated login is currently not possible. "
                        "This may be temporary rate limiting or a permanent security change."
                    )

                soup = BeautifulSoup(content, "html.parser")
                form = soup.find("form", {"id": "auto"})
                if not form:
                    _LOGGER.error(
                        "Could not find form with id='auto'. Looking for any forms..."
                    )
                    all_forms = soup.find_all("form")
                    _LOGGER.error("Found %d forms in response", len(all_forms))
                    for idx, f in enumerate(all_forms):
                        _LOGGER.error(
                            "Form %d: id=%s, action=%s",
                            idx,
                            f.get("id"),
                            f.get("action"),
                        )
                    _LOGGER.debug("Full HTML response:\n%s", content)
                    raise ValueError("Could not find auto-submit form in ESB response")

                # Extract form fields
                state_input = form.find("input", {"name": "state"})
                client_info_input = form.find("input", {"name": "client_info"})
                code_input = form.find("input", {"name": "code"})

                if not state_input or not client_info_input or not code_input:
                    raise ValueError("Missing required form fields in ESB response")

                state = state_input.get("value")
                client_info = client_info_input.get("value")
                code = code_input.get("value")
                action_url = form.get("action")

                if not all([state, client_info, code, action_url]):
                    raise ValueError("Empty values in required form fields")

                _LOGGER.debug("Extracted form data")

            # Add human-like delay
            delay = get_human_like_delay()
            await asyncio.sleep(delay)

            # REQUEST 4: POST signin-oidc
            signin_headers = {
                **headers,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://login.esbnetworks.ie",
                "Referer": "https://login.esbnetworks.ie/",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-site",
            }
            signin_data = {
                "state": state,
                "client_info": client_info,
                "code": code,
            }
            _LOGGER.debug("Request 4: Submitting signin-oidc")
            async with self._session.post(
                action_url,
                data=signin_data,
                headers=signin_headers,
                allow_redirects=False,
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()
                _LOGGER.debug("Signin-oidc successful")

            # REQUEST 5: GET myaccount.esbnetworks.ie
            myaccount_headers = {
                **headers,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://login.esbnetworks.ie/",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-site",
            }
            _LOGGER.debug("Request 5: Accessing my account page")
            async with self._session.get(
                ESB_MYACCOUNT_URL,
                headers=myaccount_headers,
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()
                _LOGGER.debug("My account page loaded")

            # Add human-like delay
            delay = get_human_like_delay()
            await asyncio.sleep(delay)

            # REQUEST 6: GET Api/HistoricConsumption
            consumption_headers = {
                **headers,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": f"{ESB_MYACCOUNT_URL}/",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
            }
            _LOGGER.debug("Request 6: Loading historic consumption page")
            async with self._session.get(
                ESB_CONSUMPTION_URL,
                headers=consumption_headers,
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()
                _LOGGER.debug("Historic consumption page loaded")

            # Add human-like delay
            delay = get_human_like_delay()
            await asyncio.sleep(delay)

            # REQUEST 7: GET file download token
            token_headers = {
                **headers,
                "Accept": "*/*",
                "X-Returnurl": ESB_CONSUMPTION_URL,
                "Referer": ESB_CONSUMPTION_URL,
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            }
            _LOGGER.debug("Request 7: Getting file download token")
            async with self._session.get(
                ESB_TOKEN_URL,
                headers=token_headers,
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()
                token_data = await response.json()
                download_token = token_data.get("token")
                if not download_token:
                    raise ValueError("Failed to get download token")
                _LOGGER.debug("Got download token")

            _LOGGER.info(
                "Authentication completed successfully for user: %s", self._username
            )
            return {"download_token": download_token, "user_agent": user_agent}

        except aiohttp.ClientError as err:
            _LOGGER.error("Network error during login: %s", err)
            raise
        except json.JSONDecodeError as err:
            _LOGGER.error("Invalid JSON in ESB response: %s", err)
            raise ValueError("Invalid authentication response from ESB") from err
        except (KeyError, ValueError, AttributeError) as err:
            _LOGGER.error("Error parsing ESB response: %s", err)
            raise

    async def __fetch_data(self, download_token: str, user_agent: str) -> str:
        """Fetch the power usage data from ESB with size limits (REQUEST 8)."""
        try:
            download_headers = {
                "User-Agent": user_agent,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.5",
                "Content-Type": "application/json",
                "Referer": ESB_CONSUMPTION_URL,
                "X-Returnurl": ESB_CONSUMPTION_URL,
                "X-Xsrf-Token": download_token,
                "Origin": ESB_MYACCOUNT_URL,
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            }
            payload = {"mprn": self._mprn, "searchType": "intervalkw"}

            _LOGGER.debug("Request 8: Downloading CSV data for MPRN %s", self._mprn)

            async with self._session.post(
                ESB_DOWNLOAD_URL,
                headers=download_headers,
                json=payload,
                timeout=self._timeout,
            ) as response:
                response.raise_for_status()

                # Check content size to prevent memory exhaustion
                content_length = response.headers.get("Content-Length")
                if content_length:
                    size_mb = int(content_length) / (1024 * 1024)
                    if size_mb > MAX_CSV_SIZE_MB:
                        raise ValueError(
                            f"CSV response too large: {size_mb:.2f}MB "
                            f"exceeds {MAX_CSV_SIZE_MB}MB limit"
                        )

                csv_data = await response.text()

                # Double-check actual size after download
                actual_size_mb = len(csv_data.encode("utf-8")) / (1024 * 1024)
                if actual_size_mb > MAX_CSV_SIZE_MB:
                    raise ValueError(
                        f"CSV data too large: {actual_size_mb:.2f}MB "
                        f"exceeds {MAX_CSV_SIZE_MB}MB limit"
                    )

                _LOGGER.debug("CSV data fetched successfully (%.2f MB)", actual_size_mb)
                return csv_data

        except aiohttp.ClientError as err:
            _LOGGER.error("Error fetching data: %s", err)
            raise

    def __csv_to_dict(self, csv_data: str) -> list[dict[str, Any]]:
        """Convert CSV data to list of dictionaries."""
        try:
            reader = csv.DictReader(StringIO(csv_data))
            data = list(reader)
            _LOGGER.debug("Parsed %d rows from CSV data", len(data))
            return data
        except Exception as err:
            _LOGGER.error("Error parsing CSV data: %s", err)
            raise

    async def fetch(self) -> ESBData:
        """Fetch data with circuit breaker, retry logic, and conditional download."""
        # Check circuit breaker before attempting
        if not self._circuit_breaker.can_attempt():
            raise RuntimeError("Circuit breaker is open. Too many recent failures.")

        try:
            # PHASE 1: Authentication only
            _LOGGER.debug("Attempting authentication to ESB")
            auth_result = await self.__login()

            # If we get here, authentication succeeded
            _LOGGER.info("Authentication successful")

            # PHASE 2: Conditional data download (only if auth succeeded)
            download_token = auth_result.get("download_token")
            user_agent = auth_result.get("user_agent")

            if not download_token:
                raise ValueError(
                    "Authentication succeeded but no download token received"
                )

            _LOGGER.debug(
                "Proceeding with data download after successful authentication"
            )
            csv_data = await self.__fetch_data(download_token, user_agent)
            data = await self._hass.async_add_executor_job(self.__csv_to_dict, csv_data)

            # Success! Record in circuit breaker
            self._circuit_breaker.record_success()

            return ESBData(data=data)

        except ValueError as err:
            # Don't retry on data validation errors (invalid CSV, size limits, etc)
            _LOGGER.error("Data validation error: %s", err)
            self._circuit_breaker.record_failure()
            raise

        except aiohttp.ClientResponseError as err:
            # Handle HTTP errors
            self._circuit_breaker.record_failure()

            if 400 <= err.status < 500:
                if err.status == 429:
                    _LOGGER.error(
                        "Rate limited by ESB (429). Waiting %d minutes before next attempt.",
                        RATE_LIMIT_BACKOFF_MINUTES,
                    )
                else:
                    _LOGGER.error(
                        "Client error %d: %s. Authentication may have failed.",
                        err.status,
                        err.message,
                    )
                raise
            else:
                # 5xx server error
                _LOGGER.error("Server error %d: %s", err.status, err.message)
                raise

        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            # Network errors
            _LOGGER.error("Network error during fetch: %s", err)
            self._circuit_breaker.record_failure()
            raise

        except Exception as err:
            # Unexpected errors
            _LOGGER.error("Unexpected error during fetch: %s", err, exc_info=True)
            self._circuit_breaker.record_failure()
            raise
