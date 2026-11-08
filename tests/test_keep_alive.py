"""The opt-in session keep-alive, and telling a session that expired from a paste ESB never accepted.

ESB's portal session expires after 20 minutes without use, and ESB re-issues the session cookie on
use. An open portal tab keeps it alive with GET /ac/e every 9 minutes; the keep-alive does the same,
saves re-issued cookies through SessionManager.save_session, and never logs in: a session ESB no
longer accepts is only marked expired, and the next scheduled poll decides what to do.
"""

import asyncio
import json
import stat
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.util import dt as dt_util
from yarl import URL

from custom_components.esb_smart_meter.api_client import ESBDataApi
from custom_components.esb_smart_meter.config_flow import ESBSmartMeterOptionsFlow
from custom_components.esb_smart_meter.const import (
    CONF_KEEP_ALIVE,
    CONF_UPDATE_INTERVAL,
    ESB_KEEPALIVE_URL,
    SESSION_ALIVE,
    SESSION_ERROR,
    SESSION_EXPIRED,
    SESSION_NONE,
    SESSION_SOURCE_LOGIN,
    SESSION_SOURCE_MANUAL,
)
from custom_components.esb_smart_meter.coordinator import ESBDataUpdateCoordinator, session_signal
from custom_components.esb_smart_meter.sensor import SessionConfirmedSensor
from custom_components.esb_smart_meter.session_manager import CaptchaRequiredException, SessionManager

MPRN = "12345678901"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
PORTAL = URL("https://myaccount.esbnetworks.ie/")


def _hass(tmp_path):
    """A hass stand-in whose executor runs the job, so session files are real files."""
    hass = MagicMock()
    hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))

    async def run(func, *args):
        return func(*args)

    hass.async_add_executor_job = run
    return hass


def _read(sm):
    return json.loads(sm._session_file.read_text(encoding="utf-8"))


class _Response:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Portal:
    """Stands in for the integration's aiohttp session: a real cookie jar, a scripted ESB.

    Every request is recorded, so a test can say exactly what was sent, and that nothing else was.
    """

    def __init__(self, status=200, reissue=None, error=None):
        self.cookie_jar = aiohttp.CookieJar(quote_cookie=False)
        self.status = status
        self.reissue = reissue or {}
        self.error = error
        self.requests = []
        self.posts = []

    def get(self, url, headers=None, allow_redirects=True, timeout=None):
        self.requests.append(
            {"url": url, "headers": headers or {}, "allow_redirects": allow_redirects,
             "cookies": {c.key: c.value for c in self.cookie_jar}}
        )
        if self.error:
            raise self.error
        if self.reissue:
            self.cookie_jar.update_cookies(self.reissue, PORTAL)
        return _Response(self.status)

    def post(self, *args, **kwargs):
        self.posts.append(args)
        raise AssertionError("the keep-alive must never post (that is a login)")


async def _session(sm, cookies=None, **fields):
    """Save a session the way the integration does, then return it as stored."""
    await sm.save_session(cookies=cookies or {".AspNetCore.Cookies": "old"}, user_agent=UA, **fields)
    return _read(sm)


