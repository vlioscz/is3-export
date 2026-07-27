"""Number platform for the IS3 Export integration.

System integers are the installer's own variables: a dimmer's remembered
level, a measured wind speed, an effect number.  The programme running on the
unit reads them and branches on them, so they are worth being able to set, not
just watch.

Relay blinds add a second kind: a per-blind *travel time*.  A relay blind has no
position sensor, so its cover times an auto-release and scales its position
estimate against this value.  It is configuration the installer tunes, not a
reading from the unit, so it is a config-category Number kept on the unit device.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Is3Error
from .const import (
    COVER_TRAVEL_TIME_MAX,
    COVER_TRAVEL_TIME_MIN,
    DEFAULT_COVER_TRAVEL_TIME,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .coordinator import Is3ConfigEntry, Is3Coordinator
from .entity import Is3Entity
from .export import (
    NUMBER_MAX,
    NUMBER_MIN,
    PLATFORM_NUMBER,
    Is3Cover,
    find_covers,
    platform_of,
)

# The blind sources whose covers are driven directly and so need a run time.
RELAY = "relay"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Is3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a number for every system integer, and a travel time per relay blind."""
    coordinator = entry.runtime_data
    export = coordinator.data.export

    entities: list[NumberEntity] = [
        Is3Number(coordinator, item)
        for item in export.entries
        if platform_of(item) == PLATFORM_NUMBER
    ]
    entities += [
        Is3CoverTravelTime(coordinator, cover)
        for cover in find_covers(export)
        if cover.source == RELAY
    ]
    async_add_entities(entities)


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


class Is3CoverTravelTime(CoordinatorEntity[Is3Coordinator], RestoreNumber):
    """The run time of one relay blind, in seconds.

    A relay blind has no position sensor, so its cover times an auto-release and
    scales its position estimate against this.  It is configuration, not a
    reading, so it is a config-category Number the installer sets per blind and
    which is remembered across restarts.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = COVER_TRAVEL_TIME_MIN
    _attr_native_max_value = COVER_TRAVEL_TIME_MAX
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-cog-outline"

    def __init__(self, coordinator: Is3Coordinator, cover: Is3Cover) -> None:
        """Bind the travel time to one blind, seeding the default until restored."""
        super().__init__(coordinator)
        self._cover = cover
        config_entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{config_entry_id}_{cover.unique_id}_travel_time"
        self._attr_name = f"{cover.name.replace('_', ' ')} travel time"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, config_entry_id)},
            "manufacturer": MANUFACTURER,
            "model": MODEL,
            "name": coordinator.config_entry.title,
        }
        coordinator.cover_travel_times.setdefault(
            cover.open.address, float(DEFAULT_COVER_TRAVEL_TIME)
        )

    @property
    def native_value(self) -> float:
        """The blind's current travel time, in seconds."""
        return self.coordinator.cover_travel_times.get(
            self._cover.open.address, float(DEFAULT_COVER_TRAVEL_TIME)
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last travel time the installer set."""
        await super().async_added_to_hass()
        data = await self.async_get_last_number_data()
        if data is not None and data.native_value is not None:
            self.coordinator.cover_travel_times[self._cover.open.address] = float(
                data.native_value
            )
            self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        """Record a new travel time; the cover reads it on its next move."""
        self.coordinator.cover_travel_times[self._cover.open.address] = float(value)
        self.async_write_ha_state()
