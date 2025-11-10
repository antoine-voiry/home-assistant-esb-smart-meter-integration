"""Circuit breaker implementation for ESB Smart Meter integration."""

import logging
from datetime import datetime
from typing import Optional

from .const import (CIRCUIT_BREAKER_FAILURES, CIRCUIT_BREAKER_MAX_TIMEOUT,
                    CIRCUIT_BREAKER_TIMEOUT, MAX_AUTH_ATTEMPTS_PER_DAY)

_LOGGER = logging.getLogger(__name__)


class CircuitBreaker:
    """Circuit breaker to prevent hammering the API after failures."""

    def __init__(self) -> None:
        """Initialize circuit breaker."""
        self._failure_count = 0
        self._last_failure_time: Optional[datetime] = None
        self._daily_attempts = 0
        self._daily_attempts_reset_time: Optional[datetime] = None
        self._is_open = False

    def can_attempt(self) -> bool:
        """Check if we can attempt a request."""
        now = datetime.now()

        # Reset daily counter if it's a new day
        if (
            self._daily_attempts_reset_time is None
            or now.date() > self._daily_attempts_reset_time.date()
        ):
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
        self._failure_count = 0
        self._is_open = False
        self._daily_attempts += 1
        _LOGGER.debug(
            "Circuit breaker: Success recorded (daily attempts: %d)",
            self._daily_attempts,
        )

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
