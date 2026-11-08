"""Config flow for ESB Smart Meter integration."""

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigSubentryFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_KEEP_ALIVE,
    CONF_MANUAL_COOKIES,
    CONF_MANUAL_USER_AGENT,
    CONF_MPRN,
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    DEFAULT_KEEP_ALIVE,
    DOMAIN,
    MPRN_LENGTH,
    SUBENTRY_TARIFF,
)
from .session_manager import SessionManager
from .tariff_flow import TariffSubentryFlowHandler

_LOGGER = logging.getLogger(__name__)


@callback
def configured_instances(hass: HomeAssistant) -> set[str]:
    """Return a set of configured instances."""
    return {entry.data[CONF_MPRN] for entry in hass.config_entries.async_entries(DOMAIN)}


class ESBSmartMeterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ESB Smart Meter."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return ESBSmartMeterOptionsFlow(config_entry)

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Tariff periods are added as subentries."""
        return {SUBENTRY_TARIFF: TariffSubentryFlowHandler}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate MPRN format (should be 11 digits)
            mprn = user_input[CONF_MPRN].strip()
            if not mprn.isdigit() or len(mprn) != MPRN_LENGTH:
                errors[CONF_MPRN] = "invalid_mprn"
            elif mprn in configured_instances(self.hass):
                errors["base"] = "mprn_exists"
            else:
                # Create a unique ID based on the MPRN
                await self.async_set_unique_id(mprn)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"ESB Smart Meter ({mprn})",
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_MPRN: mprn,
                    },
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_MPRN): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={"mprn_format": "11-digit MPRN number"},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Start re-authentication after ESB rejected the stored credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Ask for the new ESB password and reload the entry with it."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            if not password.strip():
                errors[CONF_PASSWORD] = "empty_password"
            else:
                # A loaded entry reloads through its update listener (HA reports
                # update_reload_and_abort when one exists); an entry whose setup failed
                # on the old password has no listener, so schedule the reload here.
                self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_PASSWORD: password})
                if entry.state is not config_entries.ConfigEntryState.LOADED:
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
        )


class ESBSmartMeterOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for ESB Smart Meter integration."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the options."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["update_interval", "manual_cookies", "keep_alive"],
            description_placeholders={
                "current_interval": self._config_entry.options.get(CONF_UPDATE_INTERVAL, 24),
            },
        )

    async def async_step_update_interval(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure update interval."""
        if user_input is not None:
            return self.async_create_entry(title="", data={**self._config_entry.options, **user_input})

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=self._config_entry.options.get(CONF_UPDATE_INTERVAL, 24),
                ): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=168)
                ),  # 1 hour to 1 week
            }
        )

        return self.async_show_form(
            step_id="update_interval",
            data_schema=data_schema,
        )

    async def async_step_keep_alive(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Turn the session keep-alive on or off (off by default)."""
        if user_input is not None:
            return self.async_create_entry(title="", data={**self._config_entry.options, **user_input})

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_KEEP_ALIVE,
                    default=self._config_entry.options.get(CONF_KEEP_ALIVE, DEFAULT_KEEP_ALIVE),
                ): bool,
            }
        )

        return self.async_show_form(step_id="keep_alive", data_schema=data_schema)

    async def async_step_manual_cookies(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle manual cookie input for CAPTCHA bypass."""
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie_string = user_input.get(CONF_MANUAL_COOKIES, "").strip()

            if not cookie_string:
                errors[CONF_MANUAL_COOKIES] = "empty_cookies"
            else:
                # Validate and save cookies
                mprn = self._config_entry.data[CONF_MPRN]
                user_agent = user_input.get(CONF_MANUAL_USER_AGENT)
                loaded = self._config_entry.state is config_entries.ConfigEntryState.LOADED

                if loaded:
                    # Through the running API, so a poll or keep-alive using the old session
                    # cannot overwrite the paste or vouch for it
                    coordinator = self._config_entry.runtime_data.coordinator
                    success = await coordinator.esb_api.save_manual_cookies(cookie_string, user_agent=user_agent)
                else:
                    success = await SessionManager(self.hass, mprn).save_manual_cookies(
                        cookie_string, user_agent=user_agent
                    )

                if success:
                    _LOGGER.info("Manual cookies saved successfully for MPRN %s", mprn)
                    if loaded:
                        # Use the new cookies now rather than after the CAPTCHA back-off interval
                        self.hass.async_create_task(coordinator.async_manual_session_saved())
                    return self.async_create_entry(title="", data=dict(self._config_entry.options))

                _LOGGER.error("Failed to save manual cookies for MPRN %s", mprn)
                errors[CONF_MANUAL_COOKIES] = "invalid_cookies"

        data_schema = vol.Schema(
            {
                vol.Required(CONF_MANUAL_COOKIES): str,
                vol.Optional(CONF_MANUAL_USER_AGENT, default=""): str,
            }
        )

        return self.async_show_form(
            step_id="manual_cookies",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "cookie_instructions": (
                    "1. Open https://myaccount.esbnetworks.ie in your browser and log in "
                    "(solve the CAPTCHA if asked)\n"
                    "2. Open DevTools (F12), go to the Network tab and reload the page\n"
                    "3. Click the first request to myaccount.esbnetworks.ie\n"
                    "4. Under Request Headers, copy the whole Cookie value into the first field\n"
                    "5. From the same request, copy the User-Agent value into the second field\n\n"
                    "document.cookie does NOT work: ESB's login cookies are HttpOnly and "
                    "invisible to it."
                )
            },
        )
