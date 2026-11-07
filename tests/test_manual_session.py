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

import json
from pathlib import Path
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util

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
            side_effect=[_response(), _response({"token": "tok-1"})]
        )
        esb_api._session.post = MagicMock()
        with (
            patch.object(sm, "load_session", AsyncMock(return_value=cached)),
            patch.object(sm, "check_session", AsyncMock(return_value=True)),
            patch.object(sm, "load_cookies_to_jar"),
            patch.object(sm, "save_session", AsyncMock(return_value=True)) as save,
            patch("custom_components.esb_smart_meter.api_client.asyncio.sleep", AsyncMock()),
        ):
            result = await esb_api._ESBDataApi__login()

        assert result == {
            "download_token": "tok-1",
            "user_agent": BROWSER_UA,
            "source": SESSION_SOURCE_MANUAL,
            "token_from_cache": False,
        }
        urls = [c.args[0] for c in esb_api._session.get.call_args_list]
        assert urls == [ESB_CONSUMPTION_URL, ESB_TOKEN_URL]
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
            patch.object(sm, "check_session", AsyncMock(return_value=True)),
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
            patch.object(sm, "check_session", AsyncMock(return_value=False)),
            patch.object(sm, "mark_rejected", AsyncMock()) as mark,
        ):
            with pytest.raises(CaptchaRequiredException):
                await esb_api._ESBDataApi__login()
        mark.assert_awaited_once()
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
            patch.object(sm, "check_session", AsyncMock(return_value=False)),
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

    @pytest.mark.asyncio
    async def test_legacy_session_is_moved_not_left_behind(self, tmp_path):
        legacy = tmp_path / "esb_smart_meter" / "session_cache_12345678901.json"
        legacy.parent.mkdir()
        legacy.write_text('{"cookies": {"a": "1"}}', encoding="utf-8")
        hass = self._hass(tmp_path)

        async def run(func, *args):
            return func(*args)

        hass.async_add_executor_job = run
        sm = SessionManager(hass, "12345678901")
        assert legacy.exists()  # nothing touched the disk on the event loop
        await sm._async_ensure_storage()
        assert not legacy.exists()
        assert sm._session_file.read_text(encoding="utf-8") == '{"cookies": {"a": "1"}}'

    @pytest.mark.asyncio
    async def test_load_session_moves_legacy_file_and_returns_it(self, tmp_path):
        legacy = tmp_path / "esb_smart_meter" / "session_cache_12345678901.json"
        legacy.parent.mkdir()
        expires_at = (dt_util.utcnow() + timedelta(days=1)).isoformat()
        legacy.write_text(
            json.dumps(
                {"cookies": {"a": "1"}, "mprn": "12345678901", "expires_at": expires_at}
            ),
            encoding="utf-8",
        )
        hass = self._hass(tmp_path)

        async def run(func, *args):
            return func(*args)

        hass.async_add_executor_job = run
        sm = SessionManager(hass, "12345678901")

        session = await sm.load_session()

        assert session is not None
        assert session["cookies"] == {"a": "1"}
        assert not legacy.exists()


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


class TestCookiesSentAsPasted:
    def test_value_wrapped_mid_token_is_rejoined(self, mock_hass):
        sm = SessionManager(mock_hass, "12345678901")
        pasted = "Cookie: .AspNetCore.Cookies=CfDJ8AbC\nDeF; ARRAffinity=x"
        assert sm._parse_cookie_string(pasted) == {".AspNetCore.Cookies": "CfDJ8AbCDeF", "ARRAffinity": "x"}

    @pytest.mark.asyncio
    async def test_values_are_not_quoted_and_bad_names_are_skipped(self, mock_hass):
        import aiohttp
        from yarl import URL

        sm = SessionManager(mock_hass, "12345678901")
        jar = aiohttp.CookieJar(quote_cookie=False, unsafe=True)  # as utils.create_esb_session builds it
        sm.load_cookies_to_jar(jar, {"OptanonConsent": "isGpcEnabled=0&a=b/c,d", "bad[name]": "v"})
        sent = jar.filter_cookies(URL("https://myaccount.esbnetworks.ie/")).output(header="", sep=";").strip()
        assert sent == "OptanonConsent=isGpcEnabled=0&a=b/c,d"


