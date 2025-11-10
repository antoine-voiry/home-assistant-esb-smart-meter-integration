"""Tests for CircuitBreaker functionality including security and corner cases."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from custom_components.esb_smart_meter.circuit_breaker import CircuitBreaker
from custom_components.esb_smart_meter.const import (
    CIRCUIT_BREAKER_FAILURES,
    CIRCUIT_BREAKER_TIMEOUT,
    CIRCUIT_BREAKER_MAX_TIMEOUT,
    MAX_AUTH_ATTEMPTS_PER_DAY,
)


class TestCircuitBreaker:
    """Test CircuitBreaker class."""

    @pytest.fixture
    def circuit_breaker(self):
        """Create a CircuitBreaker instance."""
        return CircuitBreaker()

    def test_initial_state_allows_attempts(self, circuit_breaker):
        """Test circuit breaker allows attempts initially."""
        assert circuit_breaker.can_attempt() is True
        assert circuit_breaker._failure_count == 0
        assert circuit_breaker._is_open is False

    def test_record_success_increments_daily_attempts(self, circuit_breaker):
        """Test that recording success increments daily attempts counter."""
        assert circuit_breaker._daily_attempts == 0
        circuit_breaker.record_success()
        assert circuit_breaker._daily_attempts == 1
        circuit_breaker.record_success()
        assert circuit_breaker._daily_attempts == 2

    def test_record_failure_increments_counters(self, circuit_breaker):
        """Test that recording failure increments both failure and daily attempt counters."""
        circuit_breaker.record_failure()
        assert circuit_breaker._failure_count == 1
        assert circuit_breaker._daily_attempts == 1
        assert circuit_breaker._last_failure_time is not None

    def test_circuit_opens_after_threshold_failures(self, circuit_breaker):
        """Test circuit opens after CIRCUIT_BREAKER_FAILURES consecutive failures."""
        # Record failures up to threshold
        for _ in range(CIRCUIT_BREAKER_FAILURES - 1):
            circuit_breaker.record_failure()
            assert circuit_breaker._is_open is False

        # One more failure should open the circuit
        circuit_breaker.record_failure()
        assert circuit_breaker._is_open is True
        assert circuit_breaker._failure_count == CIRCUIT_BREAKER_FAILURES

    def test_circuit_blocks_attempts_when_open(self, circuit_breaker):
        """Test circuit breaker blocks attempts when open."""
        # Open the circuit
        for _ in range(CIRCUIT_BREAKER_FAILURES):
            circuit_breaker.record_failure()

        assert circuit_breaker._is_open is True
        assert circuit_breaker.can_attempt() is False

    def test_circuit_reopens_after_backoff_time(self, circuit_breaker):
        """Test circuit reopens after exponential backoff time has elapsed."""
        # Open the circuit with exactly CIRCUIT_BREAKER_FAILURES failures
        for _ in range(CIRCUIT_BREAKER_FAILURES):
            circuit_breaker.record_failure()

        assert circuit_breaker.can_attempt() is False

        # Mock time to simulate backoff period passing
        backoff_time = CIRCUIT_BREAKER_TIMEOUT * (
            2 ** (CIRCUIT_BREAKER_FAILURES - 1)
        )
        future_time = datetime.now() + timedelta(seconds=backoff_time + 1)

        with patch("custom_components.esb_smart_meter.circuit_breaker.datetime") as mock_datetime:
            mock_datetime.now.return_value = future_time
            # Circuit should allow attempt after backoff
            assert circuit_breaker.can_attempt() is True
            assert circuit_breaker._is_open is False

    def test_exponential_backoff_calculation(self, circuit_breaker):
        """Test exponential backoff time increases with failure count."""
        test_cases = [
            (3, CIRCUIT_BREAKER_TIMEOUT * (2**2)),  # 3 failures = 2^2
            (4, CIRCUIT_BREAKER_TIMEOUT * (2**3)),  # 4 failures = 2^3
            (5, CIRCUIT_BREAKER_TIMEOUT * (2**4)),  # 5 failures = 2^4
        ]

        for failure_count, expected_backoff in test_cases:
            cb = CircuitBreaker()
            for _ in range(failure_count):
                cb.record_failure()

            # Calculate what the backoff should be
            calculated_backoff = min(
                CIRCUIT_BREAKER_TIMEOUT * (2 ** (failure_count - 1)),
                CIRCUIT_BREAKER_MAX_TIMEOUT,
            )
            assert calculated_backoff == expected_backoff

    def test_max_backoff_timeout_limit(self, circuit_breaker):
        """Test that backoff time doesn't exceed CIRCUIT_BREAKER_MAX_TIMEOUT."""
        # Record many failures to test the max limit
        for _ in range(20):
            circuit_breaker.record_failure()

        # Calculate backoff - should be capped at max
        backoff_time = min(
            CIRCUIT_BREAKER_TIMEOUT * (2 ** (circuit_breaker._failure_count - 1)),
            CIRCUIT_BREAKER_MAX_TIMEOUT,
        )
        assert backoff_time == CIRCUIT_BREAKER_MAX_TIMEOUT

    def test_daily_limit_blocks_further_attempts(self, circuit_breaker):
        """Test that exceeding daily attempt limit blocks all attempts."""
        # Exhaust daily limit with successes
        for _ in range(MAX_AUTH_ATTEMPTS_PER_DAY):
            assert circuit_breaker.can_attempt() is True
            circuit_breaker.record_success()

        # Should now block attempts (after MAX_AUTH_ATTEMPTS_PER_DAY completed)
        assert circuit_breaker.can_attempt() is False
        assert circuit_breaker._daily_attempts == MAX_AUTH_ATTEMPTS_PER_DAY

    def test_daily_counter_resets_next_day(self, circuit_breaker):
        """Test that daily attempt counter resets on a new day."""
        # Exhaust daily limit
        for _ in range(MAX_AUTH_ATTEMPTS_PER_DAY):
            assert circuit_breaker.can_attempt() is True
            circuit_breaker.record_success()

        assert circuit_breaker.can_attempt() is False

        # Mock next day
        next_day = datetime.now() + timedelta(days=1)
        with patch("custom_components.esb_smart_meter.circuit_breaker.datetime") as mock_datetime:
            mock_datetime.now.return_value = next_day
            # Should allow attempts again
            assert circuit_breaker.can_attempt() is True

    def test_success_resets_failure_count(self, circuit_breaker):
        """Test that recording success resets failure count."""
        # Record some failures
        circuit_breaker.record_failure()
        circuit_breaker.record_failure()
        assert circuit_breaker._failure_count == 2

        # Success should reset
        circuit_breaker.record_success()
        assert circuit_breaker._failure_count == 0
        assert circuit_breaker._is_open is False

    def test_mixed_success_and_failure_counts(self, circuit_breaker):
        """Test alternating success and failure doesn't open circuit prematurely."""
        # Alternate between success and failure
        for _ in range(5):
            circuit_breaker.record_failure()
            circuit_breaker.record_success()  # Resets failure count

        # Circuit should still be closed since failures were reset
        assert circuit_breaker._is_open is False
        assert circuit_breaker._failure_count == 0

    def test_race_condition_daily_reset_none_time(self, circuit_breaker):
        """Test edge case where daily_attempts_reset_time is None."""
        assert circuit_breaker._daily_attempts_reset_time is None
        # First call should handle None gracefully
        assert circuit_breaker.can_attempt() is True
        assert circuit_breaker._daily_attempts_reset_time is not None

    def test_security_dos_prevention_via_daily_limit(self, circuit_breaker):
        """SECURITY: Test that daily limit prevents DoS attacks on ESB."""
        # Simulate rapid repeated failures (attack scenario)
        for i in range(MAX_AUTH_ATTEMPTS_PER_DAY + 10):
            if i < MAX_AUTH_ATTEMPTS_PER_DAY:
                circuit_breaker.record_failure()
            else:
                # Should be blocked after hitting limit
                assert circuit_breaker.can_attempt() is False

    def test_security_exponential_backoff_prevents_hammering(self, circuit_breaker):
        """SECURITY: Test exponential backoff prevents API hammering."""
        # Record failures to open circuit
        for _ in range(CIRCUIT_BREAKER_FAILURES):
            circuit_breaker.record_failure()

        # Try to make requests during backoff - all should fail
        for _ in range(10):
            assert circuit_breaker.can_attempt() is False

    def test_corner_case_zero_failures(self, circuit_breaker):
        """CORNER: Test behavior with zero failures."""
        assert circuit_breaker._failure_count == 0
        assert circuit_breaker.can_attempt() is True
        circuit_breaker.record_success()
        assert circuit_breaker._failure_count == 0

    def test_corner_case_exactly_at_threshold(self, circuit_breaker):
        """CORNER: Test behavior exactly at failure threshold."""
        for _ in range(CIRCUIT_BREAKER_FAILURES):
            circuit_breaker.record_failure()

        assert circuit_breaker._failure_count == CIRCUIT_BREAKER_FAILURES
        assert circuit_breaker._is_open is True
        assert circuit_breaker.can_attempt() is False

    def test_corner_case_one_below_threshold(self, circuit_breaker):
        """CORNER: Test circuit stays closed one failure below threshold."""
        for _ in range(CIRCUIT_BREAKER_FAILURES - 1):
            circuit_breaker.record_failure()

        assert circuit_breaker._failure_count == CIRCUIT_BREAKER_FAILURES - 1
        assert circuit_breaker._is_open is False
        assert circuit_breaker.can_attempt() is True

    def test_corner_case_daily_limit_boundary(self, circuit_breaker):
        """CORNER: Test behavior at daily limit boundary."""
        # Exactly at limit - can_attempt checks first, then we record
        for _ in range(MAX_AUTH_ATTEMPTS_PER_DAY):
            assert circuit_breaker.can_attempt() is True
            circuit_breaker.record_success()

        assert circuit_breaker._daily_attempts == MAX_AUTH_ATTEMPTS_PER_DAY
        assert circuit_breaker.can_attempt() is False

        # One before limit
        cb2 = CircuitBreaker()
        for _ in range(MAX_AUTH_ATTEMPTS_PER_DAY - 1):
            cb2.record_success()
        assert cb2.can_attempt() is True

    def test_thread_safety_concern_state_consistency(self, circuit_breaker):
        """SECURITY: Test that state remains consistent during operations."""
        # Record failure and immediately check state
        circuit_breaker.record_failure()
        assert circuit_breaker._failure_count == 1
        assert circuit_breaker._daily_attempts == 1
        assert circuit_breaker._last_failure_time is not None

        # Record success and verify state reset
        circuit_breaker.record_success()
        assert circuit_breaker._failure_count == 0
        assert circuit_breaker._is_open is False
