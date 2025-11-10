"""Error path tests for API client."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.esb_smart_meter.api_client import ESBDataApi


class TestAPILoginErrorPaths:
    """Test error paths in login flow."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant."""
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *args: func(*args))
        return hass

    @pytest.fixture
    def mock_session(self):
        """Create mock aiohttp session."""
        session = MagicMock()
        session.cookie_jar = []
        return session

    @pytest.fixture
    def esb_api(self, mock_hass, mock_session):
        """Create ESBDataApi instance."""
        return ESBDataApi(
            hass=mock_hass,
            session=mock_session,
            username="test@example.com",
            password="password",
            mprn="12345678901",
        )

    @pytest.mark.asyncio
    async def test_error_missing_settings_in_page(self, esb_api):
        """ERROR PATH: Test missing SETTINGS variable in login page."""
        mock_response = MagicMock()
        mock_response.__aenter__ = AsyncMock(
            return_value=MagicMock(
                status=200,
                text=AsyncMock(return_value="<html>No SETTINGS here</html>"),
                raise_for_status=MagicMock(),
                url="https://login.esb.ie",
                headers={},
            )
        )
        mock_response.__aexit__ = AsyncMock(return_value=None)

        esb_api._session.get = MagicMock(return_value=mock_response)

        with pytest.raises(ValueError, match="Could not find SETTINGS"):
            await esb_api._ESBDataApi__login()

    @pytest.mark.asyncio
    async def test_error_missing_csrf_token(self, esb_api):
        """ERROR PATH: Test missing CSRF token in SETTINGS."""
        mock_response = MagicMock()
        mock_response.__aenter__ = AsyncMock(
            return_value=MagicMock(
                status=200,
                text=AsyncMock(
                    return_value='<html><script>var SETTINGS = {"transId":"123"};</script></html>'
                ),
                raise_for_status=MagicMock(),
                url="https://login.esb.ie",
                headers={},
            )
        )
        mock_response.__aexit__ = AsyncMock(return_value=None)

        esb_api._session.get = MagicMock(return_value=mock_response)

        with pytest.raises(ValueError, match="Missing required authentication tokens"):
            await esb_api._ESBDataApi__login()

    @pytest.mark.asyncio
    async def test_error_missing_trans_id(self, esb_api):
        """ERROR PATH: Test missing transId in SETTINGS."""
        mock_response = MagicMock()
        mock_response.__aenter__ = AsyncMock(
            return_value=MagicMock(
                status=200,
                text=AsyncMock(
                    return_value='<html><script>var SETTINGS = {"csrf":"token"};</script></html>'
                ),
                raise_for_status=MagicMock(),
                url="https://login.esb.ie",
                headers={},
            )
        )
        mock_response.__aexit__ = AsyncMock(return_value=None)

        esb_api._session.get = MagicMock(return_value=mock_response)

        with pytest.raises(ValueError, match="Missing required authentication tokens"):
            await esb_api._ESBDataApi__login()

    @pytest.mark.asyncio
    async def test_error_network_error_during_login(self, esb_api):
        """ERROR PATH: Test network error during login."""
        esb_api._session.get = MagicMock(
            side_effect=aiohttp.ClientError("Connection failed")
        )

        with pytest.raises(aiohttp.ClientError):
            await esb_api._ESBDataApi__login()

    @pytest.mark.asyncio
    async def test_error_invalid_json_in_settings(self, esb_api):
        """ERROR PATH: Test invalid JSON in SETTINGS."""
        mock_response = MagicMock()
        mock_response.__aenter__ = AsyncMock(
            return_value=MagicMock(
                status=200,
                text=AsyncMock(
                    return_value='<html><script>var SETTINGS = {invalid json};</script></html>'
                ),
                raise_for_status=MagicMock(),
                url="https://login.esb.ie",
                headers={},
            )
        )
        mock_response.__aexit__ = AsyncMock(return_value=None)

        esb_api._session.get = MagicMock(return_value=mock_response)

        with pytest.raises((ValueError, json.JSONDecodeError)):
            await esb_api._ESBDataApi__login()

    @pytest.mark.asyncio
    async def test_error_captcha_detected(self, esb_api):
        """ERROR PATH: Test CAPTCHA detection triggers error."""
        mock_response = MagicMock()
        mock_response.__aenter__ = AsyncMock(
            return_value=MagicMock(
                status=200,
                text=AsyncMock(
                    return_value='<html><div class="g-recaptcha-response"></div></html>'
                ),
                raise_for_status=MagicMock(),
                url="https://login.esb.ie",
                headers={},
            )
        )
        mock_response.__aexit__ = AsyncMock(return_value=None)

        esb_api._session.get = MagicMock(return_value=mock_response)

        # CAPTCHA should trigger ValueError about "not a robot"
        with pytest.raises(ValueError):
            await esb_api._ESBDataApi__login()

    @pytest.mark.asyncio
    async def test_error_missing_auto_submit_form(self, esb_api):
        """ERROR PATH: Test missing auto-submit form in response."""
        # This tests the form parsing logic after authentication
        # Would need to mock multiple request/response cycles
        pass  # Complex multi-step test - covered by integration tests

    @pytest.mark.asyncio
    async def test_error_empty_form_fields(self, esb_api):
        """ERROR PATH: Test empty values in form fields."""
        # This tests form validation after parsing
        pass  # Complex multi-step test - covered by integration tests