class TestKeepAliveRequest:
    @pytest.mark.asyncio
    async def test_no_session_sends_nothing(self, tmp_path):
        portal = _Portal()
        assert await SessionManager(_hass(tmp_path), MPRN).keep_alive(portal) == SESSION_NONE
        assert portal.requests == []

    @pytest.mark.asyncio
    async def test_alive_saves_reissued_cookies_through_save_session(self, tmp_path):
        sm = SessionManager(_hass(tmp_path), MPRN)
        await _session(sm, {".AspNetCore.Cookies": "old", "ARRAffinity": "a"},
                       download_token="tok", source=SESSION_SOURCE_MANUAL, confirmed=True)
        portal = _Portal(reissue={".AspNetCore.Cookies": "new"})

        with patch.object(sm, "save_session", wraps=sm.save_session) as save:
            assert await sm.keep_alive(portal) == SESSION_ALIVE

        save.assert_awaited_once_with(
            cookies={".AspNetCore.Cookies": "new", "ARRAffinity": "a"},
            user_agent=UA, download_token="tok", source=SESSION_SOURCE_MANUAL, confirmed=True,
        )
        stored = _read(sm)
        assert stored["cookies"][".AspNetCore.Cookies"] == "new"
        assert stored["last_ok"] and stored["source"] == SESSION_SOURCE_MANUAL
        # save_session's file: under .storage/, owner-only
        assert sm._session_file.parent == tmp_path / ".storage" / "esb_smart_meter"
        assert stat.S_IMODE(sm._session_file.stat().st_mode) == 0o600

        [sent] = portal.requests
        assert sent["url"] == ESB_KEEPALIVE_URL and sent["allow_redirects"] is False
        assert sent["headers"]["User-Agent"] == UA
        assert sent["cookies"] == {".AspNetCore.Cookies": "old", "ARRAffinity": "a"}

    @pytest.mark.asyncio
    async def test_alive_without_new_cookies_only_confirms(self, tmp_path):
        sm = SessionManager(_hass(tmp_path), MPRN)
        before = await _session(sm, download_token="tok", confirmed=True)
        before["expires_at"] = (dt_util.utcnow() + timedelta(days=1)).isoformat()  # close to its 14 days
        sm._write_session_file(before)

        with patch.object(sm, "save_session", wraps=sm.save_session) as save:
            assert await sm.keep_alive(_Portal()) == SESSION_ALIVE

        save.assert_not_awaited()
        after = _read(sm)
        assert after["last_ok"] > before["last_ok"] and after["expired_at"] is None
        # ESB still accepts it, so it is not thrown away at the end of its first 14 days
        assert after["expires_at"] > (dt_util.utcnow() + timedelta(days=13)).isoformat()
        unchanged = ("last_ok", "expired_at", "expires_at")
        assert {k: v for k, v in after.items() if k not in unchanged} == {
            k: v for k, v in before.items() if k not in unchanged
        }

    @pytest.mark.asyncio
    async def test_a_session_no_poll_has_validated_is_not_pinged(self, tmp_path):
        """A fresh paste is validated by the poll it triggers, never vouched for by /ac/e."""
        sm = SessionManager(_hass(tmp_path), MPRN)
        await _session(sm, source=SESSION_SOURCE_MANUAL)
        portal = _Portal()
        assert await sm.keep_alive(portal) == SESSION_NONE
        assert portal.requests == []
        assert _read(sm)["last_ok"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [302, 401, 403])
    async def test_login_answer_marks_expired_and_keeps_the_session(self, tmp_path, status):
        sm = SessionManager(_hass(tmp_path), MPRN)
        await _session(sm, confirmed=True)
        portal = _Portal(status=status)

        assert await sm.keep_alive(portal) == SESSION_EXPIRED

        stored = _read(sm)  # kept, for the next poll to decide on
        assert stored["expired_at"] and stored["cookies"] == {".AspNetCore.Cookies": "old"}
        assert sm.status["expired_at"] == stored["expired_at"]
        # an expired session is not pinged again
        assert await sm.keep_alive(portal) == SESSION_EXPIRED
        assert len(portal.requests) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [404, 429, 500, 503])
    async def test_outage_answer_says_nothing_about_the_session(self, tmp_path, status):
        sm = SessionManager(_hass(tmp_path), MPRN)
        before = await _session(sm, confirmed=True)
        assert await sm.keep_alive(_Portal(status=status)) == SESSION_ERROR
        assert _read(sm) == before

    @pytest.mark.asyncio
    async def test_network_error_leaves_the_session_alone(self, tmp_path):
        sm = SessionManager(_hass(tmp_path), MPRN)
        before = await _session(sm, confirmed=True)
        portal = _Portal(error=aiohttp.ClientConnectionError("unreachable"))
        assert await sm.keep_alive(portal) == SESSION_ERROR
        assert _read(sm) == before

    @pytest.mark.asyncio
    async def test_rejected_paste_is_not_pinged(self, tmp_path):
        sm = SessionManager(_hass(tmp_path), MPRN)
        await sm.mark_rejected(await _session(sm, source=SESSION_SOURCE_MANUAL))
        portal = _Portal()
        assert await sm.keep_alive(portal) == SESSION_NONE
        assert portal.requests == []

    @pytest.mark.asyncio
    async def test_session_past_its_expiry_is_not_pinged(self, tmp_path):
        sm = SessionManager(_hass(tmp_path), MPRN)
        stored = await _session(sm, confirmed=True)
        stored["expires_at"] = (dt_util.utcnow() - timedelta(minutes=1)).isoformat()
        sm._write_session_file(stored)
        portal = _Portal()
        assert await sm.keep_alive(portal) == SESSION_NONE
        assert portal.requests == []


