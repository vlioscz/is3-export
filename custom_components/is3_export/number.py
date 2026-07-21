"""Number platform for the IS3 Export integration.

System integers are the installer's own variables: a dimmer's remembered
level, a measured wind speed, an effect number.  The programme running on the
unit reads them and branches on them, so they are worth being able to set, not
just watch.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Is3Error
from .coordinator import Is3ConfigEntry
from .entity import Is3Entity
from .export import NUMBER_MAX, NUMBER_MIN, PLATFORM_NUMBER, platform_of


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Is3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a number for every system integer in the export."""
    coordinator = entry.runtime_data
    async_add_entities(
        Is3Number(coordinator, item)
        for item in coordinator.data.export.entries
        if platform_of(item) == PLATFORM_NUMBER
    )


class Is3Number(Is3Entity, NumberEntity):
    """A system integer on the central unit."""

    _attr_native_min_value = NUMBER_MIN
    _attr_native_max_value = NUMBER_MAX
    _attr_native_step = 1
    # A slider across the whole 32-bit range would be useless, so it is typed.
    _attr_mode = NumberMode.BOX

    @property
    def native_value(self) -> float | None:
        """Return the current value, read as signed."""
        value = self._value
        if value is None:
            return None
        # The unit reports 32-bit two's complement.
        return value - 0x100000000 if value > 0x7FFFFFFF else value

    @property
    def available(self) -> bool:
        """Unavailable until a value is known."""
        return super().available and self._value is not None

    async def async_set_native_value(self, value: float) -> None:
        """Write a new value."""
        wanted = int(value)
        try:
            await self.coordinator.async_command(self.entry.address, wanted)
        except Is3Error as err:
            raise HomeAssistantError(
                f"Cannot write {wanted} to {self.entry.address_hex}: {err}"
            ) from err

        self.async_write_ha_state()
