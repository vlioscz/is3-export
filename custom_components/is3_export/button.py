"""Button platform for the IS3 Export integration.

Outputs the installer named with an ``imp`` token are momentary impulses: they
are fired, not held.  A switch would show an on/off state that means nothing,
so these become buttons instead.

A press is a pulse: the bit goes to 1 and straight back to 0, so it rests at 0
and the next press is another clean 1.  See :meth:`Is3Button.async_press`.
"""

from __future__ import annotations

import asyncio

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Is3Error
from .coordinator import Is3ConfigEntry
from .entity import Is3Entity
from .export import PLATFORM_BUTTON, find_covers, platform_of

ON = 1
OFF = 0

# How long the bit is held at 1 before being released. Long enough for the
# unit's programme to sample the rising edge, short enough to feel instant.
PULSE_GAP = 0.3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Is3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a button for every impulse in the export."""
    coordinator = entry.runtime_data
    export = coordinator.data.export
    claimed = {address for cover in find_covers(export) for address in cover.addresses}

    async_add_entities(
        Is3Button(coordinator, item)
        for item in export.entries
        if platform_of(item) == PLATFORM_BUTTON and item.address not in claimed
    )


class Is3Button(Is3Entity, ButtonEntity):
    """A momentary impulse on the central unit."""

    async def async_press(self) -> None:
        """Fire the impulse as a pulse: 1, then straight back to 0.

        The unit's programme reacts to the bit rising, and it does not reset the
        bit itself.  Holding it at 1 would fire once and then do nothing, so the
        press sends 1, holds briefly, and sends 0.  The bit rests at 0, and the
        next press is another clean rising edge.
        """
        try:
            await self.coordinator.client.async_set(self.entry.address_hex, ON)
            await asyncio.sleep(PULSE_GAP)
            await self.coordinator.client.async_set(self.entry.address_hex, OFF)
        except Is3Error as err:
            raise HomeAssistantError(
                f"Cannot fire {self.entry.address_hex}: {err}"
            ) from err

        # Rests at 0, ready for the next press.
        self.coordinator.async_note_write(self.entry.address, OFF)
