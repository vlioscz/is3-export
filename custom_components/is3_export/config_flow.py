"""Config flow for the IS3 Export integration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import Is3Client, Is3ConnectionError
from .const import (
    BASE_HEX,
    CONF_DELIMITER,
    CONF_EXPORT_FILE,
    CONF_NUMBER_BASE,
    DEFAULT_HTTP_PORT,
    DEFAULT_PORT,
    DELIMITER_SPACE,
    DELIMITERS,
    DOMAIN,
    NUMBER_BASES,
)
from .export import Is3Export
from .source import (
    Is3ExportAuthError,
    Is3ExportError,
    async_fetch_export,
    read_export_file,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        # The ASCII port has no factory default -- the installer chooses a free
        # one in IDM3 -- so it must be entered, not assumed.
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_EXPORT_FILE, default=""): str,
        # No credentials are asked for: the unit serves the export as a static
        # file over HTTP on port 80, without authentication, so the iNELS
        # project password does not gate it.  A unit that somehow blocks the
        # download can still be set up from a local export file.
        # These two must match the "Third part setting" page in IDM3.
        vol.Optional(CONF_DELIMITER, default=DELIMITER_SPACE): vol.In(DELIMITERS),
        vol.Optional(CONF_NUMBER_BASE, default=BASE_HEX): vol.In(NUMBER_BASES),
    }
)


class Is3ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup dialog for one central unit."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the unit address, validate it, and load the export."""
        errors: dict[str, str] = {}

        if user_input is not None:
            export: Is3Export | None = None

            try:
                export = await self._async_load_export(user_input)
            except Is3ExportAuthError as err:
                # This unit's export is unprotected; a unit that blocks the
                # download must be set up from a local export file instead.
                _LOGGER.debug("Export is protected: %s", err)
                errors[CONF_EXPORT_FILE] = "invalid_auth"
            except Is3ExportError as err:
                _LOGGER.debug("Cannot load export: %s", err)
                errors[CONF_EXPORT_FILE] = "invalid_export"

            if not errors:
                client = Is3Client(
                    user_input[CONF_HOST],
                    user_input[CONF_PORT],
                    user_input[CONF_DELIMITER],
                    user_input[CONF_NUMBER_BASE],
                )
                try:
                    await client.async_connect()
                except Is3ConnectionError:
                    errors[CONF_HOST] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Unexpected error connecting to the IS3 unit")
                    errors["base"] = "unknown"
                finally:
                    await client.async_close()

            if not errors and export is not None:
                # The export header carries an installation ID; without it the
                # host is the best available identifier.
                header = export.header
                unique_id = (
                    header.unit_id if header and header.unit_id else user_input[CONF_HOST]
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                title = (
                    header.name if header and header.name else f"IS3 {user_input[CONF_HOST]}"
                )
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def _async_load_export(self, user_input: dict[str, Any]) -> Is3Export:
        """Load the export from disk, or from the unit when no path is given."""
        if path := user_input.get(CONF_EXPORT_FILE, "").strip():
            return await self.hass.async_add_executor_job(read_export_file, Path(path))

        # The unit serves the export over plain HTTP on port 80, unauthenticated.
        return await async_fetch_export(
            async_get_clientsession(self.hass),
            user_input[CONF_HOST],
            DEFAULT_HTTP_PORT,
        )
