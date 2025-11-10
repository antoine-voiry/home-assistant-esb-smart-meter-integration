"""Tests for ESB API functionality."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.esb_smart_meter.api_client import ESBDataApi
from custom_components.esb_smart_meter.cache import ESBCachingApi


class TestESBDataApi:
    """Test ESBDataApi class."""

    @pytest.fixture
    def esb_api(self, mock_hass, mock_aiohttp_session):
        """Create ESBDataApi instance."""
        return ESBDataApi(
            hass=mock_hass,
            session=mock_aiohttp_session,
            username="test@example.com",
            password="test-password",
            mprn="12345678901",
        )

    @pytest.mark.asyncio
    async def test_login_success(
        self, esb_api, sample_esb_login_html, sample_esb_confirm_html
    ):
        """Test successful login."""
        # Mock 1: Initial GET request to get CSRF token
        mock_login_response = MagicMock()
        mock_login_response.__aenter__ = AsyncMock(
            return_value=MagicMock(
                status=200,
                text=AsyncMock(return_value=sample_esb_login_html),
                raise_for_status=MagicMock(),
                headers={},
            )
        )
        mock_login_response.__aexit__ = AsyncMock(return_value=None)

        # Mock 2: POST login credentials
        mock_post_login_response = MagicMock()
        mock_post_login_response.__aenter__ = AsyncMock(
            return_value=MagicMock(
                status=200,
                text=AsyncMock(return_value="Login successful"),
                raise_for_status=MagicMock(),
                headers={},
            )
        )
        mock_post_login_response.__aexit__ = AsyncMock(return_value=None)

        # Mock 3: GET confirm login (returns form)
        mock_confirm_response = MagicMock()
        mock_confirm_response.__aenter__ = AsyncMock(
            return_value=MagicMock(
                status=200,
                text=AsyncMock(return_value=sample_esb_confirm_html),
                raise_for_status=MagicMock(),
                headers={},
            )
        )
        mock_confirm_response.__aexit__ = AsyncMock(return_value=None)

        # Mock 4: POST signin-oidc
        mock_signin_response = MagicMock()
        mock_signin_response.__aenter__ = AsyncMock(
            return_value=MagicMock(
                status=200,
                text=AsyncMock(return_value="Signin successful"),
                raise_for_status=MagicMock(),
                headers={},
            )
        )
        mock_signin_response.__aexit__ = AsyncMock(return_value=None)

        # Mock 5: GET myaccount page
        mock_myaccount_response = MagicMock()
        mock_myaccount_response.__aenter__ = AsyncMock(
            return_value=MagicMock(
                status=200,
                text=AsyncMock(return_value="<html>My Account</html>"),
                raise_for_status=MagicMock(),
                headers={},
            )
        )
        mock_myaccount_response.__aexit__ = AsyncMock(return_value=None)

        # Mock 6: GET consumption page
        mock_consumption_response = MagicMock()
        mock_consumption_response.__aenter__ = AsyncMock(
            return_value=MagicMock(
                status=200,
                text=AsyncMock(return_value="<html>Consumption</html>"),
                raise_for_status=MagicMock(),
                headers={},
            )
        )
        mock_consumption_response.__aexit__ = AsyncMock(return_value=None)

        # Mock 7: GET token
        mock_token_response = MagicMock()
        mock_token_response.__aenter__ = AsyncMock(
            return_value=MagicMock(
                status=200,
                json=AsyncMock(return_value={"token": "test-download-token"}),
                raise_for_status=MagicMock(),
                headers={},
            )
        )
        mock_token_response.__aexit__ = AsyncMock(return_value=None)

        # Mock asyncio.sleep to avoid waiting during tests
        with patch("asyncio.sleep", new_callable=AsyncMock):
            # Mock GET calls (requests 1, 3, 5, 6, 7)
            # Mock POST calls (requests 2, 4)
            with patch.object(
                esb_api._session,
                "get",
                side_effect=[
                    mock_login_response,
                    mock_confirm_response,
                    mock_myaccount_response,
                    mock_consumption_response,
                    mock_token_response,
                ],
            ):
                with patch.object(
                    esb_api._session,
                    "post",
                    side_effect=[mock_post_login_response, mock_signin_response],
                ):
                    result = await esb_api._ESBDataApi__login()
                    assert result is not None
                    assert "download_token" in result
                    assert "user_agent" in result
                    assert result["download_token"] == "test-download-token"

    @pytest.mark.asyncio
    async def test_login_missing_csrf(self, esb_api):
        """Test login fails when CSRF token is missing."""
        mock_response = MagicMock()
        mock_response.__aenter__ = AsyncMock(
            return_value=MagicMock(
                status=200,
                text=AsyncMock(return_value="<html>No settings here</html>"),
                raise_for_status=MagicMock(),
                headers={},
            )
        )
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(esb_api._session, "get", return_value=mock_response):
            with pytest.raises(ValueError, match="Could not find SETTINGS"):
                await esb_api._ESBDataApi__login()

    @pytest.mark.asyncio
    async def test_login_network_error(self, esb_api):
        """Test login handles network errors."""
        with patch.object(
            esb_api._session, "get", side_effect=aiohttp.ClientError("Network error")
        ):
            with pytest.raises(aiohttp.ClientError):
                await esb_api._ESBDataApi__login()

    @pytest.mark.asyncio
    async def test_fetch_data_success(self, esb_api, sample_csv_data):
        """Test successful data fetch."""
        mock_response = MagicMock()
        mock_response.__aenter__ = AsyncMock(
            return_value=MagicMock(
                status=200,
                text=AsyncMock(return_value=sample_csv_data),
                raise_for_status=MagicMock(),
                headers={"Content-Length": str(len(sample_csv_data))},
            )
        )
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(esb_api._session, "post", return_value=mock_response):
            csv_data = await esb_api._ESBDataApi__fetch_data(
                "test-token", "test-user-agent"
            )
            assert csv_data == sample_csv_data

    @pytest.mark.asyncio
    async def test_fetch_data_size_limit(self, esb_api):
        """Test data fetch respects size limits."""
        # Simulate response larger than MAX_CSV_SIZE_MB
        large_size = 11 * 1024 * 1024  # 11 MB
        large_data = "x" * large_size

        mock_response = MagicMock()
        mock_response.__aenter__ = AsyncMock(
            return_value=MagicMock(
                status=200,
                text=AsyncMock(return_value=large_data),
                raise_for_status=MagicMock(),
                headers={"Content-Length": str(large_size)},
            )
        )
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(esb_api._session, "post", return_value=mock_response):
            with pytest.raises(ValueError, match="CSV response too large"):
                await esb_api._ESBDataApi__fetch_data("test-token", "test-user-agent")

    @pytest.mark.asyncio
    async def test_csv_to_dict(self, esb_api, sample_csv_data):
        """Test CSV to dictionary conversion."""
        result = esb_api._ESBDataApi__csv_to_dict(sample_csv_data)
        assert isinstance(result, list)
        assert len(result) > 0
        assert "Read Date and End Time" in result[0]
        assert "Read Value" in result[0]

    @pytest.mark.asyncio
    async def test_fetch_with_retry(
        self, esb_api, sample_csv_data, sample_esb_login_html, sample_esb_confirm_html
    ):
        """Test fetch with circuit breaker - first attempt fails, second succeeds after circuit allows."""
        # First attempt should fail and record in circuit breaker
        with patch.object(
            esb_api,
            "_ESBDataApi__login",
            side_effect=aiohttp.ClientError("Network error"),
        ):
            with pytest.raises(aiohttp.ClientError):
                await esb_api.fetch()

        # Circuit breaker should have recorded the failure
        assert esb_api._circuit_breaker._failure_count == 1

        # Second attempt succeeds (circuit breaker allows since not enough failures yet)
        with patch.object(
            esb_api,
            "_ESBDataApi__login",
            return_value={"download_token": "test-token", "user_agent": "test-ua"},
        ):
            with patch.object(
                esb_api, "_ESBDataApi__fetch_data", return_value=sample_csv_data
            ):
                with patch.object(
                    esb_api._hass,
                    "async_add_executor_job",
                    side_effect=lambda func, *args: func(*args),
                ):
                    result = await esb_api.fetch()
                    assert result is not None
                    # Circuit breaker should be reset after success
                    assert esb_api._circuit_breaker._failure_count == 0


class TestESBCachingApi:
    """Test ESBCachingApi class."""

    @pytest.fixture
    def mock_esb_api(self):
        """Create mock ESBDataApi."""
        api = MagicMock()
        api.fetch = AsyncMock()
        return api

    @pytest.fixture
    def caching_api(self, mock_esb_api):
        """Create ESBCachingApi instance."""
        return ESBCachingApi(mock_esb_api)

    @pytest.mark.asyncio
    async def test_cache_miss(self, caching_api, mock_esb_api):
        """Test cache miss triggers fetch."""
        mock_data = MagicMock()
        mock_esb_api.fetch.return_value = mock_data

        result = await caching_api.fetch()

        assert result == mock_data
        mock_esb_api.fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_hit(self, caching_api, mock_esb_api):
        """Test cache hit doesn't trigger fetch."""
        mock_data = MagicMock()
        mock_esb_api.fetch.return_value = mock_data

        # First call
        result1 = await caching_api.fetch()
        # Second call (should use cache)
        result2 = await caching_api.fetch()

        assert result1 == mock_data
        assert result2 == mock_data
        # Should only fetch once
        assert mock_esb_api.fetch.call_count == 1

    @pytest.mark.asyncio
    async def test_cache_error_handling(self, caching_api, mock_esb_api):
        """Test cache handles fetch errors."""
        mock_esb_api.fetch.side_effect = Exception("Fetch failed")

        with pytest.raises(Exception, match="Fetch failed"):
            await caching_api.fetch()

        # Verify cache is cleared on error
        assert caching_api._cached_data is None
        assert caching_api._cached_data_timestamp is None
