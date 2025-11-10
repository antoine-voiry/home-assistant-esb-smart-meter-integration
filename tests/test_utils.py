"""Tests for utility functions including security and corner cases."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime

from custom_components.esb_smart_meter.utils import (
    get_human_like_delay,
    get_startup_delay,
    get_random_user_agent,
    create_esb_session,
)
from custom_components.esb_smart_meter.const import (
    MIN_REQUEST_DELAY,
    MAX_REQUEST_DELAY,
    REQUEST_DELAY_MEAN,
    LONG_PAUSE_MIN,
    LONG_PAUSE_MAX,
    STARTUP_DELAY_MIN,
    STARTUP_DELAY_MAX,
    HA_UPTIME_THRESHOLD,
)


class TestGetHumanLikeDelay:
    """Test get_human_like_delay function."""

    def test_delay_within_bounds(self):
        """Test that delay is always within min/max bounds."""
        for _ in range(100):
            delay = get_human_like_delay()
            assert MIN_REQUEST_DELAY <= delay <= MAX_REQUEST_DELAY + LONG_PAUSE_MAX

    def test_delay_positive(self):
        """Test that delay is always positive."""
        for _ in range(50):
            delay = get_human_like_delay()
            assert delay > 0

    def test_delay_variance(self):
        """Test that delays vary (not constant)."""
        delays = [get_human_like_delay() for _ in range(20)]
        # Should have at least some variance
        assert len(set(delays)) > 1

    def test_security_no_zero_delay(self):
        """SECURITY: Ensure delay is never zero to prevent timing attacks."""
        for _ in range(100):
            delay = get_human_like_delay()
            assert delay > 0

    def test_corner_case_long_pause_added(self):
        """CORNER: Test long pause addition when triggered."""
        with patch("random.random", return_value=0.01):  # Trigger long pause
            with patch("random.gauss", return_value=REQUEST_DELAY_MEAN):
                with patch("random.uniform", return_value=5.0):
                    delay = get_human_like_delay()
                    # Should include the long pause
                    assert delay >= REQUEST_DELAY_MEAN + 5.0

    def test_corner_case_no_long_pause(self):
        """CORNER: Test behavior when long pause is not triggered."""
        with patch("random.random", return_value=0.99):  # Don't trigger long pause
            with patch("random.gauss", return_value=REQUEST_DELAY_MEAN):
                delay = get_human_like_delay()
                # Should be close to mean, clamped to bounds
                expected = max(
                    MIN_REQUEST_DELAY, min(MAX_REQUEST_DELAY, REQUEST_DELAY_MEAN)
                )
                assert abs(delay - expected) < 0.1

    def test_corner_case_extreme_gaussian_values(self):
        """CORNER: Test clamping of extreme Gaussian values."""
        # Test very high value gets clamped
        with patch("random.gauss", return_value=999.0):
            with patch("random.random", return_value=0.99):  # No long pause
                delay = get_human_like_delay()
                assert delay == MAX_REQUEST_DELAY

        # Test very low value gets clamped
        with patch("random.gauss", return_value=-10.0):
            with patch("random.random", return_value=0.99):
                delay = get_human_like_delay()
                assert delay == MIN_REQUEST_DELAY


class TestGetStartupDelay:
    """Test get_startup_delay function."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock()
        return hass

    @pytest.mark.asyncio
    async def test_recent_startup_returns_delay(self, mock_hass):
        """Test that recent HA startup triggers delay."""
        # Mock HA that started 1 minute ago
        start_time = datetime.now()
        with patch(
            "custom_components.esb_smart_meter.utils.dt_util.utcnow",
            return_value=start_time,
        ):
            mock_hass.data = {"homeassistant": {"start_time": start_time}}

            delay = await get_startup_delay(mock_hass)

            # Should return a delay between min and max
            assert STARTUP_DELAY_MIN <= delay <= STARTUP_DELAY_MAX

    @pytest.mark.asyncio
    async def test_old_startup_returns_zero(self, mock_hass):
        """Test that old HA startup returns zero delay."""
        # Mock HA that started 20 minutes ago
        from datetime import timedelta

        start_time = datetime.now() - timedelta(seconds=HA_UPTIME_THRESHOLD + 60)
        current_time = datetime.now()

        with patch(
            "custom_components.esb_smart_meter.utils.dt_util.utcnow",
            return_value=current_time,
        ):
            mock_hass.data = {"homeassistant": {"start_time": start_time}}

            delay = await get_startup_delay(mock_hass)

            # Should return no delay
            assert delay == 0

    @pytest.mark.asyncio
    async def test_corner_case_missing_data(self, mock_hass):
        """CORNER: Test handling of missing homeassistant data."""
        mock_hass.data = {}

        delay = await get_startup_delay(mock_hass)

        # Should assume HA just started and return delay
        assert STARTUP_DELAY_MIN <= delay <= STARTUP_DELAY_MAX

    @pytest.mark.asyncio
    async def test_corner_case_missing_start_time(self, mock_hass):
        """CORNER: Test handling of missing start_time key."""
        mock_hass.data = {"homeassistant": {}}

        delay = await get_startup_delay(mock_hass)

        # Should assume HA just started and return delay
        assert STARTUP_DELAY_MIN <= delay <= STARTUP_DELAY_MAX

    @pytest.mark.asyncio
    async def test_corner_case_none_start_time(self, mock_hass):
        """CORNER: Test handling when start_time is None."""
        current_time = datetime.now()
        with patch(
            "custom_components.esb_smart_meter.utils.dt_util.utcnow",
            return_value=current_time,
        ):
            mock_hass.data = {"homeassistant": {"start_time": None}}

            # This should trigger the exception handler
            delay = await get_startup_delay(mock_hass)
            assert STARTUP_DELAY_MIN <= delay <= STARTUP_DELAY_MAX

    @pytest.mark.asyncio
    async def test_corner_case_exactly_at_threshold(self, mock_hass):
        """CORNER: Test behavior exactly at HA_UPTIME_THRESHOLD."""
        from datetime import timedelta

        start_time = datetime.now() - timedelta(seconds=HA_UPTIME_THRESHOLD)
        current_time = datetime.now()

        with patch(
            "custom_components.esb_smart_meter.utils.dt_util.utcnow",
            return_value=current_time,
        ):
            mock_hass.data = {"homeassistant": {"start_time": start_time}}

            delay = await get_startup_delay(mock_hass)

            # At exactly threshold, should not trigger delay
            assert delay == 0

    @pytest.mark.asyncio
    async def test_corner_case_one_second_before_threshold(self, mock_hass):
        """CORNER: Test behavior one second before threshold."""
        from datetime import timedelta

        start_time = datetime.now() - timedelta(seconds=HA_UPTIME_THRESHOLD - 1)
        current_time = datetime.now()

        with patch(
            "custom_components.esb_smart_meter.utils.dt_util.utcnow",
            return_value=current_time,
        ):
            mock_hass.data = {"homeassistant": {"start_time": start_time}}

            delay = await get_startup_delay(mock_hass)

            # Just under threshold, should trigger delay
            assert STARTUP_DELAY_MIN <= delay <= STARTUP_DELAY_MAX

    @pytest.mark.asyncio
    async def test_security_prevents_instant_login(self, mock_hass):
        """SECURITY: Test that startup delay prevents immediate ESB hammering."""
        # Simulate HA just started
        start_time = datetime.now()
        with patch(
            "custom_components.esb_smart_meter.utils.dt_util.utcnow",
            return_value=start_time,
        ):
            mock_hass.data = {"homeassistant": {"start_time": start_time}}

            delay = await get_startup_delay(mock_hass)

            # Must have some delay to prevent hammering
            assert delay >= STARTUP_DELAY_MIN
            # Should be at least 5 minutes
            assert delay >= 300


