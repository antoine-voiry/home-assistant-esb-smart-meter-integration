"""Utility functions for ESB Smart Meter integration."""

import asyncio
import logging
import random

import aiohttp
import homeassistant.util.dt as dt_util
from homeassistant.core import HomeAssistant

from .const import (DEFAULT_TIMEOUT, HA_UPTIME_THRESHOLD, LONG_PAUSE_MAX,
                    LONG_PAUSE_MIN, LONG_PAUSE_PROBABILITY, MAX_REQUEST_DELAY,
                    MIN_REQUEST_DELAY, REQUEST_DELAY_MEAN,
                    REQUEST_DELAY_STDDEV, STARTUP_DELAY_MAX, STARTUP_DELAY_MIN)
from .user_agents import USER_AGENTS

_LOGGER = logging.getLogger(__name__)


def get_human_like_delay() -> float:
    """Generate a human-like delay using normal distribution with occasional long pauses."""
    # Use normal distribution for more realistic timing
    delay = random.gauss(REQUEST_DELAY_MEAN, REQUEST_DELAY_STDDEV)

    # Clamp to reasonable bounds
    delay = max(MIN_REQUEST_DELAY, min(MAX_REQUEST_DELAY, delay))

    # Add occasional longer pauses to mimic human reading/thinking
    if random.random() < LONG_PAUSE_PROBABILITY:
        delay += random.uniform(LONG_PAUSE_MIN, LONG_PAUSE_MAX)
        _LOGGER.debug("Adding long pause: %.2f seconds total", delay)

    return delay


async def get_startup_delay(hass: HomeAssistant) -> float:
    """Calculate startup delay based on HA uptime to avoid immediate requests after boot."""
    # Check how long Home Assistant has been running
    try:
        uptime_seconds = (
            dt_util.utcnow()
            - hass.data.get("homeassistant", {}).get("start_time", dt_util.utcnow())
        ).total_seconds()
    except (AttributeError, TypeError, KeyError):
        # If we can't determine uptime, assume HA just started
        uptime_seconds = 0

    # If HA has been running for less than 10 minutes, add a startup delay
    if uptime_seconds < HA_UPTIME_THRESHOLD:
        delay = random.uniform(STARTUP_DELAY_MIN, STARTUP_DELAY_MAX)
        _LOGGER.info(
            "Home Assistant uptime is %.1f seconds (< %d), adding startup delay of %.1f seconds",
            uptime_seconds,
            HA_UPTIME_THRESHOLD,
            delay,
        )
        return delay

    _LOGGER.debug(
        "Home Assistant uptime is %.1f seconds, skipping startup delay", uptime_seconds
    )
    return 0


def get_random_user_agent() -> str:
    """Get a random user agent from popular browsers."""
    return random.choice(USER_AGENTS)


async def create_esb_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Creates a new, non-shared, lenient aiohttp ClientSession.

    The recommended approach for a custom component making external requests
    where cookie isolation/leniency is required.
    """
    # 1. Create a custom CookieJar.
    # The 'quote_cookie=False' flag prevents aiohttp from strictly enforcing
    # cookie value quoting, which is usually the source of 400 errors
    # with services like MSFT/Google.
    cookie_jar = aiohttp.CookieJar(
        quote_cookie=False,
        unsafe=False,  # Keep this False unless you specifically need IP address cookie support
    )

    # 2. Create a ClientTimeout.
    timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)

    # 3. Create the session.
    # Using a custom cookie_jar means this session is independent
    # from Home Assistant's global session, preventing cookie conflicts.
    session = aiohttp.ClientSession(
        cookie_jar=cookie_jar,
        timeout=timeout,
    )

    return session
