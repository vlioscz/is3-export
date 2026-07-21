"""Light platform for the IS3 Export integration.

Dimmers take a percentage.  The vendor's own sample scripts write values like
``writeValue('DA3-22M_OUT1_010cb2', 70)``, and abetka/InelsHA scales Home
Assistant's 0-255 brightness into the same 0-100 range, so that is the scale
used here.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api import Is3Error
from .coordinator import Is3ConfigEntry, Is3Coordinator
from .entity import Is3Entity
from .export import (
    DIMMER_MAX,
    DIMMER_MIN,
    PLATFORM_LIGHT,
    Is3Entry,
    find_covers,
    is_dimmable,
    platform_of,
)

HA_BRIGHTNESS_MAX = 255

# A relay named as a light has no levels; on is 1, as it is for any relay.
RELAY_ON = 1


def to_percent(brightness: int) -> int:
    """Convert Home Assistant's 0-255 brightness to the unit's 0-100."""
    percent = round(brightness * DIMMER_MAX / HA_BRIGHTNESS_MAX)
    return max(DIMMER_MIN, min(DIMMER_MAX, percent))


def to_brightness(percent: int) -> int:
    """Convert the unit's 0-100 back to Home Assistant's 0-255."""
    brightness = round(percent * HA_BRIGHTNESS_MAX / DIMMER_MAX)
    return max(0, min(HA_BRIGHTNESS_MAX, brightness))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Is3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a light for every dimmer, and for every relay named as a light."""
    coordinator = entry.runtime_data
    export = coordinator.data.export
    claimed = {address for cover in find_covers(export) for address in cover.addresses}

    async_add_entities(
        Is3Light(coordinator, item)
        for item in export.entries
        if platform_of(item) == PLATFORM_LIGHT and item.address not in claimed
    )


class Is3Light(Is3Entity, LightEntity, RestoreEntity):
    """A light on the central unit.

    Either a dimmer, which takes a percentage, or a plain relay the installer
    named as a light, which only knows on and off.
    """

    def __init__(self, coordinator: Is3Coordinator, entry: Is3Entry) -> None:
        """Seed the assumed brightness from the export file snapshot."""
        super().__init__(coordinator, entry)
        self._dimmable = is_dimmable(entry)
        self._assumed_percent: int | None = entry.value

        mode = ColorMode.BRIGHTNESS if self._dimmable else ColorMode.ONOFF
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}
        # The lamp and mirror icons are set by the base entity.

    @property
    def assumed_state(self) -> bool:
        """Whether the shown state is a guess rather than a reading."""
        return not self.coordinator.reads_supported

    async def async_added_to_hass(self) -> None:
        """Restore the pre-restart brightness when reads are unavailable."""
        await super().async_added_to_hass()
        if not self.assumed_state:
            return
        if (last := await self.async_get_last_state()) is None:
            return
        if last.state == "off":
            self._assumed_percent = 0
        elif (brightness := last.attributes.get(ATTR_BRIGHTNESS)) is not None:
            self._assumed_percent = to_percent(brightness)

    @property
    def _percent(self) -> int | None:
        """Current level on the unit's own 0-100 scale."""
        if self.coordinator.reads_supported:
            return self._value
        return self._assumed_percent

    @property
    def is_on(self) -> bool | None:
        """Return whether the dimmer is above zero."""
        percent = self._percent
        return None if percent is None else percent > DIMMER_MIN

    @property
    def brightness(self) -> int | None:
        """Return the brightness on Home Assistant's scale."""
        if not self._dimmable:
            return None
        percent = self._percent
        return None if percent is None else to_brightness(percent)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, at the requested brightness or at full."""
        if not self._dimmable:
            # A relay only knows on; 1 is on in either number base.
            await self._async_send(RELAY_ON)
            return

        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            await self._async_send(to_percent(brightness))
        else:
            await self._async_send(DIMMER_MAX)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._async_send(DIMMER_MIN)

    async def _async_send(self, percent: int) -> None:
        """Send the level; the coordinator shows it and confirms it took."""
        try:
            await self.coordinator.async_command(self.entry.address, percent)
        except Is3Error as err:
            raise HomeAssistantError(
                f"Cannot write {percent} to {self.entry.address_hex}: {err}"
            ) from err

        self._assumed_percent = percent
        self.async_write_ha_state()
