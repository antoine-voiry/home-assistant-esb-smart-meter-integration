"""A pasted browser session must be usable on its own (manual cookies, CAPTCHA bypass).

Three faults made a pasted session unusable, whatever the user pasted:
- it was saved with no download token, and the cached path never asked for one, so
  fetch() always stopped at "Authentication succeeded but no download token received";
- it was replayed with a truncated User-Agent no browser sends;
- when ESB rejected it, the integration fell straight through to a scripted login,
  which hit the CAPTCHA again.
And the instructions asked for document.cookie, which cannot see ESB's HttpOnly
login cookies, so the paste never contained the session in the first place.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.esb_smart_meter.api_client import ESBDataApi
from custom_components.esb_smart_meter.const import (
    ESB_CONSUMPTION_URL,
    ESB_MYACCOUNT_URL,
    ESB_TOKEN_URL,
    SESSION_SOURCE_LOGIN,
    SESSION_SOURCE_MANUAL,
)
from custom_components.esb_smart_meter.session_manager import (
    CaptchaRequiredException,
    SessionManager,
)
from custom_components.esb_smart_meter.user_agents import USER_AGENTS

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"


def _response(json_body=None):
    inner = MagicMock(status=200, raise_for_status=MagicMock(), headers={})
    inner.json = AsyncMock(return_value=json_body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=inner)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


@pytest.fixture
def esb_api(mock_hass, mock_aiohttp_session):
    return ESBDataApi(
        hass=mock_hass,
        session=mock_aiohttp_session,
        username="test@example.com",
        password="test-password",
        mprn="12345678901",
    )


class TestParseCookieHeader:
    def test_strips_header_name_and_line_wrapping(self, mock_hass):
        sm = SessionManager(mock_hass, "12345678901")
        pasted = "Cookie: .AspNetCore.Cookies=abc; ARRAffinity=x1;\n  XSRF-TOKEN=t=9"
        assert sm._parse_cookie_string(pasted) == {
            ".AspNetCore.Cookies": "abc",
            "ARRAffinity": "x1",
            "XSRF-TOKEN": "t=9",
        }

    def test_plain_header_value_unchanged(self, mock_hass):
        sm = SessionManager(mock_hass, "12345678901")
        assert sm._parse_cookie_string("a=1; b=2") == {"a": "1", "b": "2"}


class TestSaveManualCookies:
    @pytest.mark.asyncio
    async def test_saved_as_manual_with_browser_user_agent(self, mock_hass):
        sm = SessionManager(mock_hass, "12345678901")
        with patch.object(sm, "save_session", AsyncMock(return_value=True)) as save:
            assert await sm.save_manual_cookies("a=1", user_agent=f"  {BROWSER_UA} ") is True
        kwargs = save.await_args.kwargs
        assert kwargs["source"] == SESSION_SOURCE_MANUAL
        assert kwargs["user_agent"] == BROWSER_UA
        assert kwargs["download_token"] is None

    @pytest.mark.asyncio
    async def test_default_user_agent_is_a_complete_one(self, mock_hass):
        sm = SessionManager(mock_hass, "12345678901")
        with patch.object(sm, "save_session", AsyncMock(return_value=True)) as save:
            await sm.save_manual_cookies("a=1")
        assert save.await_args.kwargs["user_agent"] == USER_AGENTS[0]


class TestCachedManualSession:
    @pytest.mark.asyncio
    async def test_valid_session_without_token_fetches_one(self, esb_api):
        cached = {
            "cookies": {".AspNetCore.Cookies": "abc"},
            "user_agent": BROWSER_UA,
            "download_token": None,
            "source": SESSION_SOURCE_MANUAL,
        }
        sm = esb_api._session_manager
        esb_api._session.get = MagicMock(
            side_effect=[_response(), _response(), _response({"token": "tok-1"})]
        )
        esb_api._session.post = MagicMock()
        with (
            patch.object(sm, "load_session", AsyncMock(return_value=cached)),
            patch.object(sm, "validate_session_cookies", AsyncMock(return_value=True)),
            patch.object(sm, "load_cookies_to_jar"),
            patch.object(sm, "save_session", AsyncMock(return_value=True)) as save,
            patch("custom_components.esb_smart_meter.api_client.asyncio.sleep", AsyncMock()),
        ):
            result = await esb_api._ESBDataApi__login()

        assert result == {"download_token": "tok-1", "user_agent": BROWSER_UA}
        urls = [c.args[0] for c in esb_api._session.get.call_args_list]
        assert urls == [ESB_MYACCOUNT_URL, ESB_CONSUMPTION_URL, ESB_TOKEN_URL]
        first = esb_api._session.get.call_args_list[0].kwargs["headers"]
        assert first["User-Agent"] == BROWSER_UA
        assert first["Referer"] == f"{ESB_MYACCOUNT_URL}/"
        assert first["Sec-Fetch-Site"] == "same-origin"
        esb_api._session.post.assert_not_called()  # no login POSTs
        save.assert_awaited_once()
        assert save.await_args.kwargs["download_token"] == "tok-1"
        assert save.await_args.kwargs["source"] == SESSION_SOURCE_MANUAL

    @pytest.mark.asyncio
    async def test_valid_session_with_token_makes_no_request(self, esb_api):
        cached = {
            "cookies": {"c": "1"},
            "user_agent": BROWSER_UA,
            "download_token": "kept",
            "source": SESSION_SOURCE_MANUAL,
        }
        sm = esb_api._session_manager
        esb_api._session.get = MagicMock()
        with (
            patch.object(sm, "load_session", AsyncMock(return_value=cached)),
            patch.object(sm, "validate_session_cookies", AsyncMock(return_value=True)),
            patch.object(sm, "load_cookies_to_jar"),
        ):
            result = await esb_api._ESBDataApi__login()
        assert result["download_token"] == "kept"
        esb_api._session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejected_manual_session_does_not_attempt_login(self, esb_api):
        cached = {
            "cookies": {"c": "1"},
            "user_agent": BROWSER_UA,
            "download_token": None,
            "source": SESSION_SOURCE_MANUAL,
        }
        sm = esb_api._session_manager
        esb_api._session.get = MagicMock()
        esb_api._session.post = MagicMock()
        with (
            patch.object(sm, "load_session", AsyncMock(return_value=cached)),
            patch.object(sm, "validate_session_cookies", AsyncMock(return_value=False)),
            patch.object(sm, "clear_session", AsyncMock()) as clear,
        ):
            with pytest.raises(CaptchaRequiredException):
                await esb_api._ESBDataApi__login()
        clear.assert_awaited_once()
        esb_api._session.get.assert_not_called()
        esb_api._session.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejected_login_session_still_falls_through_to_login(self, esb_api):
        """Unchanged behaviour for the integration's own sessions (control)."""
        cached = {
            "cookies": {"c": "1"},
            "user_agent": BROWSER_UA,
            "download_token": "old",
            "source": SESSION_SOURCE_LOGIN,
        }
        sm = esb_api._session_manager
        esb_api._session.get = MagicMock(side_effect=RuntimeError("login attempted"))
        with (
            patch.object(sm, "load_session", AsyncMock(return_value=cached)),
            patch.object(sm, "validate_session_cookies", AsyncMock(return_value=False)),
            patch.object(sm, "clear_session", AsyncMock()),
        ):
            with pytest.raises(RuntimeError, match="login attempted"):
                await esb_api._ESBDataApi__login()


