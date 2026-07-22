"""Cover platform for the IS3 Export integration.

Blinds come from one of two places, described in :mod:`.export`, and the two
differ in how much this integration has to do.

This split is not an invention of this integration.  The old Connection Server
offered the same choice: "dummy shutters", which reached only for the relays,
or the shutter module, which reached for the bits and could tilt.


**Program bits.**  The installer's blind program owns the physical contacts: it
interlocks the directions, handles reversal and knows the end positions.  A bit
is a request to that program, so driving a blind is one write and nothing here
has to reason about relays.  This is the preferred source, and it is also the
one that offers stop and tilt.

**A relay pair on a JA3 driver.**  Here the contacts are driven directly: 1 runs
the motor, 0 stops it, and the two directions are interlocked in hardware, so
the opposite direction has to be released before reversing.  Used only when the
export contains no blind program.

Neither reports a position, so the covers carry an assumed state.  What they do
report is direction: the relays hold their value while the motor runs, and the
program bits are pulses that the unit clears when the blind arrives.

A live test on a program-bit blind confirmed the model: driving up or down
holds that direction until the program's configured run time elapses (about a
minute for a normal window) or the stop bit is pulsed, either of which clears
the direction so the blind can be driven again, and a tilt is a brief pulse the
program clears itself.  So even a blind with no end sensor stops on its own; the
integration does not need to know or track the run time.
"""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Is3Error
from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import Is3ConfigEntry, Is3Coordinator
from .export import Is3Cover, find_covers

ON = 1
OFF = 0

# A program bit is a command, not a state: writing 1 asks the blind to move.
PULSE = 1

# How long to wait after releasing one direction before engaging the other.
# Chosen rather than measured: the two relays are interlocked in hardware, and
# each command travels on its own connection, so the release needs to have
# landed before the opposite direction is asked for.
REVERSE_DELAY = 0.5


def needs_release_first(source: str, other_value: int | None) -> bool:
    """Whether the opposite direction has to be released before driving.

    Only relay pairs interlock; program bits are commands to a blind program
    that sorts the motor out itself.  A relay that is not running needs no
    release, so opening from standstill stays a single write.
    """
    return source == "relay" and bool(other_value)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Is3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a cover for every blind found in the export."""
    coordinator = entry.runtime_data
    async_add_entities(
        Is3CoverEntity(coordinator, cover)
        for cover in find_covers(coordinator.data.export)
    )


class Is3CoverEntity(CoordinatorEntity[Is3Coordinator], CoverEntity):
    """A blind driven either by program bits or by a relay pair."""

    _attr_has_entity_name = True
    _attr_assumed_state = True

    def __init__(self, coordinator: Is3Coordinator, cover: Is3Cover) -> None:
        """Advertise only the actions this blind's wiring supports."""
        super().__init__(coordinator)
        self.cover = cover

        config_entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{config_entry_id}_{cover.unique_id}"
        self._attr_name = cover.name.replace("_", " ")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, config_entry_id)},
            "manufacturer": MANUFACTURER,
            "model": MODEL,
            "name": coordinator.config_entry.title,
        }

        features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
        if cover.stop is not None or cover.source == "relay":
            # A relay pair stops by releasing both directions.
            features |= CoverEntityFeature.STOP
        if cover.has_tilt:
            features |= CoverEntityFeature.OPEN_TILT | CoverEntityFeature.CLOSE_TILT
        self._attr_supported_features = features

    async def async_added_to_hass(self) -> None:
        """Wake this blind when either direction it shows changes.

        The state a cover displays is the two direction channels (is_opening /
        is_closing), so it subscribes to those, like every other entity -- rather
        than leaning on the coordinator's blanket refresh, which is suppressed.
        """
        await super().async_added_to_hass()
        for address in (self.cover.open.address, self.cover.close.address):
            self.async_on_remove(
                self.coordinator.async_add_address_listener(
                    address, self.async_write_ha_state
                )
            )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose the addresses behind the blind, for matching to the export."""
        attributes = {
            "open_address": self.cover.open.address_hex,
            "close_address": self.cover.close.address_hex,
            "source": self.cover.source,
        }
        if self.cover.stop is not None:
            attributes["stop_address"] = self.cover.stop.address_hex
        return attributes

    def _value(self, address: int | None) -> int | None:
        """The last known value of one of this blind's addresses."""
        if address is None:
            return None
        return self.coordinator.values.get(address)

    @property
    def is_closed(self) -> bool | None:
        """Unknown: the unit reports no position for a blind."""
        return None

    @property
    def is_opening(self) -> bool:
        """Whether the up direction is currently engaged."""
        return bool(self._value(self.cover.open.address))

    @property
    def is_closing(self) -> bool:
        """Whether the down direction is currently engaged."""
        return bool(self._value(self.cover.close.address))

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Raise the blind."""
        await self._async_drive(self.cover.open, self.cover.close)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Lower the blind."""
        await self._async_drive(self.cover.close, self.cover.open)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the blind where it is."""
        if self.cover.stop is not None:
            await self._async_write(self.cover.stop, PULSE)
            return
        # No stop bit: releasing a relay stops that direction. Both are
        # released rather than only the one believed to be running, because a
        # missed event would leave the other stuck on.
        await self._async_write(self.cover.open, OFF)
        await self._async_write(self.cover.close, OFF)

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Angle the slats up."""
        if self.cover.tilt_open is not None:
            await self._async_write(self.cover.tilt_open, PULSE)

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Angle the slats down."""
        if self.cover.tilt_close is not None:
            await self._async_write(self.cover.tilt_close, PULSE)

    async def _async_drive(self, engage, release) -> None:
        """Start moving one way.

        On a relay pair, setting a direction to 1 runs the motor and 0 stops
        it.  The two directions are interlocked in hardware, so whether a
        command takes effect can depend on the other relay's state -- the
        opposite direction is therefore released first, and only when it is
        actually engaged, so an ordinary open from standstill stays a single
        write.
        """
        if not needs_release_first(self.cover.source, self._value(release.address)):
            await self._async_write(engage, ON)
            return

        await self._async_write(release, OFF)
        # Give the module a moment to drop the interlock before the opposite
        # direction arrives on a fresh connection. Reversing a blind motor
        # instantly is also hard on the mechanism.
        await asyncio.sleep(REVERSE_DELAY)
        await self._async_write(engage, ON)

    async def _async_write(self, entry, value: int) -> None:
        """Write one address and reflect it immediately."""
        try:
            await self.coordinator.client.async_set(entry.address_hex, value)
        except Is3Error as err:
            raise HomeAssistantError(
                f"Cannot write {value} to {entry.address_hex}: {err}"
            ) from err
        self.coordinator.async_note_write(entry.address, value)
        self.async_write_ha_state()
