"""Fan platform for the IS3 Export integration.

An output the installer named for a fan -- ``Vent_koup``, ``VENT_WC_2np`` --
becomes a fan rather than a plain switch, so it reads as one everywhere: its own
card, and "turn on the bathroom fan" understood by a voice assistant.

It is a relay, so it has one speed.  A fan wired to a dimmer would be a
different thing, with a percentage, and is deliberately left as the light it is
already classified as rather than guessed at.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import Is3ConfigEntry, Is3Coordinator
from .entity import Is3Entity
from .errors import Is3Error
from .export import PLATFORM_FAN, Is3Entry, find_covers, platform_of

ON_VALUE = 1
OFF_VALUE = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Is3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a fan for every output named for one."""
    coordinator = entry.runtime_data
    export = coordinator.data.export

    # A blind's relays belong to the cover platform; nothing else may drive them.
    claimed = {
        address for cover in find_covers(export) for address in cover.addresses
    }

    async_add_entities(
        Is3Fan(coordinator, item)
        for item in export.entries
        if platform_of(item) == PLATFORM_FAN and item.address not in claimed
    )


class Is3Fan(Is3Entity, FanEntity, RestoreEntity):
    """A fan on a relay: on or off, no speed."""

    # The icon comes from the fan domain itself; the name-derived one would be
    # the same picture with less meaning behind it.
    _attr_icon = None

    def __init__(self, coordinator: Is3Coordinator, entry: Is3Entry) -> None:
        """Seed the assumed state from the export file snapshot."""
        super().__init__(coordinator, entry)
        self._attr_icon = None
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

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Run the fan.  A percentage is accepted and ignored: there is one speed."""
        await self._async_send(ON_VALUE)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the fan."""
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
