"""Tests for ESB API functionality."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
import aiohttp

from custom_components.esb_smart_meter.sensor import ESBDataApi, ESBCachingApi


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
    async def test_login_success(self, esb_api, sample_esb_login_html, sample_esb_confirm_html):
        """Test successful login."""
        # Mock 1: Initial GET request to get CSRF token
        mock_login_response = MagicMock()
        mock_login_response.__aenter__ = AsyncMock(return_value=MagicMock(
            status=200,
            text=AsyncMock(return_value=sample_esb_login_html),
            raise_for_status=MagicMock(),
            headers={}
        ))
        mock_login_response.__aexit__ = AsyncMock(return_value=None)

        # Mock 2: POST login credentials
        mock_post_login_response = MagicMock()
        mock_post_login_response.__aenter__ = AsyncMock(return_value=MagicMock(
            status=200,
            text=AsyncMock(return_value="Login successful"),
            raise_for_status=MagicMock(),
            headers={}
        ))
        mock_post_login_response.__aexit__ = AsyncMock(return_value=None)

        # Mock 3: GET confirm login (returns form)
        mock_confirm_response = MagicMock()
        mock_confirm_response.__aenter__ = AsyncMock(return_value=MagicMock(
            status=200,
            text=AsyncMock(return_value=sample_esb_confirm_html),
            raise_for_status=MagicMock(),
            headers={}
        ))
        mock_confirm_response.__aexit__ = AsyncMock(return_value=None)

        # Mock 4: POST final form with cookies
        mock_final_response = MagicMock()
        mock_final_response.__aenter__ = AsyncMock(return_value=MagicMock(
            status=200,
            text=AsyncMock(return_value="Confirmed"),
            raise_for_status=MagicMock(),
            headers={},
            cookies={'test_cookie': 'test_value'}
        ))
        mock_final_response.__aexit__ = AsyncMock(return_value=None)

        # Mock GET calls (initial CSRF get, confirm get)
        # Mock POST calls (login post, final form post)
        with patch.object(esb_api._session, 'get', side_effect=[mock_login_response, mock_confirm_response]):
            with patch.object(esb_api._session, 'post', side_effect=[mock_post_login_response, mock_final_response]):
                cookies = await esb_api._ESBDataApi__login()
                assert cookies is not None

    @pytest.mark.asyncio
    async def test_login_missing_csrf(self, esb_api):
        """Test login fails when CSRF token is missing."""
        mock_response = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=MagicMock(
            status=200,
            text=AsyncMock(return_value="<html>No settings here</html>"),
            raise_for_status=MagicMock(),
            headers={}
        ))
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(esb_api._session, 'get', return_value=mock_response):
            with pytest.raises(ValueError, match="Could not find SETTINGS"):
                await esb_api._ESBDataApi__login()

    @pytest.mark.asyncio
    async def test_login_network_error(self, esb_api):
        """Test login handles network errors."""
        with patch.object(
            esb_api._session,
            'get',
            side_effect=aiohttp.ClientError("Network error")
        ):
            with pytest.raises(aiohttp.ClientError):
                await esb_api._ESBDataApi__login()

    @pytest.mark.asyncio
    async def test_fetch_data_success(self, esb_api, sample_csv_data):
        """Test successful data fetch."""
        mock_response = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=MagicMock(
            status=200,
            text=AsyncMock(return_value=sample_csv_data),
            raise_for_status=MagicMock(),
            headers={'Content-Length': str(len(sample_csv_data))}
        ))
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(esb_api._session, 'get', return_value=mock_response):
            csv_data = await esb_api._ESBDataApi__fetch_data()
            assert csv_data == sample_csv_data

    @pytest.mark.asyncio
    async def test_fetch_data_size_limit(self, esb_api):
        """Test data fetch respects size limits."""
        # Simulate response larger than MAX_CSV_SIZE_MB
        large_size = 11 * 1024 * 1024  # 11 MB
        large_data = "x" * large_size
        
        mock_response = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=MagicMock(
            status=200,
            text=AsyncMock(return_value=large_data),
            raise_for_status=MagicMock(),
            headers={'Content-Length': str(large_size)}
        ))
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(esb_api._session, 'get', return_value=mock_response):
            with pytest.raises(ValueError, match="CSV response too large"):
                await esb_api._ESBDataApi__fetch_data()

    @pytest.mark.asyncio
    async def test_csv_to_dict(self, esb_api, sample_csv_data):
        """Test CSV to dictionary conversion."""
        result = esb_api._ESBDataApi__csv_to_dict(sample_csv_data)
        assert isinstance(result, list)
        assert len(result) > 0
        assert "Read Date and End Time" in result[0]
        assert "Read Value" in result[0]

    @pytest.mark.asyncio
    async def test_fetch_with_retry(self, esb_api, sample_csv_data, sample_esb_login_html, sample_esb_confirm_html):
        """Test fetch with retry logic."""
        # First attempt fails, second succeeds
        login_responses = [
            aiohttp.ClientError("Network error"),
            AsyncMock(
                status=200,
                text=AsyncMock(return_value=sample_esb_login_html),
                raise_for_status=MagicMock(),
                headers={}
            ),
        ]

        call_count = [0]

        async def mock_login(*args, **kwargs):
            if call_count[0] == 0:
                call_count[0] += 1
                raise login_responses[0]
            return {"test": "cookie"}

        with patch.object(esb_api, '_ESBDataApi__login', side_effect=mock_login):
            with patch.object(
                esb_api,
                '_ESBDataApi__fetch_data',
                return_value=sample_csv_data
            ):
                with patch.object(
                    esb_api._hass,
                    'async_add_executor_job',
                    side_effect=lambda func, *args: func(*args)
                ):
                    result = await esb_api.fetch()
                    assert result is not None


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
