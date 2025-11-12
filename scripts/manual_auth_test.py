#!/usr/bin/env python3
"""
Standalone Manual Authentication Test Script for ESB Smart Meter Integration.

This script tests the ESB Smart Meter authentication flow outside of Home Assistant
by using the actual production ESBDataApi code. It's useful for debugging authentication
issues and verifying that credentials work before setting up the integration.

Usage:
    python scripts/manual_auth_test.py <username> <password> <mprn>

    Or use environment variables in .env file:
    ESB_USERNAME=your@email.com
    ESB_PASSWORD=yourpassword
    ESB_MPRN=12345678901
"""

# Standard library imports
import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path

# Third-party imports
import aiohttp

# Load environment variables from .env file if available
try:
    from dotenv import load_dotenv

    # Load .env from the parent directory (project root)
    dotenv_path = Path(__file__).parent.parent / ".env"
    load_dotenv(dotenv_path)
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

# Add the parent directory to the path so we can import custom_components
sys.path.insert(0, str(Path(__file__).parent.parent))

# Local imports from the integration
from custom_components.esb_smart_meter.sensor import ESBDataApi

# Configure logging to show detailed debug information
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
_LOGGER = logging.getLogger(__name__)


def create_esb_session() -> aiohttp.ClientSession:
    """
    Create a new aiohttp ClientSession with lenient cookie handling.

    This session uses quote_cookie=False to prevent strict cookie value quoting,
    which can cause 400 errors with Microsoft/Google/ESB authentication services.

    Returns:
        aiohttp.ClientSession: Configured session for ESB API requests
    """
    # Create a custom CookieJar with lenient cookie handling
    cookie_jar = aiohttp.CookieJar(
        quote_cookie=False,  # Prevents strict RFC compliance that breaks ESB cookies
        unsafe=False,  # Keep False unless you need IP address cookie support
    )

    # Create a new session with the custom cookie jar
    session = aiohttp.ClientSession(cookie_jar=cookie_jar)

    return session


class MockConfig:
    """
    Mock Home Assistant configuration object.

    Provides a temporary directory for session storage during testing.
    """

    def __init__(self):
        """Initialize mock config with a temporary directory."""
        self._config_dir = tempfile.mkdtemp()

    def path(self, *args):
        """
        Return a path within the config directory.

        Args:
            *args: Path components to join with the config directory

        Returns:
            str: Full path within the config directory
        """
        return os.path.join(self._config_dir, *args)


class MockHomeAssistant:
    """
    Mock Home Assistant instance for standalone testing.

    Provides the minimal interface required by ESBDataApi to function
    outside of a real Home Assistant environment.
    """

    def __init__(self):
        """Initialize mock Home Assistant with config and event loop."""
        self.loop = asyncio.get_event_loop()
        self.config = MockConfig()

    async def async_add_executor_job(self, func, *args):
        """
        Run a function in the executor (mimics Home Assistant's behavior).

        Args:
            func: Function to run
            *args: Arguments to pass to the function

        Returns:
            Result of the function execution
        """
        return await self.loop.run_in_executor(None, func, *args)


