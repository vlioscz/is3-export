"""Binary sensor platform for the IS3 Export integration.

Digital inputs: wall buttons, window detectors and the controller's own status
outputs. Not system bits -- those are the writable 0x0203 variables.
These are never written to.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import Is3ConfigEntry, Is3Coordinator
from .entity import Is3Entity
from .export import (
    ALERT,
    PLATFORM_BINARY_SENSOR,
    Is3Entry,
    is_battery_input,
    is_press_button,
    platform_of,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Is3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a binary sensor for every digital input in the export file.

    Buttons are left out: momentary presses are reported by the event platform,
    not as an on/off state that could stick on when a release is lost.
    """
    coordinator = entry.runtime_data
    async_add_entities(
        Is3BinarySensor(coordinator, item)
        for item in coordinator.data.export.entries
        if platform_of(item) == PLATFORM_BINARY_SENSOR and not is_press_button(item)
    )


class Is3BinarySensor(Is3Entity, BinarySensorEntity):
    """A digital input, or a fault flag raised by a module."""

    def __init__(self, coordinator: Is3Coordinator, entry: Is3Entry) -> None:
        """Mark fault flags and low-battery inputs as diagnostic sensors."""
        super().__init__(coordinator, entry)
        if is_battery_input(entry):
            self._attr_device_class = BinarySensorDeviceClass.BATTERY
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        elif (entry.space, entry.data_type) in ALERT:
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        """Return whether the input is active."""
        value = self._value
        return None if value is None else bool(value)

    @property
    def available(self) -> bool:
        """Unavailable until a value is known, so it cannot read as a false off."""
        return super().available and self._value is not None
