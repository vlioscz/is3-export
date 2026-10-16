"""A dimmer honours ``transition``, and the newest request for it wins."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_TRANSITION,
    LightEntityFeature,
)

from custom_components.is3_export.export import parse_export
from custom_components.is3_export.light import Is3Light

EXPORT = (
    "VERSION_01-03-03_IDM3_03-04-19_ID_ABC_NAME_Test\r\n"
    "Sv_obyv DA3-22M_OUT1_0D0002 0x01040001 0x00000000 %\r\n"
    "Sv_loznice SA3-012M_RE10_0D0001 0x0102000A 0x00000000\r\n"
)
DIMMER, RELAY = parse_export(EXPORT).entries


class _Coordinator:
    """Records what the light asked for; writes take a moment, as on a unit."""

    def __init__(self, level: int | None = 0) -> None:
        self.config_entry = SimpleNamespace(entry_id="abc", title="Unit")
        self.reads_supported = True
        self.values: dict[int, int] = {} if level is None else {DIMMER.address: level}
        self.fading_at: int | None = None
        self.commands: list[tuple[int, int]] = []
        self.fades: list[tuple[int, int, int, float]] = []

    def fade_level(self, address: int) -> int | None:
        return self.fading_at

    async def async_command(self, address: int, value: int) -> None:
        await asyncio.sleep(0.01)
        self.commands.append((address, value))
        self.values[address] = value

    async def async_fade(self, address, start, target, duration) -> None:
        await asyncio.sleep(0.01)
        self.fades.append((address, start, target, duration))
        self.values[address] = target


def _light(entry, coordinator: _Coordinator) -> Is3Light:
    light = Is3Light(coordinator, entry)
    light.async_write_ha_state = lambda: None
    return light


def test_a_dimmer_offers_transitions_and_a_relay_does_not() -> None:
    """Home Assistant strips ``transition`` from an entity that does not declare
    it, so the bit is what makes the feature reachable at all."""
    assert _light(DIMMER, _Coordinator()).supported_features & LightEntityFeature.TRANSITION
    assert not _light(RELAY, _Coordinator()).supported_features & LightEntityFeature.TRANSITION


def test_a_transition_fades_from_the_current_level() -> None:
    coordinator = _Coordinator(level=20)
    light = _light(DIMMER, coordinator)

    asyncio.run(light.async_turn_on(**{ATTR_BRIGHTNESS: 255, ATTR_TRANSITION: 3}))

    assert coordinator.fades == [(DIMMER.address, 20, 100, 3)]
    assert not coordinator.commands


def test_turning_off_with_a_transition_fades_down() -> None:
    coordinator = _Coordinator(level=60)
    light = _light(DIMMER, coordinator)

    asyncio.run(light.async_turn_off(**{ATTR_TRANSITION: 2}))

    assert coordinator.fades == [(DIMMER.address, 60, 0, 2)]


def test_a_new_fade_starts_from_where_the_last_one_got_to() -> None:
    """Mid-fade the light shows its target, but the output is somewhere on the
    way -- and that is where the next fade has to start."""
    coordinator = _Coordinator(level=100)  # showing the target
    coordinator.fading_at = 35  # actually here
    light = _light(DIMMER, coordinator)

    asyncio.run(light.async_turn_off(**{ATTR_TRANSITION: 1}))

    assert coordinator.fades == [(DIMMER.address, 35, 0, 1)]


def test_no_transition_is_still_a_single_write() -> None:
    coordinator = _Coordinator(level=0)
    light = _light(DIMMER, coordinator)

    asyncio.run(light.async_turn_on(**{ATTR_BRIGHTNESS: 128}))

    assert coordinator.commands == [(DIMMER.address, 50)]
    assert not coordinator.fades


def test_a_transition_to_where_it_already_is_is_just_a_write() -> None:
    coordinator = _Coordinator(level=100)
    light = _light(DIMMER, coordinator)

    asyncio.run(light.async_turn_on(**{ATTR_TRANSITION: 2}))

    assert coordinator.commands == [(DIMMER.address, 100)]
    assert not coordinator.fades


def test_a_level_unknown_yet_cannot_be_faded_from() -> None:
    """Nothing read yet means nowhere to start; the level is simply set."""
    coordinator = _Coordinator(level=None)
    light = _light(DIMMER, coordinator)

    asyncio.run(light.async_turn_on(**{ATTR_BRIGHTNESS: 255, ATTR_TRANSITION: 2}))

    assert coordinator.commands == [(DIMMER.address, 100)]


def test_a_relay_named_as_a_light_ignores_transitions() -> None:
    coordinator = _Coordinator()
    light = _light(RELAY, coordinator)

    asyncio.run(light.async_turn_on(**{ATTR_TRANSITION: 2}))

    assert coordinator.commands == [(RELAY.address, 1)]
    assert not coordinator.fades


def test_a_fade_the_unit_refuses_reaches_home_assistant_as_an_error() -> None:
    """The coordinator raises the integration's own error, which is not one
    Home Assistant knows; the light must turn it into one."""
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.is3_export.errors import Is3ConnectionError

    coordinator = _Coordinator(level=0)

    async def refuse(*_args) -> None:
        raise Is3ConnectionError("Write was not acknowledged")

    coordinator.async_fade = refuse
    light = _light(DIMMER, coordinator)

    with pytest.raises(HomeAssistantError):
        asyncio.run(light.async_turn_on(**{ATTR_BRIGHTNESS: 255, ATTR_TRANSITION: 2}))


def test_dragging_a_slider_sends_only_the_newest_level() -> None:
    """Requests that queue behind a write in flight are overtaken by the next
    one; only the first (already on its way) and the last reach the unit."""
    coordinator = _Coordinator(level=0)
    light = _light(DIMMER, coordinator)

    async def drag() -> None:
        await asyncio.gather(
            *(light.async_turn_on(**{ATTR_BRIGHTNESS: b}) for b in (51, 102, 153, 204, 255))
        )

    asyncio.run(drag())

    levels = [value for _, value in coordinator.commands]
    assert levels[-1] == 100, f"the light was left on the wrong level: {levels}"
    assert 40 not in levels and 60 not in levels and 80 not in levels, levels
