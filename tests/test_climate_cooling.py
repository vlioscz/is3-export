"""Heating/cooling behaviour of a climate zone.

The bedroom zone on the live unit gained a cooling output and its festive plan;
switching Control-HC-IN flipped the demand outputs, and cooling carries its own
setpoint channels.  These tests pin the entity logic that follows from that.
"""

from __future__ import annotations

import asyncio

from homeassistant.components.climate import HVACAction, HVACMode

import custom_components.is3_export.climate as climate_module
from custom_components.is3_export.climate import Is3Climate
from custom_components.is3_export.export import (
    PRESET_MANUAL,
    Is3Controller,
    _controller_has_cooling,
    parse_export,
)

# Bedroom channel addresses, as in the climate fixture.
ACTUAL = 0x01080025
REQUIRED = 0x01080026
MANUAL = 0x01120043
HEAT_DEMAND = 0x01010063
PRESET = 0x01110018
CONTROL_ON = 0x01110015
CONTROL_HC = 0x01110016
COOL_DEMAND = 0x01010064
COOL_REQUIRED = 0x01080027
COOL_MANUAL = 0x01120044


def _controller() -> Is3Controller:
    """A fully equipped zone: on/off, heat/cool, and both setpoints."""
    return Is3Controller(
        name="Zone",
        serial="0E0001",
        actual=ACTUAL,
        required=REQUIRED,
        manual=MANUAL,
        heat_demand=HEAT_DEMAND,
        preset_select=PRESET,
        control_on=CONTROL_ON,
        control_hc=CONTROL_HC,
        cool_demand=COOL_DEMAND,
        cool_required=COOL_REQUIRED,
        cool_manual=COOL_MANUAL,
        has_cooling=True,
    )


class _Client:
    def __init__(self) -> None:
        self.sets: list[tuple[str, int]] = []
        self.reads: dict[str, int] = {}

    async def async_set(self, address_hex: str, value: int) -> None:
        self.sets.append((address_hex, value))

    async def async_get(self, address_hex: str) -> int | None:
        return self.reads.get(address_hex)


class _Coord:
    def __init__(self, values: dict[int, int]) -> None:
        self.values = values
        self.client = _Client()
        self.notes: list[tuple[int, int]] = []

    def async_note_write(self, address: int, value: int) -> None:
        self.notes.append((address, value))


def _climate(values: dict[int, int], controller: Is3Controller | None = None) -> Is3Climate:
    entity = Is3Climate.__new__(Is3Climate)
    entity.controller = controller or _controller()
    entity.coordinator = _Coord(values)
    entity._attr_name = "Zone"
    entity.async_write_ha_state = lambda: None
    return entity


def test_hvac_mode_follows_the_heat_cool_switch() -> None:
    """Control-HC-IN decides heat vs cool; Control-IN can override to off."""
    heat = _climate({CONTROL_ON: 1, CONTROL_HC: 0})
    assert heat.hvac_mode == HVACMode.HEAT

    cool = _climate({CONTROL_ON: 1, CONTROL_HC: 1})
    assert cool.hvac_mode == HVACMode.COOL

    off = _climate({CONTROL_ON: 0, CONTROL_HC: 1})
    assert off.hvac_mode == HVACMode.OFF


def test_target_temperature_uses_the_active_mode_setpoint() -> None:
    """Heating reads Required-Therm; cooling reads its own Required-Cool-Therm."""
    values = {REQUIRED: 2000, COOL_REQUIRED: 2450}
    heat = _climate({**values, CONTROL_HC: 0})
    assert heat.target_temperature == 20.0

    cool = _climate({**values, CONTROL_HC: 1})
    assert cool.target_temperature == 24.5


def test_hvac_action_is_scoped_to_the_active_mode() -> None:
    """In cooling only the cool demand counts, and the reverse when heating."""
    cooling = _climate({CONTROL_ON: 1, CONTROL_HC: 1, COOL_DEMAND: 1, HEAT_DEMAND: 1})
    assert cooling.hvac_action == HVACAction.COOLING

    cool_idle = _climate({CONTROL_ON: 1, CONTROL_HC: 1, COOL_DEMAND: 0, HEAT_DEMAND: 1})
    assert cool_idle.hvac_action == HVACAction.IDLE

    heating = _climate({CONTROL_ON: 1, CONTROL_HC: 0, HEAT_DEMAND: 1, COOL_DEMAND: 1})
    assert heating.hvac_action == HVACAction.HEATING


class _FullCoord(_Coord):
    class _Entry:
        entry_id = "e1"
        title = "Unit"

    config_entry = _Entry()


