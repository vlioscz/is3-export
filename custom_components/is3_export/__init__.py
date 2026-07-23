"""The IS3 Export integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .api import Is3Client, Is3ConnectionError
from .export import Is3Export, expected_entities
from .const import (
    BASE_HEX,
    CONF_DELIMITER,
    CONF_EXPORT_FILE,
    CONF_NUMBER_BASE,
    DEFAULT_HTTP_PORT,
    DEFAULT_PORT,
    DELIMITER_SPACE,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .coordinator import Is3ConfigEntry, Is3Coordinator
from .issues import async_clear_issues, async_update_reads_issue

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.EVENT,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: Is3ConfigEntry) -> bool:
    """Set up IS3 Export from a config entry."""
    coordinator: Is3Coordinator | None = None

    def handle_event(address: int, value: int) -> None:
        """Forward a pushed value to the coordinator."""
        if coordinator is not None:
            coordinator.handle_event(address, value)

    def handle_reconnect() -> None:
        """Re-read state after a dropped connection came back."""
        if coordinator is not None:
            hass.async_create_task(coordinator.async_request_refresh())

    client = Is3Client(
        host=entry.data[CONF_HOST],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
        delimiter=entry.data.get(CONF_DELIMITER, DELIMITER_SPACE),
        number_base=entry.data.get(CONF_NUMBER_BASE, BASE_HEX),
        on_event=handle_event,
        on_reconnect=handle_reconnect,
    )
    # An empty path means the export is fetched from the unit over HTTP.
    configured_path = entry.data.get(CONF_EXPORT_FILE, "").strip()
    coordinator = Is3Coordinator(
        hass,
        entry,
        client,
        Path(configured_path) if configured_path else None,
        host=entry.data[CONF_HOST],
        # The export is fetched over plain HTTP on port 80, unauthenticated.
        http_port=DEFAULT_HTTP_PORT,
    )

    try:
        await client.async_connect()
    except Is3ConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.async_on_unload(client.async_close)

    await coordinator.async_detect_capabilities()
    # A delimiter or number base that does not match the unit leaves reads
    # unanswered; raise a repair card so it is not just a line in the log.
    async_update_reads_issue(
        hass,
        entry.entry_id,
        reads_supported=coordinator.reads_supported,
        delimiter=entry.data.get(CONF_DELIMITER, DELIMITER_SPACE),
        number_base=entry.data.get(CONF_NUMBER_BASE, BASE_HEX),
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    _register_unit_device(hass, entry)
    _prune_orphan_entities(hass, entry, coordinator.data.export)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


def _register_unit_device(hass: HomeAssistant, entry: Is3ConfigEntry) -> None:
    """Register the central unit as a device.

    Module devices -- wall switches, relay boards -- point at it as their parent,
    so it is created up front rather than left to whichever entity happens to be
    the first to mention it.
    """
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=MANUFACTURER,
        model=MODEL,
        name=entry.title,
    )


def _prune_orphan_entities(
    hass: HomeAssistant, entry: Is3ConfigEntry, export: Is3Export
) -> None:
    """Remove entities this integration no longer produces.

    Refining the classification moves outputs between platforms, and Home
    Assistant keeps the entity from the old platform as unavailable rather than
    removing it -- so a system integer that became a number left its sensor
    behind. Anything in the registry for this entry that the current export
    would not create again is dropped.
    """
    registry = er.async_get(hass)
    expected = expected_entities(export, entry.entry_id)

    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (registered.domain, registered.unique_id) not in expected:
            registry.async_remove(registered.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: Is3ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: Is3ConfigEntry) -> None:
    """Reload when the options change, so a new delimiter takes effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: Is3ConfigEntry) -> None:
    """Clear this unit's repair cards when it is removed."""
    async_clear_issues(hass, entry.entry_id)
