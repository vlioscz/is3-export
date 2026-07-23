"""Diagnostics for the IS3 Export integration.

A one-click, redacted snapshot of what the integration sees: the config (with
the host and any credentials masked), the unit's capabilities, and every entry
the export produced together with how it was classified.  It exists so a bug
report can carry the data that would otherwise have to be gathered by hand off
the ASCII port -- the maintainer cannot reach a user's unit.

Entry names and hardware ids are kept deliberately: they are the whole point of
a classification report ("why did my socket become a light?"), and the user
chooses when to share the file.  Only the installation's identity -- host,
credentials, unit name and id -- is redacted.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_NUMBER_BASE
from .coordinator import Is3ConfigEntry, Is3Coordinator
from .export import (
    Is3Entry,
    detector_binary_class,
    is_binary,
    is_controller_internal,
    is_counter,
    is_dimmable,
    is_press_button,
    is_readable,
    is_rf_button,
    is_switchable,
    platform_of,
)

REDACTED = "**REDACTED**"

# Config keys that identify the installation or its owner.
_REDACT_CONFIG = frozenset({CONF_HOST, CONF_PASSWORD, CONF_USERNAME})


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: Is3ConfigEntry
) -> dict[str, Any]:
    """Return a redacted snapshot of one configured unit."""
    return build_diagnostics(entry, entry.runtime_data)


def build_diagnostics(
    entry: Is3ConfigEntry, coordinator: Is3Coordinator
) -> dict[str, Any]:
    """Assemble the diagnostics payload.  Kept sync so it is easy to test."""
    export = coordinator.data.export
    values = coordinator.data.values
    header = export.header

    platforms = Counter((platform_of(e) or "none") for e in export.entries)

    return {
        "note": (
            "Host, credentials and the unit's name/id are redacted. Entry names "
            "and hardware ids are kept so classification can be diagnosed."
        ),
        "config": {
            key: (REDACTED if key in _REDACT_CONFIG else value)
            for key, value in entry.data.items()
        },
        "capabilities": {
            "reads_supported": coordinator.reads_supported,
            "connected": coordinator.client.connected,
            "delimiter": repr(coordinator.client.delimiter),
            "number_base": entry.data.get(CONF_NUMBER_BASE),
        },
        "header": {
            "version": header.version if header else None,
            "created": header.created if header else None,
            "idm3": header.idm3 if header else None,
            "name": REDACTED if header and header.name else None,
            "unit_id": REDACTED if header and header.unit_id else None,
        },
        "summary": {
            "entry_count": len(export.entries),
            "values_reported": sum(1 for e in export.entries if e.address in values),
            "by_platform": dict(sorted(platforms.items())),
        },
        "entries": [_entry_diagnostics(e, values) for e in export.entries],
    }


def _entry_diagnostics(entry: Is3Entry, values: dict[int, int]) -> dict[str, Any]:
    """One entry, with its address, current value and how it classified."""
    flags = [
        name
        for name, matched in (
            ("dimmable", is_dimmable(entry)),
            ("switchable", is_switchable(entry)),
            ("binary", is_binary(entry)),
            ("readable", is_readable(entry)),
            ("press_button", is_press_button(entry)),
            ("rf_button", is_rf_button(entry)),
            ("counter", is_counter(entry)),
            ("controller_internal", is_controller_internal(entry)),
        )
        if matched
    ]
    if (detector := detector_binary_class(entry)) is not None:
        flags.append(f"detector:{detector}")

    return {
        "address": entry.address_hex,
        "name": entry.name,
        "hw_id": entry.hw_id,
        "unit": entry.unit,
        "labelled": entry.labelled,
        "space": f"0x{entry.space:02X}",
        "data_type": f"0x{entry.data_type:02X}",
        "platform": platform_of(entry),
        "value": values.get(entry.address),
        "export_value": entry.value,
        "flags": flags,
    }
