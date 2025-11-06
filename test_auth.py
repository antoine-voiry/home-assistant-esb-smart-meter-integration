#!/usr/bin/env python3
"""Standalone test script for ESB Smart Meter authentication using actual sensor.py code."""
import asyncio
import logging
import sys
import os
from pathlib import Path

import aiohttp

# Try to load dotenv if available
try:
    from dotenv import load_dotenv
    load_dotenv()
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

# Add the custom_components directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from custom_components.esb_smart_meter.sensor import ESBDataApi

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
_LOGGER = logging.getLogger(__name__)


class MockHomeAssistant:
    """Mock Home Assistant for testing."""
    
    def __init__(self):
        """Initialize mock hass."""
        self.loop = asyncio.get_event_loop()
    
    async def async_add_executor_job(self, func, *args):
        """Run a job in the executor."""
        return await self.loop.run_in_executor(None, func, *args)


async def test_authentication(username: str, password: str, mprn: str):
    """Test ESB authentication using the actual ESBDataApi class."""
    _LOGGER.info("=" * 80)
    _LOGGER.info("Testing ESB Smart Meter Authentication")
    _LOGGER.info("=" * 80)
    _LOGGER.info(f"Username: {username}")
    _LOGGER.info(f"MPRN: {mprn}")
    _LOGGER.info("")
    
    mock_hass = MockHomeAssistant()
    
    async with aiohttp.ClientSession() as session:
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
            
            # This will run the complete 8-step authentication flow
            esb_data = await esb_api.fetch()
            
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
            _LOGGER.error("")
            _LOGGER.error("=" * 80)
            _LOGGER.error(f"❌ TEST FAILED: {err}")
            _LOGGER.error("=" * 80)
            _LOGGER.error("", exc_info=True)
            raise


async def main():
    """Main test function."""
    # Try to get credentials from command line arguments first
    if len(sys.argv) >= 4:
        username = sys.argv[1]
        password = sys.argv[2]
        mprn = sys.argv[3]
    # Otherwise try environment variables
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
            print("   python test_auth.py <username> <password> <mprn>")
            print("\n   Example:")
            print("   python test_auth.py user@example.com mypassword 12345678901")
            print("\n2. Environment variables (create a .env file):")
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
        print("\n" + "=" * 80)
        print("ESB Smart Meter Authentication Test")
        print("=" * 80)
        print("\n❌ ERROR: No credentials provided!")
        print("\nUsage: python test_auth.py <username> <password> <mprn>")
        print("\nExample:")
        print("  python test_auth.py user@example.com mypassword 12345678901")
        print("\nAlternatively, install python-dotenv and use a .env file:")
        print("  pip install python-dotenv")
        print("  cp .env.example .env")
        print("  # Edit .env with your credentials")
        print("\nThis script uses the actual ESBDataApi code from sensor.py")
        print("to test the complete 8-step authentication flow.")
        print("=" * 80 + "\n")
        sys.exit(1)
    
    try:
        await test_authentication(username, password, mprn)
        sys.exit(0)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