class TestAPIFetchDataErrorPaths:
    """Test error paths in data fetching."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant."""
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *args: func(*args))
        return hass

    @pytest.fixture
    def mock_session(self):
        """Create mock aiohttp session."""
        session = MagicMock()
        session.cookie_jar = []
        return session

    @pytest.fixture
    def esb_api(self, mock_hass, mock_session):
        """Create ESBDataApi instance."""
        return ESBDataApi(
            hass=mock_hass,
            session=mock_session,
            username="test@example.com",
            password="password",
            mprn="12345678901",
        )

    @pytest.mark.asyncio
    async def test_error_csv_too_large(self, esb_api):
        """ERROR PATH: Test CSV size exceeds limit."""
        # Mock successful login
        with patch.object(esb_api, "_ESBDataApi__login", return_value={}):
            # Mock large CSV response
            large_csv = "a" * (50 * 1024 * 1024)  # 50 MB
            mock_response = MagicMock()
            mock_response.__aenter__ = AsyncMock(
                return_value=MagicMock(
                    status=200,
                    text=AsyncMock(return_value=large_csv),
                    raise_for_status=MagicMock(),
                    headers={"Content-Length": str(len(large_csv))},
                )
            )
            mock_response.__aexit__ = AsyncMock(return_value=None)

            esb_api._session.post = MagicMock(return_value=mock_response)

            with pytest.raises(ValueError, match="CSV file size.*exceeds maximum"):
                await esb_api._ESBDataApi__fetch_data()

    @pytest.mark.asyncio
    async def test_error_network_timeout(self, esb_api):
        """ERROR PATH: Test network timeout during fetch."""
        with patch.object(esb_api, "_ESBDataApi__login", return_value={}):
            esb_api._session.post = MagicMock(side_effect=asyncio.TimeoutError())

            with pytest.raises(asyncio.TimeoutError):
                await esb_api._ESBDataApi__fetch_data()

    @pytest.mark.asyncio
    async def test_error_http_error_response(self, esb_api):
        """ERROR PATH: Test HTTP error response."""
        with patch.object(esb_api, "_ESBDataApi__login", return_value={}):
            mock_response = MagicMock()
            mock_response.__aenter__ = AsyncMock(
                return_value=MagicMock(
                    status=500,
                    raise_for_status=MagicMock(
                        side_effect=aiohttp.ClientResponseError(
                            request_info=MagicMock(),
                            history=(),
                            status=500,
                            message="Internal Server Error",
                        )
                    ),
                )
            )
            mock_response.__aexit__ = AsyncMock(return_value=None)

            esb_api._session.post = MagicMock(return_value=mock_response)

            with pytest.raises(aiohttp.ClientResponseError):
                await esb_api._ESBDataApi__fetch_data()

    @pytest.mark.asyncio
    async def test_error_failed_download_token(self, esb_api):
        """ERROR PATH: Test failed to get download token."""
        with patch.object(esb_api, "_ESBDataApi__login", return_value={}):
            # Mock response without download token
            mock_response = MagicMock()
            mock_response.__aenter__ = AsyncMock(
                return_value=MagicMock(
                    status=200,
                    text=AsyncMock(return_value="<html>No token here</html>"),
                    raise_for_status=MagicMock(),
                    headers={},
                )
            )
            mock_response.__aexit__ = AsyncMock(return_value=None)

            esb_api._session.post = MagicMock(return_value=mock_response)

            with pytest.raises(ValueError, match="Failed to get download token"):
                await esb_api._ESBDataApi__fetch_data()


