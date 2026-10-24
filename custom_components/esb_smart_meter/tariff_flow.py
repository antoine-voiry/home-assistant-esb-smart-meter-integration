"""Config subentry flow for tariff periods."""

from datetime import date
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.helpers.selector import (
    BooleanSelector,
    DateSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_DAY_RATE,
    CONF_EFFECTIVE_FROM,
    CONF_EXPORT_RATE,
    CONF_NIGHT_END,
    CONF_NIGHT_RATE,
    CONF_NIGHT_START,
    CONF_PEAK_END,
    CONF_PEAK_RATE,
    CONF_PEAK_START,
    CONF_PEAK_WEEKDAYS_ONLY,
    CONF_PRESET,
    CONF_STANDING_CHARGE,
    SUBENTRY_TARIFF,
)
from .tariff import TariffPeriod, validate_period

_STANDARD_BANDS = {
    CONF_NIGHT_START: 23,
    CONF_NIGHT_END: 8,
    CONF_PEAK_START: 17,
    CONF_PEAK_END: 19,
    CONF_EXPORT_RATE: 0.0,
}

# Example smart-tariff rates (inc. VAT) published October 2026; users check them against their bill.
# SSE: Compare Electricity, 30% discount. Bord Gáis: Selectra ex-VAT discounted rates + 9% VAT.
PRESETS: dict[str, dict[str, Any]] = {
    "sse_airtricity_smart": {
        **_STANDARD_BANDS,
        CONF_DAY_RATE: 0.3381,
        CONF_NIGHT_RATE: 0.2056,
        CONF_PEAK_RATE: 0.3788,
        CONF_STANDING_CHARGE: 0.7671,
        CONF_PEAK_WEEKDAYS_ONLY: False,
    },
    "bord_gais_smart": {
        **_STANDARD_BANDS,
        CONF_DAY_RATE: 0.3491,
        CONF_NIGHT_RATE: 0.2577,
        CONF_PEAK_RATE: 0.4250,
        CONF_STANDING_CHARGE: 0.7190,
        CONF_PEAK_WEEKDAYS_ONLY: True,
    },
    "custom": {**_STANDARD_BANDS, CONF_PEAK_WEEKDAYS_ONLY: False},
}

_RATE = NumberSelector(
    NumberSelectorConfig(min=0, step="any", mode=NumberSelectorMode.BOX, unit_of_measurement="€/kWh")
)
_HOUR = NumberSelector(NumberSelectorConfig(min=0, max=23, step=1, mode=NumberSelectorMode.BOX))

PERIOD_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EFFECTIVE_FROM): DateSelector(),
        vol.Required(CONF_DAY_RATE): _RATE,
        vol.Required(CONF_NIGHT_RATE): _RATE,
        vol.Required(CONF_PEAK_RATE): _RATE,
        vol.Required(CONF_STANDING_CHARGE): NumberSelector(
            NumberSelectorConfig(min=0, step="any", mode=NumberSelectorMode.BOX, unit_of_measurement="€/day")
        ),
        vol.Required(CONF_EXPORT_RATE): _RATE,
        vol.Required(CONF_NIGHT_START): _HOUR,
        vol.Required(CONF_NIGHT_END): _HOUR,
        vol.Required(CONF_PEAK_START): _HOUR,
        vol.Required(CONF_PEAK_END): _HOUR,
        vol.Required(CONF_PEAK_WEEKDAYS_ONLY): BooleanSelector(),
    }
)


def _title(period: TariffPeriod) -> str:
    return f"Tariff from {period.effective_from.isoformat()}"


class TariffSubentryFlowHandler(ConfigSubentryFlow):
    """Add or edit a dated tariff period."""

    _defaults: dict[str, Any]

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Pick a supplier preset to prefill the rates."""
        if user_input is not None:
            self._defaults = dict(PRESETS[user_input[CONF_PRESET]])
            return await self.async_step_rates()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PRESET, default="custom"): SelectSelector(
                        SelectSelectorConfig(
                            options=list(PRESETS), translation_key=CONF_PRESET, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
        )

    async def async_step_rates(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Enter the period's start date, rates and bands."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_period(user_input, self._other_dates(exclude=None))
            if not errors:
                period = TariffPeriod.from_data(user_input)
                return self.async_create_entry(
                    title=_title(period), data=period.as_data(), unique_id=period.effective_from.isoformat()
                )
        return self.async_show_form(
            step_id="rates",
            data_schema=self.add_suggested_values_to_schema(PERIOD_SCHEMA, user_input or self._defaults),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Edit an existing period."""
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_period(user_input, self._other_dates(exclude=subentry.subentry_id))
            if not errors:
                period = TariffPeriod.from_data(user_input)
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=_title(period),
                    data=period.as_data(),
                    unique_id=period.effective_from.isoformat(),
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(PERIOD_SCHEMA, user_input or dict(subentry.data)),
            errors=errors,
        )

    def _other_dates(self, *, exclude: str | None) -> set[date]:
        return {
            TariffPeriod.from_data(subentry.data).effective_from
            for subentry in self._get_entry().subentries.values()
            if subentry.subentry_type == SUBENTRY_TARIFF and subentry.subentry_id != exclude
        }
