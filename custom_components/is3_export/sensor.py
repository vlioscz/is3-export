"""Sensor platform for the IS3 Export integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfTemperature,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Is3ConfigEntry, Is3Coordinator
from .protocol import UNIT_STATES
from .entity import Is3Entity
from .export import (
    PLATFORM_SENSOR,
    Is3Entry,
    effective_unit,
    is_counter,
    is_illuminance,
    platform_of,
    value_scale,
)

# Units as written in the export file.  The address does not identify the
# quantity -- one address class covers both temperature and humidity -- so the
# device class is derived from the unit instead.
UNITS: dict[str, tuple[str, SensorDeviceClass | None]] = {
    "%": (PERCENTAGE, SensorDeviceClass.HUMIDITY),
    "°C": (UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
    "mV": (UnitOfElectricPotential.MILLIVOLT, SensorDeviceClass.VOLTAGE),
}

# A meter's unit, when the export carries one, decides its device class -- which
# is what places it in the Energy dashboard (that also wants a total state class,
# which every counter already has).  Matched case-insensitively against the units
# the export could plausibly write; a counter with no unit, or an unrecognised
# one such as a bare pulse count, is left a plain increasing total, exactly as
# before -- nothing is guessed.
_ENERGY_UNITS: dict[str, str] = {
    "wh": UnitOfEnergy.WATT_HOUR,
    "kwh": UnitOfEnergy.KILO_WATT_HOUR,
    "mwh": UnitOfEnergy.MEGA_WATT_HOUR,
    "gj": UnitOfEnergy.GIGA_JOULE,
}
_VOLUME_UNITS: dict[str, str] = {
    "m3": UnitOfVolume.CUBIC_METERS,
    "m³": UnitOfVolume.CUBIC_METERS,
    "l": UnitOfVolume.LITERS,
    "ft3": UnitOfVolume.CUBIC_FEET,
    "ft³": UnitOfVolume.CUBIC_FEET,
}
# A cubic metre fits both water and gas; the name is what tells them apart.
_GAS_TOKENS = ("plyn", "gas")


def counter_metric(entry: Is3Entry) -> tuple[str, SensorDeviceClass] | None:
    """The unit and device class for a meter, or None to leave it a plain total.

    Only a recognised energy or volume unit yields a device class; without one
    the meter still records a long-term total, it just does not claim to be
    energy, water or gas in the Energy dashboard.
    """
    if not is_counter(entry):
        return None
    unit = effective_unit(entry)
    if unit is None:
        return None

    key = unit.strip().lower()
    if key in _ENERGY_UNITS:
        return _ENERGY_UNITS[key], SensorDeviceClass.ENERGY
    if key in _VOLUME_UNITS:
        identity = f"{entry.name} {entry.hw_id or ''}".lower()
        gas = any(token in identity for token in _GAS_TOKENS)
        return _VOLUME_UNITS[key], SensorDeviceClass.GAS if gas else SensorDeviceClass.WATER
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Is3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a sensor for every analog reading, plus the unit's own status."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(
                Is3Sensor(coordinator, item)
                for item in coordinator.data.export.entries
                if platform_of(item) == PLATFORM_SENSOR
            ),
            Is3UnitStatusSensor(coordinator),
        ]
    )


class Is3UnitStatusSensor(CoordinatorEntity[Is3Coordinator], SensorEntity):
    """How the central unit says it is running.

    A unit that has been stopped still answers on the network and still holds
    the last values it read, so from Home Assistant everything looks normal
    while nothing in the building responds.  This is the one place that says
    so.  Its clock comes along as an attribute, because the unit runs its own
    heating schedules off it -- a unit an hour out does the right thing at the
    wrong time, and there is otherwise no way to see that from here.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "unit_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [*UNIT_STATES.values(), "unknown"]

    def __init__(self, coordinator: Is3Coordinator) -> None:
        """Attach the sensor to the central unit's own device."""
        super().__init__(coordinator)
        config_entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{config_entry_id}_unit_status"
        self._attr_device_info = {"identifiers": {(DOMAIN, config_entry_id)}}

    async def async_added_to_hass(self) -> None:
        """Wake when the unit's own status changes.

        The coordinator does not wake entities wholesale -- every other entity
        follows its own address -- so this one subscribes to the status
        specifically.
        """
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_unit_state_listener(self.async_write_ha_state)
        )

    @property
    def native_value(self) -> str | None:
        """The run state, or None until the unit has been asked."""
        state = self.coordinator.unit_state
        return state.state if state is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """The unit's own clock, and how its values are getting here.

        Whether the unit pushes its changes decides whether a light switched at
        the wall shows up at once or on the next sweep, and it is the first
        thing worth knowing about an installation that feels slow.  Having it
        only in the downloadable diagnostics meant nobody found it.
        """
        attributes = {
            "value_source": (
                "pushed by the unit"
                if getattr(self.coordinator.client, "events_started", True)
                else "periodic refresh (this unit does not push changes)"
            ),
            "refresh_interval": str(self.coordinator.update_interval),
        }
        state = self.coordinator.unit_state
        if state is not None and state.clock is not None:
            attributes["unit_clock"] = state.clock
        return attributes


class Is3Sensor(Is3Entity, SensorEntity):
    """An analog reading from the central unit."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: Is3Coordinator, entry: Is3Entry) -> None:
        """Derive the unit and device class from the export file."""
        super().__init__(coordinator, entry)
        self._scale = value_scale(entry)
        if is_counter(entry):
            # Meters only climb, so they back long-term statistics; a recognised
            # unit also gives them a device class and a place in the Energy
            # dashboard.  An unrecognised or absent unit is passed through as-is.
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            if (metric := counter_metric(entry)) is not None:
                self._attr_native_unit_of_measurement, self._attr_device_class = metric
            elif (unit := effective_unit(entry)) is not None:
                self._attr_native_unit_of_measurement = unit
        elif is_illuminance(entry):
            # A light-intensity input reads lux; without this it would be a bare
            # number, since the export gives it no unit.
            self._attr_device_class = SensorDeviceClass.ILLUMINANCE
            self._attr_native_unit_of_measurement = LIGHT_LUX
        elif (reading_unit := effective_unit(entry)) is not None:
            unit, device_class = UNITS.get(reading_unit, (reading_unit, None))
            self._attr_native_unit_of_measurement = unit
            self._attr_device_class = device_class
        if self._scale > 1:
            self._attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | int | None:
        """Return the most recent value, on its real scale.

        Readings arrive multiplied: a thermostat channel reporting 2550 is at
        25.50 degrees.
        """
        value = self._value
        if value is None:
            return None
        # Values are two's complement, so sub-zero temperatures wrap.
        if value > 0x7FFFFFFF:
            value -= 0x100000000
        return value / self._scale if self._scale > 1 else value

    @property
    def available(self) -> bool:
        """Unavailable until a value is known."""
        return super().available and self._value is not None
