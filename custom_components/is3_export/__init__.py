"""The IS3 Export integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

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
)
from .coordinator import Is3ConfigEntry, Is3Coordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
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
        # HTTP is fixed at port 80 and the unit has no username; only the
        # password is configurable.
        http_port=DEFAULT_HTTP_PORT,
        username=None,
        password=entry.data.get(CONF_PASSWORD) or None,
    )

    try:
        await client.async_connect()
    except Is3ConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.async_on_unload(client.async_close)

    await coordinator.async_detect_capabilities()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    _prune_orphan_entities(hass, entry, coordinator.data.export)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


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
