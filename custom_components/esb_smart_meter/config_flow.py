"""Config flow for ESB Smart Meter integration."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_MANUAL_COOKIES,
    CONF_MPRN,
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    DOMAIN,
    MPRN_LENGTH,
)
from .session_manager import SessionManager

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


class ESBSmartMeterOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for ESB Smart Meter integration."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the options."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["update_interval", "manual_cookies"],
            description_placeholders={
                "current_interval": self._config_entry.options.get(CONF_UPDATE_INTERVAL, 24),
            },
        )

    async def async_step_update_interval(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure update interval."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

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
                session_manager = SessionManager(self.hass, mprn)

                success = await session_manager.save_manual_cookies(cookie_string)

                if success:
                    _LOGGER.info("Manual cookies saved successfully for MPRN %s", mprn)
                    return self.async_create_entry(title="", data={})

                _LOGGER.error("Failed to save manual cookies for MPRN %s", mprn)
                errors[CONF_MANUAL_COOKIES] = "invalid_cookies"

        data_schema = vol.Schema(
            {
                vol.Required(CONF_MANUAL_COOKIES): str,
            }
        )

        return self.async_show_form(
            step_id="manual_cookies",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "cookie_instructions": (
                    "1. Open https://myaccount.esbnetworks.ie in your browser\n"
                    "2. Log in (solve CAPTCHA if needed)\n"
                    "3. Open browser DevTools (F12)\n"
                    "4. Go to Console tab\n"
                    "5. Type: document.cookie\n"
                    "6. Copy the entire output and paste below"
                )
            },
        )
