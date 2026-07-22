"""Climate platform for the IS3 Export integration.

Each heating controller becomes one climate zone.  The channels are grouped in
:mod:`.export`; how they map onto a thermostat comes from the Connection
Server's own zone pairings:

* current temperature is ``Actual-Therm-AOUT``;
* the setpoint in force is ``Required-Therm-AOUT`` (heat) or
  ``Required-Cool-Therm-AOUT`` (cool);
* the preset is ``Control-Manual-IN`` -- 0 Schedule, 1-4 Preset 1-4, 7 Manual;
* the zone is turned off and on with ``Control-IN`` -- 0 off, 1 on;
* heating and cooling are chosen with ``Control-HC-IN`` -- 0 heat, 1 cool;
* setting a temperature switches to Manual and writes ``Manual-Therm-AIN`` when
  heating, or ``Manual-Cool-Therm-AIN`` when cooling.

Both ``Control-HC-IN`` (the demand outputs flip with it) and the plans behind
the plan select were confirmed on a live unit.  Temperatures are scaled by 100,
as everywhere on iNELS.

Setting the temperature has one subtlety, found the hard way.  Writing the
setpoint immediately after switching to Manual corrupts it -- the value lands
below frost protection (about 0.1 degrees), which drops the effective setpoint
and the heating relay, so the zone stops heating.  So the switch to Manual is
given time to settle before the setpoint is written, and then the write is
confirmed against ``Required-Therm-AOUT`` and repeated until it takes.  Both the
settle and the read-back were verified on a live unit.
"""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Is3Error
from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import Is3ConfigEntry, Is3Coordinator
from .export import (
    CONTROLLER_PRESETS,
    PRESET_MANUAL,
    PRESET_VALUES,
    Is3Controller,
    find_controllers,
)

TEMP_SCALE = 100
MIN_TEMP = 5.0
MAX_TEMP = 35.0
TEMP_STEP = 0.5

