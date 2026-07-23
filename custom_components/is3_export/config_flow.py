"""Config flow for the IS3 Export integration.

Both the initial setup and a later reconfigure share one form and one
validation path: load the export (from the unit or a local file) and probe the
ASCII port.  Reconfigure lets the port, delimiter or number base be corrected
without removing the integration -- which matters because a wrong delimiter or
base leaves every read unanswered (see the repair card in ``issues.py``).
"""

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


def build_schema(defaults: dict[str, Any]) -> vol.Schema:
    """The setup form, pre-filled from ``defaults``.

    Empty defaults give a fresh install (host blank, the documented port and the
    common delimiter/base); the reconfigure step passes the entry's own values
    so the form opens on what is currently set.
    """
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST, default=defaults.get(CONF_HOST, vol.UNDEFINED)
            ): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): int,
            vol.Optional(
                CONF_EXPORT_FILE, default=defaults.get(CONF_EXPORT_FILE, "")
            ): str,
            vol.Optional(
                CONF_DELIMITER, default=defaults.get(CONF_DELIMITER, DELIMITER_SPACE)
            ): vol.In(DELIMITERS),
            vol.Optional(
                CONF_NUMBER_BASE, default=defaults.get(CONF_NUMBER_BASE, BASE_HEX)
            ): vol.In(NUMBER_BASES),
        }
    )


def unit_identity(export: Is3Export, host: str) -> tuple[str, str]:
    """The unique id and title for a unit.

    The export header carries an installation id and name; without them the host
    is the best identifier and a generic title is used.
    """
    header = export.header
    unique_id = header.unit_id if header and header.unit_id else host
    title = header.name if header and header.name else f"IS3 {host}"
    return unique_id, title


class Is3ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup and reconfigure dialogs for one central unit."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the unit address, validate it, and load the export."""
        errors: dict[str, str] = {}

        if user_input is not None:
            export, errors = await self._async_validate(user_input)
            if not errors and export is not None:
                unique_id, title = unit_identity(export, user_input[CONF_HOST])
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=build_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the connection settings of an existing unit."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            export, errors = await self._async_validate(user_input)
            if not errors and export is not None:
                unique_id, _title = unit_identity(export, user_input[CONF_HOST])
                await self.async_set_unique_id(unique_id)
                # Reconfiguring must stay on the same unit, not repoint the entry
                # at a different one and shadow whatever entry already owns it.
                self._abort_if_unique_id_mismatch(reason="wrong_unit")
                return self.async_update_reload_and_abort(entry, data_updates=user_input)

        defaults = user_input if user_input is not None else dict(entry.data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=build_schema(defaults),
            errors=errors,
        )

    async def _async_validate(
        self, user_input: dict[str, Any]
    ) -> tuple[Is3Export | None, dict[str, str]]:
        """Load the export and probe the ASCII port; return (export, errors)."""
        errors: dict[str, str] = {}
        export: Is3Export | None = None

        try:
            export = await self._async_load_export(user_input)
        except Is3ExportAuthError as err:
            # This installation's export is unprotected; a unit that blocks the
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

        return export, errors

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