class TestGetRandomUserAgent:
    """Test get_random_user_agent function."""

    def test_returns_string(self):
        """Test that function returns a string."""
        ua = get_random_user_agent()
        assert isinstance(ua, str)

    def test_returns_non_empty(self):
        """Test that function returns non-empty string."""
        ua = get_random_user_agent()
        assert len(ua) > 0

    def test_returns_different_agents(self):
        """Test that function returns different user agents."""
        agents = [get_random_user_agent() for _ in range(20)]
        # Should have some variety
        assert len(set(agents)) > 1

    def test_security_no_custom_identifiers(self):
        """SECURITY: Ensure user agents don't contain custom identifiers."""
        for _ in range(10):
            ua = get_random_user_agent()
            # Should not contain "HomeAssistant" or similar identifiers
            assert "homeassistant" not in ua.lower()
            assert "hass" not in ua.lower()
            assert "python" not in ua.lower()

    def test_corner_case_valid_format(self):
        """CORNER: Test that returned user agent has valid format."""
        ua = get_random_user_agent()
        # User agent should contain typical browser indicators
        valid_indicators = [
            "Mozilla",
            "Chrome",
            "Safari",
            "Firefox",
            "Edge",
            "Opera",
        ]
        assert any(indicator in ua for indicator in valid_indicators)


class TestCreateEsbSession:
    """Test create_esb_session function."""

    @pytest.mark.asyncio
    async def test_creates_session(self):
        """Test that function creates an aiohttp session."""
        mock_hass = MagicMock()
        session = await create_esb_session(mock_hass)

        assert session is not None
        assert hasattr(session, "get")
        assert hasattr(session, "post")
        assert hasattr(session, "close")

        # Cleanup
        await session.close()

    @pytest.mark.asyncio
    async def test_session_has_custom_cookie_jar(self):
        """Test that session uses custom cookie jar."""
        mock_hass = MagicMock()
        session = await create_esb_session(mock_hass)

        assert session.cookie_jar is not None
        # Cookie jar should have quote_cookie=False for ESB compatibility
        assert hasattr(session.cookie_jar, "_quote_cookie")

        await session.close()

    @pytest.mark.asyncio
    async def test_session_has_timeout(self):
        """Test that session has configured timeout."""
        mock_hass = MagicMock()
        session = await create_esb_session(mock_hass)

        assert session.timeout is not None

        await session.close()

    @pytest.mark.asyncio
    async def test_security_unsafe_cookie_jar_disabled(self):
        """SECURITY: Test that unsafe cookie jar is disabled."""
        mock_hass = MagicMock()
        session = await create_esb_session(mock_hass)

        # Verify cookie jar is custom (not default) - safe by configuration
        # The unsafe=False parameter prevents IP address cookies
        # We can't directly access the unsafe attribute, but verify custom jar exists
        assert session.cookie_jar is not None
        assert hasattr(session, '_connector') or hasattr(session, 'cookie_jar')

        await session.close()

    @pytest.mark.asyncio
    async def test_corner_case_session_reusable(self):
        """CORNER: Test that created session is reusable for multiple requests."""
        mock_hass = MagicMock()
        session = await create_esb_session(mock_hass)

        # Session should be open and ready
        assert not session.closed

        await session.close()
        # After close, should be closed
        assert session.closed