async def test_authentication(username: str, password: str, mprn: str):
    """
    Test ESB Smart Meter authentication using the actual production ESBDataApi.

    This function performs the complete 8-step authentication flow:
    1. Get CSRF token from ESB
    2. Submit login credentials
    3. Confirm login
    4. Submit signin-oidc
    5. Access my account page
    6. Load historic consumption page
    7. Get file download token
    8. Download and parse CSV data

    Args:
        username: ESB Networks account email
        password: ESB Networks account password
        mprn: Meter Point Reference Number (11 digits)

    Returns:
        bool: True if authentication and data fetch succeeded

    Raises:
        Exception: If authentication or data fetch fails
    """
    _LOGGER.info("=" * 80)
    _LOGGER.info("Testing ESB Smart Meter Authentication")
    _LOGGER.info("=" * 80)
    _LOGGER.info(f"Username: {username}")
    _LOGGER.info(f"MPRN: {mprn}")
    _LOGGER.info("")

    # Create mock Home Assistant instance
    mock_hass = MockHomeAssistant()

    # Create custom session with lenient cookie handling
    # This matches what the integration does in production
    session = create_esb_session()

    try:
        # Create the ESBDataApi instance using the actual production code
        esb_api = ESBDataApi(
            hass=mock_hass,
            session=session,
            username=username,
            password=password,
            mprn=mprn,
        )

        try:
            _LOGGER.info("Starting fetch (this will perform login + download)...")
            _LOGGER.info("")

            # Run the complete 8-step authentication flow
            esb_data = await esb_api.fetch()

            # Display success message and data summary
            _LOGGER.info("")
            _LOGGER.info("=" * 80)
            _LOGGER.info("✅ AUTHENTICATION AND DATA FETCH SUCCESSFUL!")
            _LOGGER.info("=" * 80)
            _LOGGER.info("")
            _LOGGER.info("📊 Data Summary:")
            _LOGGER.info(f"  Today:           {esb_data.today:.2f} kWh")
            _LOGGER.info(f"  Last 24 hours:   {esb_data.last_24_hours:.2f} kWh")
            _LOGGER.info(f"  This week:       {esb_data.this_week:.2f} kWh")
            _LOGGER.info(f"  Last 7 days:     {esb_data.last_7_days:.2f} kWh")
            _LOGGER.info(f"  This month:      {esb_data.this_month:.2f} kWh")
            _LOGGER.info(f"  Last 30 days:    {esb_data.last_30_days:.2f} kWh")
            _LOGGER.info("")
            _LOGGER.info("✅ All tests passed! The integration is working correctly.")
            _LOGGER.info("=" * 80)

            return True

        except Exception as err:
            # Log detailed error information
            _LOGGER.error("")
            _LOGGER.error("=" * 80)
            _LOGGER.error(f"❌ TEST FAILED: {err}")
            _LOGGER.error("=" * 80)
            _LOGGER.error("", exc_info=True)
            raise
    finally:
        # Always close the session to prevent resource leaks
        await session.close()


async def main():
    """
    Main entry point for the authentication test script.

    Loads credentials from either command line arguments or environment variables,
    then runs the authentication test.
    """
    # Try to get credentials from command line arguments first
    if len(sys.argv) >= 4:
        username = sys.argv[1]
        password = sys.argv[2]
        mprn = sys.argv[3]
    # Otherwise try environment variables from .env file
    elif DOTENV_AVAILABLE:
        username = os.getenv("ESB_USERNAME")
        password = os.getenv("ESB_PASSWORD")
        mprn = os.getenv("ESB_MPRN")

        if not all([username, password, mprn]):
            print("\n" + "=" * 80)
            print("ESB Smart Meter Authentication Test")
            print("=" * 80)
            print("\n❌ ERROR: Missing credentials!")
            print("\nYou can provide credentials in two ways:")
            print("\n1. Command line arguments:")
            print("   python scripts/manual_auth_test.py <username> <password> <mprn>")
            print("\n   Example:")
            print(
                "   python scripts/manual_auth_test.py user@example.com mypassword 12345678901"
            )
            print("\n2. Environment variables (create a .env file in project root):")
            print("   ESB_USERNAME=user@example.com")
            print("   ESB_PASSWORD=mypassword")
            print("   ESB_MPRN=12345678901")
            if not DOTENV_AVAILABLE:
                print("\n⚠️  Note: python-dotenv is not installed.")
                print("   Install it with: pip install python-dotenv")
            print("\nThis script uses the actual ESBDataApi code from sensor.py")
            print("to test the complete 8-step authentication flow.")
            print("=" * 80 + "\n")
            sys.exit(1)
    else:
        # No dotenv and no command line arguments
        print("\n" + "=" * 80)
        print("ESB Smart Meter Authentication Test")
        print("=" * 80)
        print("\n❌ ERROR: No credentials provided!")
        print(
            "\nUsage: python scripts/manual_auth_test.py <username> <password> <mprn>"
        )
        print("\nExample:")
        print(
            "  python scripts/manual_auth_test.py user@example.com mypassword 12345678901"
        )
        print("\nAlternatively, install python-dotenv and use a .env file:")
        print("  pip install python-dotenv")
        print("  # Create .env file in project root with:")
        print("  ESB_USERNAME=user@example.com")
        print("  ESB_PASSWORD=mypassword")
        print("  ESB_MPRN=12345678901")
        print("\nThis script uses the actual ESBDataApi code from sensor.py")
        print("to test the complete 8-step authentication flow.")
        print("=" * 80 + "\n")
        sys.exit(1)

    try:
        # Run the authentication test
        await test_authentication(username, password, mprn)
        print("✅ All tests passed! The integration is working correctly.")
        sys.exit(0)
    except Exception as e:
        # Display error information
        print(f"❌ An error occurred during the authentication test: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