# Time for a switch to Manual to settle before the setpoint is written; writing
# too soon lands a corrupt value below frost protection.  Chosen from live
# testing, where 1.5s was reliable and back-to-back was not.
MANUAL_SETTLE = 1.5
# The setpoint write is confirmed against Required-Therm-AOUT and retried,
# because a single write is not always accepted.
SETPOINT_VERIFY_DELAY = 1.2
SETPOINT_ATTEMPTS = 4


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Is3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a climate entity for every heating controller in the export."""
    coordinator = entry.runtime_data
    async_add_entities(
        Is3Climate(coordinator, controller)
        for controller in find_controllers(coordinator.data.export)
    )


class Is3Climate(CoordinatorEntity[Is3Coordinator], ClimateEntity):
    """A heating zone on the central unit."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = TEMP_STEP
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_preset_modes = list(CONTROLLER_PRESETS.values())
    _attr_supported_features = (
        ClimateEntityFeature.PRESET_MODE | ClimateEntityFeature.TARGET_TEMPERATURE
    )

    def __init__(self, coordinator: Is3Coordinator, controller: Is3Controller) -> None:
        """Bind the entity to one controller."""
        super().__init__(coordinator)
        self.controller = controller
        config_entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{config_entry_id}_{controller.unique_id}"
        self._attr_name = controller.name.replace("_", " ")

        # Cooling is offered only where the zone actually has a cooling output
        # configured -- every zone carries the cool channels, so their presence
        # is not enough; the controller's root marks the real capability.
        modes = [HVACMode.HEAT]
        if controller.has_cooling and controller.control_hc is not None:
            modes.append(HVACMode.COOL)
        # A zone can be turned off only if it exposes the Control-IN switch.
        if controller.control_on is not None:
            modes.insert(0, HVACMode.OFF)
            self._attr_supported_features |= ClimateEntityFeature.TURN_OFF
            self._attr_supported_features |= ClimateEntityFeature.TURN_ON
        self._attr_hvac_modes = modes

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry_id)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=coordinator.config_entry.title,
        )

    async def async_added_to_hass(self) -> None:
        """Wake this entity whenever any of its channels reports a change."""
        await super().async_added_to_hass()
        for address in self.controller.read_addresses:
            self.async_on_remove(
                self.coordinator.async_add_address_listener(
                    address, self.async_write_ha_state
                )
            )

    def _temperature(self, address: int) -> float | None:
        """A temperature channel, on its real scale, signed."""
        value = self.coordinator.values.get(address)
        if value is None:
            return None
        if value > 0x7FFFFFFF:
            value -= 0x100000000
        return value / TEMP_SCALE

    @property
    def current_temperature(self) -> float | None:
        """The measured temperature in the zone."""
        return self._temperature(self.controller.actual)

    @property
    def target_temperature(self) -> float | None:
        """The setpoint in force for the active mode: heat, or its own cool one."""
        if self._is_cool and self.controller.cool_required is not None:
            return self._temperature(self.controller.cool_required)
        return self._temperature(self.controller.required)

    @property
    def _is_off(self) -> bool:
        """Whether the zone is switched off via Control-IN."""
        if self.controller.control_on is None:
            return False
        return self.coordinator.values.get(self.controller.control_on) == 0

    @property
    def _is_cool(self) -> bool:
        """Whether the zone is switched to cooling via Control-HC-IN."""
        if self.controller.control_hc is None:
            return False
        return self.coordinator.values.get(self.controller.control_hc) == 1

    @property
    def hvac_mode(self) -> HVACMode:
        """Off when switched off, otherwise the selected heat or cool mode."""
        if self._is_off:
            return HVACMode.OFF
        return HVACMode.COOL if self._is_cool else HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction | None:
        """Whether the zone is heating, cooling, idle or off right now."""
        if self._is_off:
            return HVACAction.OFF
        if self._is_cool:
            if self.controller.cool_demand is not None and self.coordinator.values.get(
                self.controller.cool_demand
            ):
                return HVACAction.COOLING
            return HVACAction.IDLE
        if self.coordinator.values.get(self.controller.heat_demand):
            return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def preset_mode(self) -> str | None:
        """The active preset, read back from the controller."""
        return CONTROLLER_PRESETS.get(
            self.coordinator.values.get(self.controller.preset_select)
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Select a preset by name."""
        await self._async_write(self.controller.preset_select, PRESET_VALUES[preset_mode])

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a target temperature.

        Switch to Manual, let it settle, then write the setpoint and confirm it
        landed -- writing too soon after the switch corrupts the value, so the
        write is verified against Required-Therm-AOUT and repeated if needed.
        """
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        raw = round(temperature * TEMP_SCALE)
        client = self.coordinator.client

        # Cooling has its own manual setpoint and its own setpoint-in-force; when
        # the zone is in cooling, write and verify those instead of the heat ones.
        cooling = self._is_cool
        manual_addr = (
            self.controller.cool_manual
            if cooling and self.controller.cool_manual is not None
            else self.controller.manual
        )
        required_addr = (
            self.controller.cool_required
            if cooling and self.controller.cool_required is not None
            else self.controller.required
        )
        preset_hex = f"0x{self.controller.preset_select:08X}"
        manual_hex = f"0x{manual_addr:08X}"
        required_hex = f"0x{required_addr:08X}"

        try:
            if self.coordinator.values.get(self.controller.preset_select) != PRESET_MANUAL:
                await client.async_set(preset_hex, PRESET_MANUAL)
                await asyncio.sleep(MANUAL_SETTLE)

            for _ in range(SETPOINT_ATTEMPTS):
                await client.async_set(manual_hex, raw)
                await asyncio.sleep(SETPOINT_VERIFY_DELAY)
                if await client.async_get(required_hex) == raw:
                    break
            else:
                raise HomeAssistantError(
                    f"Setpoint {temperature} °C did not take on {self._attr_name}"
                )
        except Is3Error as err:
            raise HomeAssistantError(
                f"Cannot set {temperature} °C on {self._attr_name}: {err}"
            ) from err

        # Only now that it is confirmed, reflect it.
        self.coordinator.async_note_write(self.controller.preset_select, PRESET_MANUAL)
        self.coordinator.async_note_write(required_addr, raw)
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Turn the zone off, or switch it on into heating or cooling."""
        if hvac_mode == HVACMode.OFF:
            if self.controller.control_on is not None:
                await self._async_write(self.controller.control_on, 0)
            return

        # Any heat/cool mode implies the zone is on; turn it on if it was off.
        if self.controller.control_on is not None and self._is_off:
            await self._async_write(self.controller.control_on, 1)
        # Choose heating or cooling where the zone supports the switch.
        if self.controller.control_hc is not None:
            await self._async_write(
                self.controller.control_hc, 1 if hvac_mode == HVACMode.COOL else 0
            )

    async def _async_write(self, address: int, value: int) -> None:
        """Write one channel and reflect it at once."""
        address_hex = f"0x{address:08X}"
        try:
            await self.coordinator.client.async_set(address_hex, value)
        except Is3Error as err:
            raise HomeAssistantError(
                f"Cannot write {value} to {address_hex}: {err}"
            ) from err
        self.coordinator.async_note_write(address, value)
        self.async_write_ha_state()
