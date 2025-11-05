"""Config flow for ESB Smart Meter integration."""
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_MPRN, CONF_PASSWORD, CONF_USERNAME, DOMAIN

_LOGGER = logging.getLogger(__name__)


@callback
def configured_instances(hass: HomeAssistant) -> set[str]:
    """Return a set of configured instances."""
    return {
        entry.data[CONF_MPRN]
        for entry in hass.config_entries.async_entries(DOMAIN)
    }


class ESBSmartMeterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ESB Smart Meter."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate MPRN format (should be 11 digits)
            mprn = user_input[CONF_MPRN].strip()
            if not mprn.isdigit() or len(mprn) != 11:
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
                    }
                )

        data_schema = vol.Schema({
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Required(CONF_MPRN): str,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "mprn_format": "11-digit MPRN number"
            }
        )
