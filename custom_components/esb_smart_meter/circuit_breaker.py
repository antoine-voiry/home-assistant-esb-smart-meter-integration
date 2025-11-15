"""Circuit breaker implementation for ESB Smart Meter integration."""

import logging
from datetime import datetime
from typing import Optional

from .const import (
    CIRCUIT_BREAKER_FAILURES,
    CIRCUIT_BREAKER_MAX_TIMEOUT,
    CIRCUIT_BREAKER_TIMEOUT,
    MAX_AUTH_ATTEMPTS_PER_DAY,
)

_LOGGER = logging.getLogger(__name__)


class CircuitBreaker:
    """Circuit breaker to prevent hammering the API after failures."""

    def __init__(self, hass=None, mprn=None) -> None:
        """Initialize circuit breaker.
        
        Args:
            hass: Home Assistant instance (optional, for notifications)
            mprn: Meter Point Reference Number (optional, for notifications)
        """
        self._failure_count = 0
        self._last_failure_time: Optional[datetime] = None
        self._daily_attempts = 0
        self._daily_attempts_reset_time: Optional[datetime] = None
        self._is_open = False
        self._hass = hass
        self._mprn = mprn
        self._notification_sent = False

    def can_attempt(self) -> bool:
        """Check if we can attempt a request."""
        now = datetime.now()

        # Reset daily counter if it's a new day
        if self._daily_attempts_reset_time is None or now.date() > self._daily_attempts_reset_time.date():
            self._daily_attempts = 0
            self._daily_attempts_reset_time = now

        # Check daily limit
        if self._daily_attempts >= MAX_AUTH_ATTEMPTS_PER_DAY:
            _LOGGER.warning(
                "Circuit breaker: Daily authentication limit reached (%d/%d)",
                self._daily_attempts,
                MAX_AUTH_ATTEMPTS_PER_DAY,
            )
            return False

        # Check if circuit is open
        if self._is_open and self._last_failure_time:
            # Calculate backoff time with exponential growth
            backoff_time = min(
                CIRCUIT_BREAKER_TIMEOUT * (2 ** (self._failure_count - 1)),
                CIRCUIT_BREAKER_MAX_TIMEOUT,
            )
            elapsed = (now - self._last_failure_time).total_seconds()

            if elapsed < backoff_time:
                remaining = backoff_time - elapsed
                _LOGGER.debug(
                    "Circuit breaker open: waiting %.0f more seconds before retry (failures: %d)",
                    remaining,
                    self._failure_count,
                )
                return False

            # Enough time has passed, try half-open state
            _LOGGER.info(
                "Circuit breaker: attempting recovery after %d failures",
                self._failure_count,
            )
            self._is_open = False

        return True

    def record_success(self) -> None:
        """Record a successful attempt."""
        was_open = self._is_open
        self._failure_count = 0
        self._is_open = False
        self._daily_attempts += 1
        self._notification_sent = False  # Reset notification flag
        
        # Dismiss notification if circuit was open
        if was_open and self._hass and self._mprn:
            try:
                if hasattr(self._hass, 'async_create_task') and callable(self._hass.async_create_task):
                    self._hass.async_create_task(self._dismiss_circuit_notification())
            except (RuntimeError, AttributeError) as err:
                _LOGGER.debug("Could not create notification task: %s", err)
        
        _LOGGER.debug(
            "Circuit breaker: Success recorded (daily attempts: %d/%d)",
            self._daily_attempts,
            MAX_AUTH_ATTEMPTS_PER_DAY,
        )

    async def _dismiss_circuit_notification(self) -> None:
        """Dismiss the circuit breaker notification."""
        try:
            from .const import DOMAIN
            await self._hass.services.async_call(
                "persistent_notification",
                "dismiss",
                {"notification_id": f"{DOMAIN}_circuit_breaker_{self._mprn}"},
            )
        except Exception as err:
            _LOGGER.debug("Could not dismiss circuit breaker notification: %s", err)

    def record_failure(self) -> None:
        """Record a failed attempt."""
        self._failure_count += 1
        self._last_failure_time = datetime.now()
        self._daily_attempts += 1

        if self._failure_count >= CIRCUIT_BREAKER_FAILURES:
            self._is_open = True
            backoff_time = min(
                CIRCUIT_BREAKER_TIMEOUT * (2 ** (self._failure_count - 1)),
                CIRCUIT_BREAKER_MAX_TIMEOUT,
            )
            _LOGGER.warning(
                "Circuit breaker opened after %d failures. Will retry in %.0f seconds",
                self._failure_count,
                backoff_time,
            )
            
            # Send notification when circuit opens (only once per opening)
            if self._hass and self._mprn and not self._notification_sent:
                try:
                    if hasattr(self._hass, 'async_create_task') and callable(self._hass.async_create_task):
                        self._hass.async_create_task(
                            self._send_circuit_open_notification(backoff_time)
                        )
                except (RuntimeError, AttributeError) as err:
                    _LOGGER.debug("Could not create notification task: %s", err)
                self._notification_sent = True

    async def _send_circuit_open_notification(self, backoff_seconds: float) -> None:
        """Send notification that circuit breaker has opened."""
        minutes = int(backoff_seconds / 60)
        hours = backoff_seconds / 3600
        
        if hours >= 1:
            time_str = f"{hours:.1f} hours"
        else:
            time_str = f"{minutes} minutes"
        
        try:
            from .const import DOMAIN
            await self._hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "notification_id": f"{DOMAIN}_circuit_breaker_{self._mprn}",
                    "title": "⚠️ ESB Smart Meter: Too Many Failures",
                    "message": (
                        f"The integration has experienced **{self._failure_count} consecutive failures** "
                        f"for MPRN `{self._mprn}`.\n\n"
                        f"**Automatic retry paused for {time_str}** to prevent overloading the ESB API.\n\n"
                        "**Common causes:**\n"
                        "• Network connectivity issues\n"
                        "• ESB Networks server downtime\n"
                        "• Temporary service interruption\n\n"
                        "The integration will **automatically resume** after the cooldown period. "
                        "This notification will clear when updates succeed."
                    ),
                },
            )
        except Exception as err:
            _LOGGER.debug("Could not send circuit breaker notification: %s", err)