def test_full_zone_offers_off_heat_and_cool() -> None:
    """A zone with both switches offers all three modes; off comes first."""
    coord = _FullCoord({})
    entity = Is3Climate(coord, _controller())
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]


def test_heat_only_zone_offers_just_heat() -> None:
    """Without the switches there is no off and no cool -- only heat."""
    coord = _FullCoord({})
    controller = Is3Controller(
        name="Heat only",
        serial="0E0002",
        actual=ACTUAL,
        required=REQUIRED,
        manual=MANUAL,
        heat_demand=HEAT_DEMAND,
        preset_select=PRESET,
    )
    entity = Is3Climate(coord, controller)
    assert entity.hvac_modes == [HVACMode.HEAT]


def test_cool_channels_without_a_cooling_output_offer_no_cool() -> None:
    """Every zone carries the cool channels; cool is offered only when the zone's
    root marks a cooling output (has_cooling), not merely because they exist."""
    coord = _FullCoord({})
    controller = _controller()
    controller.has_cooling = False  # cool channels present, but no cooling output
    entity = Is3Climate(coord, controller)
    assert HVACMode.COOL not in entity.hvac_modes
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.HEAT]


def test_cooling_capability_is_read_from_the_controller_root() -> None:
    """A heating-only root reads flags 0x05 with empty cool plan slots; a zone
    with a cooling output reads 0x3F with them filled (verified on the live unit)."""
    cool = parse_export(
        "Z Controller_0E0001 0x0003002A 0x00000006 0x3F "
        "0x05010006_0x05010006_0x05010004_0x05010004_0x05050001_0x05050001"
    ).entries[0]
    heat = parse_export(
        "Z Controller_0E0002 0x0003002B 0x00000006 0x05 "
        "0x05010005_0x00000000_0x05010004_0x00000000_0x00000000_0x00000000"
    ).entries[0]
    assert _controller_has_cooling(cool)
    assert not _controller_has_cooling(heat)


def test_setting_cool_mode_writes_the_switch() -> None:
    """Selecting cool writes Control-HC-IN = 1; heat writes 0; off writes Control-IN."""
    cool = _climate({CONTROL_ON: 1, CONTROL_HC: 0})
    asyncio.run(cool.async_set_hvac_mode(HVACMode.COOL))
    assert (f"0x{CONTROL_HC:08X}", 1) in cool.coordinator.client.sets

    heat = _climate({CONTROL_ON: 1, CONTROL_HC: 1})
    asyncio.run(heat.async_set_hvac_mode(HVACMode.HEAT))
    assert (f"0x{CONTROL_HC:08X}", 0) in heat.coordinator.client.sets

    off = _climate({CONTROL_ON: 1, CONTROL_HC: 0})
    asyncio.run(off.async_set_hvac_mode(HVACMode.OFF))
    assert off.coordinator.client.sets == [(f"0x{CONTROL_ON:08X}", 0)]


def test_selecting_a_mode_turns_a_stopped_zone_on() -> None:
    """A heat/cool selection on an off zone turns it on before switching."""
    entity = _climate({CONTROL_ON: 0, CONTROL_HC: 0})
    asyncio.run(entity.async_set_hvac_mode(HVACMode.COOL))
    assert entity.coordinator.client.sets == [
        (f"0x{CONTROL_ON:08X}", 1),
        (f"0x{CONTROL_HC:08X}", 1),
    ]


def test_setpoint_in_cooling_writes_the_cool_channels(monkeypatch) -> None:
    """Setting a temperature while cooling targets the cool manual and verifies
    against the cool setpoint, not the heat ones."""
    monkeypatch.setattr(climate_module, "MANUAL_SETTLE", 0)
    monkeypatch.setattr(climate_module, "SETPOINT_VERIFY_DELAY", 0)

    entity = _climate({CONTROL_HC: 1, PRESET: 0})
    # The unit reflects the write into the cool setpoint, so verification passes.
    entity.coordinator.client.reads[f"0x{COOL_REQUIRED:08X}"] = 2100

    from homeassistant.const import ATTR_TEMPERATURE

    asyncio.run(entity.async_set_temperature(**{ATTR_TEMPERATURE: 21.0}))

    sets = entity.coordinator.client.sets
    assert (f"0x{PRESET:08X}", PRESET_MANUAL) in sets, "must switch to Manual"
    assert (f"0x{COOL_MANUAL:08X}", 2100) in sets, "must write the cool manual setpoint"
    assert (f"0x{MANUAL:08X}", 2100) not in sets, "must not touch the heat setpoint"
    assert (COOL_REQUIRED, 2100) in entity.coordinator.notes