class TestResumeSession:
    @pytest.mark.asyncio
    async def test_network_error_keeps_the_session(self, esb_api):
        import aiohttp

        cached = {"cookies": {"c": "1"}, "user_agent": BROWSER_UA, "download_token": None, "source": SESSION_SOURCE_MANUAL}
        sm = esb_api._session_manager
        with (
            patch.object(sm, "load_session", AsyncMock(return_value=cached)),
            patch.object(sm, "check_session", AsyncMock(side_effect=aiohttp.ClientConnectionError("dns"))),
            patch.object(sm, "clear_session", AsyncMock()) as clear,
            patch.object(sm, "mark_rejected", AsyncMock(), create=True) as mark,
        ):
            with pytest.raises(aiohttp.ClientConnectionError):
                await esb_api._ESBDataApi__login()
        clear.assert_not_awaited()
        mark.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_token_renewal_saves_the_jar_cookies(self, esb_api):
        cached = {"cookies": {"c": "1"}, "user_agent": BROWSER_UA, "download_token": None, "source": SESSION_SOURCE_MANUAL}
        sm = esb_api._session_manager
        esb_api._session.get = MagicMock(side_effect=[_response(), _response({"token": "tok-1"})])
        with (
            patch.object(sm, "load_session", AsyncMock(return_value=cached)),
            patch.object(sm, "check_session", AsyncMock(return_value=True)),
            patch.object(sm, "extract_cookies_from_jar", return_value={"c": "1", "XSRF-TOKEN": "new"}),
            patch.object(sm, "save_session", AsyncMock(return_value=True)) as save,
            patch("custom_components.esb_smart_meter.api_client.asyncio.sleep", AsyncMock()),
        ):
            await esb_api._ESBDataApi__login()
        assert save.await_args.kwargs["cookies"] == {"c": "1", "XSRF-TOKEN": "new"}


class TestRejectedPasteMarker:
    @pytest.mark.asyncio
    async def test_marker_blocks_login_without_any_request(self, esb_api):
        from homeassistant.util import dt as dt_util

        cached = {
            "cookies": {"c": "1"}, "user_agent": BROWSER_UA, "source": SESSION_SOURCE_MANUAL,
            "rejected_at": dt_util.utcnow().isoformat(),
        }
        sm = esb_api._session_manager
        esb_api._session.get = MagicMock()
        esb_api._session.post = MagicMock()
        with patch.object(sm, "load_session", AsyncMock(return_value=cached)):
            with pytest.raises(CaptchaRequiredException):
                await esb_api._ESBDataApi__login()
        esb_api._session.get.assert_not_called()
        esb_api._session.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_old_marker_allows_login_again(self, esb_api):
        from datetime import timedelta

        from homeassistant.util import dt as dt_util

        cached = {
            "cookies": {"c": "1"}, "user_agent": BROWSER_UA, "source": SESSION_SOURCE_MANUAL,
            "rejected_at": (dt_util.utcnow() - timedelta(hours=25)).isoformat(),
        }
        sm = esb_api._session_manager
        esb_api._session.get = MagicMock(side_effect=RuntimeError("login attempted"))
        with (
            patch.object(sm, "load_session", AsyncMock(return_value=cached)),
            patch.object(sm, "clear_session", AsyncMock()) as clear,
        ):
            with pytest.raises(RuntimeError, match="login attempted"):
                await esb_api._ESBDataApi__login()
        clear.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_rejected_keeps_cookies_and_drops_token(self, tmp_path):
        hass = MagicMock()
        hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))

        async def run(func, *args):
            return func(*args)

        hass.async_add_executor_job = run
        sm = SessionManager(hass, "12345678901")
        await sm.save_session(cookies={"c": "1"}, user_agent=BROWSER_UA, download_token="t", source=SESSION_SOURCE_MANUAL)
        await sm.mark_rejected(await sm.load_session())
        saved = await sm.load_session()
        assert saved["cookies"] == {"c": "1"} and saved["download_token"] is None
        assert sm.rejection_active(saved) is True


