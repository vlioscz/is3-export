"""Switch platform for the IS3 Export integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api import Is3Error
from .coordinator import Is3ConfigEntry, Is3Coordinator
from .entity import Is3Entity
from .export import PLATFORM_SWITCH, Is3Entry, find_covers, platform_of

ON_VALUE = 1
OFF_VALUE = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Is3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a switch for every on/off output that is not part of a blind."""
    coordinator = entry.runtime_data
    export = coordinator.data.export

    # Blind addresses belong to the cover platform; exposing them here as well
    # would offer two ways to drive the same motor.
    claimed = {
        address for cover in find_covers(export) for address in cover.addresses
    }

    async_add_entities(
        Is3Switch(coordinator, item)
        for item in export.entries
        if platform_of(item) == PLATFORM_SWITCH and item.address not in claimed
    )


class Is3Switch(Is3Entity, SwitchEntity, RestoreEntity):
    """An output on the central unit, driven by SET commands.

    When the unit answers reads the state is polled.  When it does not, the
    switch reports the last command sent and says so via ``assumed_state``.
    """

    def __init__(self, coordinator: Is3Coordinator, entry: Is3Entry) -> None:
        """Seed the assumed state from the export file snapshot."""
        super().__init__(coordinator, entry)
        self._assumed: bool | None = (
            bool(entry.value) if entry.value is not None else None
        )

    @property
    def assumed_state(self) -> bool:
        """Whether the shown state is a guess rather than a reading."""
        return not self.coordinator.reads_supported

    async def async_added_to_hass(self) -> None:
        """Restore the pre-restart state when there is nothing to read back."""
        await super().async_added_to_hass()
        if self.assumed_state and (last := await self.async_get_last_state()):
            self._assumed = last.state == "on"

    @property
    def is_on(self) -> bool | None:
        """Return the polled state, or the last commanded one."""
        if self.coordinator.reads_supported:
            value = self._value
            return None if value is None else bool(value)
        return self._assumed

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the output on."""
        await self._async_send(ON_VALUE)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the output off."""
        await self._async_send(OFF_VALUE)

    async def _async_send(self, value: int) -> None:
        """Send the command; the coordinator shows it and confirms it took."""
        try:
            await self.coordinator.async_command(self.entry.address, value)
        except Is3Error as err:
            raise HomeAssistantError(
                f"Cannot write {value} to {self.entry.address_hex}: {err}"
            ) from err

        self._assumed = bool(value)
        self.async_write_ha_state()
