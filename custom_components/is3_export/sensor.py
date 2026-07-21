"""Sensor platform for the IS3 Export integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfElectricPotential, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import Is3ConfigEntry, Is3Coordinator
from .entity import Is3Entity
from .export import (
    PLATFORM_SENSOR,
    Is3Entry,
    effective_unit,
    is_counter,
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Is3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a sensor for every analog reading in the export file."""
    coordinator = entry.runtime_data
    async_add_entities(
        Is3Sensor(coordinator, item)
        for item in coordinator.data.export.entries
        if platform_of(item) == PLATFORM_SENSOR
    )


class Is3Sensor(Is3Entity, SensorEntity):
    """An analog reading from the central unit."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: Is3Coordinator, entry: Is3Entry) -> None:
        """Derive the unit and device class from the export file."""
        super().__init__(coordinator, entry)
        self._scale = value_scale(entry)
        if is_counter(entry):
            # Meter readings only climb, so they can back long-term statistics.
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        if (reading_unit := effective_unit(entry)) is not None:
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