class TestStaleDownloadToken:
    @staticmethod
    def _refused(status):
        import aiohttp

        return aiohttp.ClientResponseError(MagicMock(), (), status=status)

    @pytest.mark.asyncio
    async def test_refused_cached_token_is_renewed_once(self, esb_api):
        login = {"download_token": "old", "user_agent": BROWSER_UA, "source": SESSION_SOURCE_LOGIN, "token_from_cache": True}
        with (
            patch.object(esb_api, "_ESBDataApi__login", AsyncMock(return_value=login)),
            patch.object(esb_api, "_ESBDataApi__fetch_data", AsyncMock(side_effect=[self._refused(400), "csv"])) as data,
            patch.object(esb_api, "_ESBDataApi__renew_download_token", AsyncMock(return_value="new")) as renew,
            patch.object(esb_api, "_ESBDataApi__csv_to_dict", return_value=[]),
        ):
            esb_api._hass.async_add_executor_job = AsyncMock(return_value=[])
            await esb_api.fetch()
        renew.assert_awaited_once_with(BROWSER_UA, SESSION_SOURCE_LOGIN, account_page_loaded=False)
        assert data.await_args_list[1].args == ("new", BROWSER_UA)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("from_cache, status", [(False, 400), (True, 429), (True, 503)])
    async def test_other_failures_are_not_retried(self, esb_api, from_cache, status):
        import aiohttp

        login = {"download_token": "t", "user_agent": BROWSER_UA, "source": SESSION_SOURCE_LOGIN, "token_from_cache": from_cache}
        with (
            patch.object(esb_api, "_ESBDataApi__login", AsyncMock(return_value=login)),
            patch.object(esb_api, "_ESBDataApi__fetch_data", AsyncMock(side_effect=self._refused(status))),
            patch.object(esb_api, "_ESBDataApi__renew_download_token", AsyncMock()) as renew,
        ):
            with pytest.raises(aiohttp.ClientResponseError):
                await esb_api.fetch()
        renew.assert_not_awaited()


class TestRejectionMarkerLifetime:
    @staticmethod
    def _sm(tmp_path):
        hass = MagicMock()
        hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))

        async def run(func, *args):
            return func(*args)

        hass.async_add_executor_job = run
        return SessionManager(hass, "12345678901")

    @pytest.mark.asyncio
    async def test_marker_outlives_nearly_expired_session(self, tmp_path):
        import json
        from datetime import timedelta

        from homeassistant.util import dt as dt_util

        sm = self._sm(tmp_path)
        await sm.save_session(cookies={"c": "1"}, user_agent=BROWSER_UA, download_token="t", source=SESSION_SOURCE_MANUAL)
        data = json.loads(Path(sm._session_file).read_text())
        data["expires_at"] = (dt_util.utcnow() + timedelta(hours=1)).isoformat()
        Path(sm._session_file).write_text(json.dumps(data))
        await sm.mark_rejected(await sm.load_session())
        saved = await sm.load_session()
        assert saved is not None
        from datetime import datetime

        assert datetime.fromisoformat(saved["expires_at"]) >= dt_util.utcnow() + timedelta(hours=23)

    @pytest.mark.asyncio
    async def test_unparsable_rejected_at_is_not_active(self, tmp_path):
        assert self._sm(tmp_path).rejection_active({"rejected_at": "garbage"}) is False

    @pytest.mark.asyncio
    async def test_session_file_is_owner_only(self, tmp_path):
        import stat

        sm = self._sm(tmp_path)
        Path(sm._session_file).parent.mkdir(parents=True, exist_ok=True)
        Path(sm._session_file).write_text("{}")
        Path(sm._session_file).chmod(0o644)
        await sm.save_session(cookies={"c": "1"}, user_agent=BROWSER_UA, download_token="t", source=SESSION_SOURCE_MANUAL)
        assert stat.S_IMODE(Path(sm._session_file).stat().st_mode) == 0o600