class TestKeepAliveAnswers:
    @pytest.mark.asyncio
    async def test_any_success_answer_is_alive(self, tmp_path):
        sm = SessionManager(_hass(tmp_path), MPRN)
        await _session(sm, confirmed=True)
        assert await sm.keep_alive(_Portal(status=204)) == SESSION_ALIVE
        assert _read(sm)["expired_at"] is None


class TestKeepAliveNeverLogsIn:
    @staticmethod
    def _api(tmp_path, portal):
        return ESBDataApi(hass=_hass(tmp_path), session=portal, username="u@example.com",
                          password="p", mprn=MPRN)

    @pytest.mark.asyncio
    async def test_expired_session_sends_only_the_keep_alive(self, tmp_path):
        portal = _Portal(status=302)
        api = self._api(tmp_path, portal)
        await _session(api._session_manager, confirmed=True)

        assert await api.keep_alive() == SESSION_EXPIRED

        assert [r["url"] for r in portal.requests] == [ESB_KEEPALIVE_URL]
        assert portal.posts == []

    @pytest.mark.asyncio
    async def test_skipped_while_a_poll_holds_the_session(self, tmp_path):
        portal = _Portal()
        api = self._api(tmp_path, portal)
        await _session(api._session_manager, confirmed=True)

        async with api._session_lock:
            # bounded: a keep-alive that waited for the lock would hang here, not fail
            assert await asyncio.wait_for(api.keep_alive(), timeout=2) is None
        assert portal.requests == []
        assert await api.keep_alive() == SESSION_ALIVE  # control: the same call, unlocked

    @pytest.mark.asyncio
    async def test_a_paste_waits_for_a_poll_using_the_old_session(self, tmp_path):
        api = self._api(tmp_path, _Portal())
        await _session(api._session_manager, confirmed=True)

        async with api._session_lock:
            paste = asyncio.ensure_future(api.save_manual_cookies(".AspNetCore.Cookies=pasted"))
            await asyncio.wait({paste}, timeout=0.2)  # real loop turns (asyncio.sleep is mocked here)
            assert not paste.done()  # nothing written while the poll holds the session
            assert _read(api._session_manager)["cookies"] == {".AspNetCore.Cookies": "old"}
        assert await paste is True
        stored = _read(api._session_manager)
        assert stored["cookies"] == {".AspNetCore.Cookies": "pasted"} and stored["last_ok"] is None

    @staticmethod
    def _coordinator(state):
        return SimpleNamespace(
            keep_alive_state=SESSION_ALIVE,
            esb_api=SimpleNamespace(keep_alive=AsyncMock(return_value=state)),
            hass=MagicMock(),
            config_entry=SimpleNamespace(entry_id="e1"),
            async_request_refresh=AsyncMock(),
            async_refresh=AsyncMock(),
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", [SESSION_EXPIRED, SESSION_ERROR, SESSION_NONE, SESSION_ALIVE])
    async def test_coordinator_never_refreshes(self, state):
        fake = self._coordinator(state)
        with patch("custom_components.esb_smart_meter.coordinator.async_dispatcher_send") as sent:
            await ESBDataUpdateCoordinator.async_keep_alive(fake)
        fake.async_request_refresh.assert_not_awaited()
        fake.async_refresh.assert_not_awaited()
        assert fake.keep_alive_state == state
        sent.assert_called_once_with(fake.hass, session_signal("e1"))

    @pytest.mark.asyncio
    async def test_coordinator_keeps_the_last_state_when_skipped(self):
        fake = self._coordinator(None)
        with patch("custom_components.esb_smart_meter.coordinator.async_dispatcher_send"):
            await ESBDataUpdateCoordinator.async_keep_alive(fake)
        assert fake.keep_alive_state == SESSION_ALIVE


class TestExpiredVersusRejected:
    """At the next poll: a session ESB accepted before has expired; a paste it never accepted was rejected."""

    @staticmethod
    async def _poll(tmp_path, cached_fields, check_ok):
        api = ESBDataApi(hass=_hass(tmp_path), session=MagicMock(), username="u@example.com",
                         password="p", mprn=MPRN)
        sm = api._session_manager
        await _session(sm, **cached_fields)
        api._session.get = MagicMock(side_effect=RuntimeError("login attempted"))
        with patch.object(sm, "check_session", AsyncMock(return_value=check_ok)):
            try:
                result = await api._ESBDataApi__login()
            except (RuntimeError, CaptchaRequiredException) as err:
                result = err
        return api, sm, result

    @pytest.mark.asyncio
    async def test_expired_paste_gets_one_fresh_login(self, tmp_path):
        api, sm, result = await self._poll(tmp_path, {"source": SESSION_SOURCE_MANUAL, "confirmed": True}, False)
        assert isinstance(result, RuntimeError)  # the login started
        assert not sm._session_file.exists()  # no rejection marker: scripted logins stay allowed

    @pytest.mark.asyncio
    async def test_paste_never_accepted_is_rejected(self, tmp_path):
        """1.5.0's behaviour, kept: no scripted login, and a 24-hour marker."""
        api, sm, result = await self._poll(tmp_path, {"source": SESSION_SOURCE_MANUAL}, False)
        assert isinstance(result, CaptchaRequiredException)
        api._session.get.assert_not_called()
        assert _read(sm)["rejected_at"]

    @pytest.mark.asyncio
    async def test_accepted_session_is_confirmed(self, tmp_path):
        api, sm, result = await self._poll(
            tmp_path, {"source": SESSION_SOURCE_MANUAL, "download_token": "tok"}, True
        )
        assert result["download_token"] == "tok"
        stored = _read(sm)
        assert stored["last_ok"] and stored["expired_at"] is None
        api._session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_expired_mark_is_cleared_when_esb_accepts_the_session(self, tmp_path):
        sm = SessionManager(_hass(tmp_path), MPRN)
        await _session(sm, confirmed=True)
        await sm.mark_expired()
        await sm.mark_confirmed()
        assert _read(sm)["expired_at"] is None


class TestBookkeeping:
    @pytest.mark.asyncio
    async def test_a_paste_is_unconfirmed_and_a_login_is_confirmed(self, tmp_path):
        sm = SessionManager(_hass(tmp_path), MPRN)
        await sm.save_manual_cookies("a=1", user_agent=UA)
        assert _read(sm)["last_ok"] is None
        await sm.save_session(cookies={"a": "1"}, user_agent=UA, confirmed=True)
        assert _read(sm)["last_ok"]

    @pytest.mark.asyncio
    async def test_status_never_carries_cookies(self, tmp_path):
        sm = SessionManager(_hass(tmp_path), MPRN)
        await _session(sm, download_token="secret-token", confirmed=True)
        assert set(sm.status) == {"source", "last_ok", "expired_at", "rejected_at"}
        assert "old" not in json.dumps(sm.status) and "secret-token" not in json.dumps(sm.status)

    @pytest.mark.asyncio
    async def test_status_follows_reads_and_clears(self, tmp_path):
        written = SessionManager(_hass(tmp_path), MPRN)
        await _session(written, source=SESSION_SOURCE_LOGIN, confirmed=True)
        fresh = SessionManager(_hass(tmp_path), MPRN)  # as after a restart
        assert fresh.status == {}
        assert await fresh.load_session()
        assert fresh.status["source"] == SESSION_SOURCE_LOGIN and fresh.status["last_ok"]
        await fresh.clear_session()
        assert fresh.status == {}

    @pytest.mark.asyncio
    async def test_a_session_saved_by_1_5_0_still_loads(self, tmp_path):
        """A session saved by 1.5.0 (no last_ok) keeps working after the upgrade."""
        sm = SessionManager(_hass(tmp_path), MPRN)
        stored = await _session(sm)
        del stored["last_ok"]
        sm._write_session_file(stored)
        assert (await SessionManager(_hass(tmp_path), MPRN).load_session())["cookies"] == stored["cookies"]


class TestKeepAliveOption:
    @staticmethod
    def _flow(options):
        entry = MagicMock()
        entry.options = options
        flow = ESBSmartMeterOptionsFlow(entry)
        flow.hass = MagicMock()
        return flow

    @pytest.mark.asyncio
    async def test_menu_offers_it(self):
        result = await self._flow({}).async_step_init()
        assert "keep_alive" in result["menu_options"]

    @pytest.mark.asyncio
    async def test_off_by_default(self):
        result = await self._flow({}).async_step_keep_alive()
        assert result["type"] == "form" and result["step_id"] == "keep_alive"
        [key] = result["data_schema"].schema
        assert key == CONF_KEEP_ALIVE and key.default() is False

    @pytest.mark.asyncio
    async def test_saving_keeps_the_other_options(self):
        result = await self._flow({CONF_UPDATE_INTERVAL: 6}).async_step_keep_alive({CONF_KEEP_ALIVE: True})
        assert result["type"] == "create_entry"
        assert result["data"] == {CONF_UPDATE_INTERVAL: 6, CONF_KEEP_ALIVE: True}

    @pytest.mark.asyncio
    async def test_form_shows_the_saved_choice(self):
        result = await self._flow({CONF_KEEP_ALIVE: True}).async_step_keep_alive()
        [key] = result["data_schema"].schema
        assert key.default() is True


class TestSessionSensor:
    @staticmethod
    def _sensor(status, state=None):
        coordinator = SimpleNamespace(esb_api=SimpleNamespace(session_status=status), keep_alive_state=state)
        return SessionConfirmedSensor(coordinator=coordinator, mprn=MPRN, entry_id="e1")

    def test_value_is_when_esb_last_accepted_the_session(self):
        sensor = self._sensor({"last_ok": "2026-11-08T11:41:23+00:00", "source": "manual"}, SESSION_ALIVE)
        assert sensor.native_value.isoformat() == "2026-11-08T11:41:23+00:00"
        assert sensor.extra_state_attributes == {
            "source": "manual", "expired_at": None, "rejected_at": None, "keep_alive": SESSION_ALIVE,
        }

    @pytest.mark.parametrize("status", [{}, {"last_ok": None}, {"last_ok": "not a time"}])
    def test_unknown_without_a_confirmed_session(self, status):
        assert self._sensor(status).native_value is None

    def test_identity(self):
        sensor = self._sensor({})
        assert sensor.should_poll is False
        assert sensor.entity_id == "sensor.esb_smart_meter_session_confirmed"
        assert sensor.unique_id == f"{MPRN}_session_confirmed"
        assert sensor.translation_key == "session_confirmed"