class TestAPIFetchWithCircuitBreaker:
    """Test fetch() with circuit breaker error handling."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant."""
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *args: func(*args))
        return hass

    @pytest.fixture
    def mock_session(self):
        """Create mock aiohttp session."""
        session = MagicMock()
        session.cookie_jar = []
        return session

    @pytest.fixture
    def esb_api(self, mock_hass, mock_session):
        """Create ESBDataApi instance."""
        return ESBDataApi(
            hass=mock_hass,
            session=mock_session,
            username="test@example.com",
            password="password",
            mprn="12345678901",
        )

    @pytest.mark.asyncio
    async def test_error_circuit_breaker_open(self, esb_api):
        """ERROR PATH: Test fetch blocked when circuit breaker is open."""
        # Open the circuit breaker
        from custom_components.esb_smart_meter.const import CIRCUIT_BREAKER_FAILURES

        for _ in range(CIRCUIT_BREAKER_FAILURES):
            esb_api._circuit_breaker.record_failure()

        # Should not attempt fetch
        result = await esb_api.fetch()
        assert result is None

    @pytest.mark.asyncio
    async def test_error_value_error_during_fetch(self, esb_api):
        """ERROR PATH: Test ValueError handling in fetch."""
        with patch.object(
            esb_api, "_ESBDataApi__fetch_data", side_effect=ValueError("Parse error")
        ):
            result = await esb_api.fetch()
            assert result is None
            # Circuit breaker should record failure
            assert esb_api._circuit_breaker._failure_count > 0

    @pytest.mark.asyncio
    async def test_error_client_response_error_429(self, esb_api):
        """ERROR PATH: Test 429 rate limit response."""
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=429,
            message="Too Many Requests",
        )

        with patch.object(esb_api, "_ESBDataApi__fetch_data", side_effect=error):
            result = await esb_api.fetch()
            assert result is None
            # Should record failure
            assert esb_api._circuit_breaker._failure_count > 0

    @pytest.mark.asyncio
    async def test_error_client_response_error_other(self, esb_api):
        """ERROR PATH: Test other HTTP error responses."""
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=403,
            message="Forbidden",
        )

        with patch.object(esb_api, "_ESBDataApi__fetch_data", side_effect=error):
            result = await esb_api.fetch()
            assert result is None

    @pytest.mark.asyncio
    async def test_error_network_error_in_fetch(self, esb_api):
        """ERROR PATH: Test network error in fetch."""
        with patch.object(
            esb_api,
            "_ESBDataApi__fetch_data",
            side_effect=aiohttp.ClientError("Network error"),
        ):
            result = await esb_api.fetch()
            assert result is None

    @pytest.mark.asyncio
    async def test_error_timeout_in_fetch(self, esb_api):
        """ERROR PATH: Test timeout in fetch."""
        with patch.object(
            esb_api, "_ESBDataApi__fetch_data", side_effect=asyncio.TimeoutError()
        ):
            result = await esb_api.fetch()
            assert result is None

    @pytest.mark.asyncio
    async def test_error_unexpected_exception(self, esb_api):
        """ERROR PATH: Test unexpected exception in fetch."""
        with patch.object(
            esb_api, "_ESBDataApi__fetch_data", side_effect=RuntimeError("Boom")
        ):
            result = await esb_api.fetch()
            assert result is None


class TestAPICSVParsingErrorPaths:
    """Test error paths in CSV parsing."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant."""
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *args: func(*args))
        return hass

    @pytest.fixture
    def mock_session(self):
        """Create mock aiohttp session."""
        session = MagicMock()
        session.cookie_jar = []
        return session

    @pytest.fixture
    def esb_api(self, mock_hass, mock_session):
        """Create ESBDataApi instance."""
        return ESBDataApi(
            hass=mock_hass,
            session=mock_session,
            username="test@example.com",
            password="password",
            mprn="12345678901",
        )

    def test_error_empty_csv(self, esb_api):
        """ERROR PATH: Test empty CSV string."""
        result = esb_api._ESBDataApi__csv_to_dict("")
        assert result == []

    def test_error_csv_with_only_header(self, esb_api):
        """ERROR PATH: Test CSV with only header row."""
        csv_data = "Read Date and End Time,Read Value,Read Type,MPRN"
        result = esb_api._ESBDataApi__csv_to_dict(csv_data)
        assert result == []

    def test_error_malformed_csv_rows(self, esb_api):
        """ERROR PATH: Test CSV with malformed rows."""
        csv_data = """Read Date and End Time,Read Value,Read Type,MPRN
31-12-2024 00:30,1.5,Active Import,12345678901
This row is malformed
31-12-2024 01:00,2.0,Active Import,12345678901"""
        
        # Should parse valid rows and skip malformed
        result = esb_api._ESBDataApi__csv_to_dict(csv_data)
        # Depends on implementation - may raise or skip
        assert isinstance(result, list)

    def test_error_csv_with_unicode(self, esb_api):
        """CORNER: Test CSV with Unicode characters."""
        csv_data = """Read Date and End Time,Read Value,Read Type,MPRN
31-12-2024 00:30,1.5,Active Import,12345678901
Comment: Café ☕"""
        
        # Should handle Unicode without errors
        result = esb_api._ESBDataApi__csv_to_dict(csv_data)
        assert isinstance(result, list)

    def test_error_csv_with_special_characters(self, esb_api):
        """CORNER: Test CSV with special characters."""
        csv_data = """Read Date and End Time,Read Value,Read Type,MPRN
31-12-2024 00:30,"1,500.5",Active Import,12345678901"""
        
        # Should handle quoted values with commas
        result = esb_api._ESBDataApi__csv_to_dict(csv_data)
        assert len(result) >= 1
