"""Data update coordinator for ESB Smart Meter integration."""

import asyncio
import logging
from datetime import timedelta

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import ESBDataApi
from .const import CAPTCHA_NOTIFICATION_ID, DEFAULT_SCAN_INTERVAL, DOMAIN, ESB_MYACCOUNT_URL
from .models import ESBData
from .session_manager import CaptchaRequiredException

_LOGGER = logging.getLogger(__name__)


class ESBDataUpdateCoordinator(DataUpdateCoordinator[ESBData]):
    """
    Coordinator to manage data fetching for ESB Smart Meter sensors.

    This coordinator handles:
    - Single API call for all sensors (efficiency)
    - Automatic retry logic with exponential backoff
    - Error handling and state management
    - CAPTCHA detection and notification
    """

    def __init__(
        self,
        hass: HomeAssistant,
        esb_api: ESBDataApi,
        mprn: str,
        config_entry,
        update_interval: timedelta = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        """
        Initialize the coordinator.

        Args:
            hass: Home Assistant instance
            esb_api: ESB API client instance
            mprn: Meter Point Reference Number
            config_entry: Configuration entry for this integration
            update_interval: How often to fetch new data
        """
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{mprn}",
            update_interval=update_interval,
        )
        self.esb_api = esb_api
        self.mprn = mprn
        self.config_entry = config_entry
        self._captcha_notification_sent = False

    async def _async_update_data(self) -> ESBData:
        """
        Fetch data from ESB API.

        This method is called by the coordinator at the specified update_interval.
        All sensors will be notified when new data is available.

        Returns:
            ESBData: The fetched energy consumption data

        Raises:
            UpdateFailed: If the update fails for any reason
        """
        try:
            _LOGGER.debug("Fetching data from ESB for MPRN %s", self.mprn)

            # Fetch data from ESB API
            esb_data = await self.esb_api.fetch()

            if esb_data is None:
                raise UpdateFailed("No data returned from ESB API")

            # Validate that we have some data
            if not hasattr(esb_data, "_data") or len(esb_data._data) == 0:
                _LOGGER.warning("ESB returned empty dataset for MPRN %s", self.mprn)
                # Don't fail completely, return empty data to avoid breaking sensors
                return esb_data

            # Clear CAPTCHA notification flag on success
            if self._captcha_notification_sent:
                _LOGGER.info("ESB data fetch successful, clearing CAPTCHA notification")
                self._captcha_notification_sent = False
                # Restore normal update interval
                self.update_interval = DEFAULT_SCAN_INTERVAL
                # Dismiss the notification
                await self._dismiss_captcha_notification()

            _LOGGER.debug(
                "Successfully fetched ESB data: today=%.2f kWh, last_30_days=%.2f kWh",
                esb_data.today,
                esb_data.last_30_days,
            )

            return esb_data

        except CaptchaRequiredException as err:
            # CAPTCHA detected - send notification and stop updates
            _LOGGER.warning("CAPTCHA detected for MPRN %s: %s", self.mprn, err)

            if not self._captcha_notification_sent:
                await self._send_captcha_notification()
                self._captcha_notification_sent = True
                # Set update interval to 7 days to effectively stop polling
                # (coordinator will still honor manual refresh requests)
                _LOGGER.error(
                    "CAPTCHA protection activated. Integration will retry once per week "
                    "until ESB's restriction clears (typically 24-48 hours). "
                    "You can speed this up by logging into your ESB account via browser."
                )
                self.update_interval = timedelta(days=7)

            # Return None instead of raising to prevent coordinator retry logic
            # This stops the exponential backoff hammering
            return None

        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            # Network errors - let coordinator handle retry
            _LOGGER.error("Network error fetching ESB data for MPRN %s: %s", self.mprn, err)
            raise UpdateFailed(f"Network error: {err}") from err

        except (ValueError, KeyError) as err:
            # Data parsing errors
            _LOGGER.error("Data parsing error for MPRN %s: %s", self.mprn, err)
            raise UpdateFailed(f"Data parsing error: {err}") from err

        except Exception as err:
            # Unexpected errors
            _LOGGER.error(
                "Unexpected error fetching ESB data for MPRN %s: %s",
                self.mprn,
                err,
                exc_info=True,
            )
            raise UpdateFailed(f"Unexpected error: {err}") from err

    async def _send_captcha_notification(self) -> None:
        """Send a persistent notification and create a repair issue when CAPTCHA is detected."""
        _LOGGER.info("Sending CAPTCHA notification and creating repair issue for MPRN %s", self.mprn)

        # Create a repair issue for better visibility
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"captcha_required_{self.config_entry.entry_id}",
            is_fixable=False,  # User must manually resolve
            severity=ir.IssueSeverity.WARNING,
            translation_key="captcha_required",
            translation_placeholders={
                "mprn": self.mprn,
                "esb_url": ESB_MYACCOUNT_URL,
            },
        )

        # Create notification with improved messaging
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "notification_id": CAPTCHA_NOTIFICATION_ID,
                "title": "🔐 ESB Smart Meter: CAPTCHA Required",
                "message": (
                    "ESB Networks requires CAPTCHA verification to prevent automated access.\n\n"
                    "**Steps to resolve:**\n\n"
                    f"1. Visit [ESB Networks My Account]({ESB_MYACCOUNT_URL})\n"
                    "2. Complete the CAPTCHA challenge and log in\n"
                    "3. Leave your browser session active for 5 minutes\n"
                    "4. The integration will automatically retry\n\n"
                    "**Alternative:** Wait 24-48 hours for automatic clearance.\n\n"
                    f"📋 MPRN: `{self.mprn}`\n\n"
                    "This notification will clear automatically once data retrieval succeeds.\n\n"
                    "**Need help?** See the [CAPTCHA Setup Guide](https://github.com/your-repo/blob/master/CAPTCHA-SETUP.md) "
                    "for advanced cookie extraction methods."
                ),
            },
        )

        # Send mobile notification with action button
        try:
            await self.hass.services.async_call(
                "notify",
                "notify",
                {
                    "title": "🔐 ESB Smart Meter: CAPTCHA Required",
                    "message": (
                        f"MPRN {self.mprn}: CAPTCHA verification needed. "
                        "Tap to open ESB website and complete verification."
                    ),
                    "data": {
                        "actions": [
                            {
                                "action": "URI",
                                "title": "Open ESB Account",
                                "uri": ESB_MYACCOUNT_URL,
                            }
                        ],
                        "tag": f"esb_captcha_{self.mprn}",
                        "importance": "high",
                        "channel": "ESB Smart Meter Alerts",
                    },
                },
            )
        except Exception as err:
            _LOGGER.debug("Could not send mobile notification: %s", err)

    async def _dismiss_captcha_notification(self) -> None:
        """Dismiss the CAPTCHA notification and repair issue."""
        _LOGGER.debug("Dismissing CAPTCHA notification and repair issue for MPRN %s", self.mprn)

        # Dismiss the repair issue
        ir.async_delete_issue(
            self.hass,
            DOMAIN,
            f"captcha_required_{self.config_entry.entry_id}",
        )

        # Dismiss the persistent notification
        await self.hass.services.async_call(
            "persistent_notification",
            "dismiss",
            {"notification_id": CAPTCHA_NOTIFICATION_ID},
        )