class TestSessionLocation:
    """The session file holds login cookies: it must not live where a /config backup commits."""

    @staticmethod
    def _hass(config_dir):
        hass = MagicMock()
        hass.config.path = lambda *parts: str(config_dir.joinpath(*parts))
        return hass

    def test_session_file_is_under_storage(self, tmp_path):
        sm = SessionManager(self._hass(tmp_path), "12345678901")
        assert sm._session_file.parent == tmp_path / ".storage" / "esb_smart_meter"

    def test_legacy_session_is_moved_not_left_behind(self, tmp_path):
        legacy = tmp_path / "esb_smart_meter" / "session_cache_12345678901.json"
        legacy.parent.mkdir()
        legacy.write_text('{"cookies": {"a": "1"}}', encoding="utf-8")
        sm = SessionManager(self._hass(tmp_path), "12345678901")
        assert not legacy.exists()
        assert sm._session_file.read_text(encoding="utf-8") == '{"cookies": {"a": "1"}}'


class TestFullLoginKeepsSession:
    """A failure after signin must not discard the authenticated session (2026-11-01)."""

    @pytest.mark.asyncio
    async def test_session_saved_before_token_step(
        self, esb_api, sample_esb_login_html, sample_esb_confirm_html
    ):
        import aiohttp

        def resp(text="", status=200, raises=None):
            inner = MagicMock(status=status, headers={})
            inner.text = AsyncMock(return_value=text)
            inner.raise_for_status = MagicMock(side_effect=raises)
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=inner)
            ctx.__aexit__ = AsyncMock(return_value=None)
            return ctx

        not_found = aiohttp.ClientResponseError(MagicMock(), (), status=404, message="Not Found")
        gets = [
            resp(sample_esb_login_html),  # 1 login page
            resp(sample_esb_confirm_html),  # 3 confirm
            resp("<html>My Account</html>"),  # 5 account page
            resp(raises=not_found),  # 6 consumption page: today's 404
        ]
        posts = [resp("ok"), resp("ok")]  # 2 credentials, 4 signin-oidc
        sm = esb_api._session_manager
        with (
            patch.object(sm, "load_session", AsyncMock(return_value=None)),
            patch.object(sm, "save_session", AsyncMock(return_value=True)) as save,
            patch.object(sm, "extract_cookies_from_jar", return_value={".AspNetCore.Cookies": "x"}),
            patch("custom_components.esb_smart_meter.api_client.asyncio.sleep", AsyncMock()),
            patch.object(esb_api._session, "get", side_effect=gets),
            patch.object(esb_api._session, "post", side_effect=posts),
        ):
            with pytest.raises(aiohttp.ClientResponseError):
                await esb_api._ESBDataApi__login()

        save.assert_awaited_once()
        kw = save.await_args.kwargs
        assert kw["download_token"] is None
        assert kw["source"] == SESSION_SOURCE_LOGIN
        assert kw["cookies"] == {".AspNetCore.Cookies": "x"}
