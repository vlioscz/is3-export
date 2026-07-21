"""Select platform for the IS3 Export integration.

One select per heating zone that has a Control-Plan-IN channel: it picks the
plan the zone follows -- normal, vacation or public holiday.  All three were
verified writable on a live unit (0, 64, 128).  Public holiday must be set up in
the unit as a daily programme; where a zone has no such programme, selecting it
simply does not take -- the read-back then restores the shown plan.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Is3Error
from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import Is3ConfigEntry, Is3Coordinator
from .export import PLAN_OPTIONS, PLAN_VALUES, Is3Controller, find_controllers


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Is3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a plan select for every zone that can switch plans."""
    coordinator = entry.runtime_data
    async_add_entities(
        Is3PlanSelect(coordinator, controller)
        for controller in find_controllers(coordinator.data.export)
        if controller.plan_select is not None
    )


class Is3PlanSelect(CoordinatorEntity[Is3Coordinator], SelectEntity):
    """The plan a heating zone follows."""

    _attr_has_entity_name = True
    _attr_options = list(PLAN_OPTIONS.values())

    def __init__(self, coordinator: Is3Coordinator, controller: Is3Controller) -> None:
        """Bind the select to one controller's plan channel."""
        super().__init__(coordinator)
        self.controller = controller
        assert controller.plan_select is not None
        self._address = controller.plan_select
        config_entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{config_entry_id}_plan_{controller.serial.lower()}"
        self._attr_name = f"{controller.name.replace('_', ' ')} plan"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry_id)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=coordinator.config_entry.title,
        )

    async def async_added_to_hass(self) -> None:
        """Refresh when the plan changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_address_listener(
                self._address, self.async_write_ha_state
            )
        )

    @property
    def current_option(self) -> str | None:
        """The plan now in force."""
        return PLAN_OPTIONS.get(self.coordinator.values.get(self._address))

    async def async_select_option(self, option: str) -> None:
        """Switch to a plan; the coordinator shows it and confirms it took.

        A plan that is not configured on the zone will not hold, so the write is
        read back and the shown plan corrected instead of left wrong.
        """
        value = PLAN_VALUES[option]
        try:
            await self.coordinator.async_command(self._address, value)
        except Is3Error as err:
            raise HomeAssistantError(
                f"Cannot write {value} to 0x{self._address:08X}: {err}"
            ) from err
        self.async_write_ha_state()
