"""A wall switch / RF button fires one reliable press per interaction.

Short vs long cannot be told apart over this stream -- the unit's release event
is delayed by up to seconds, or lost -- so a single press is fired on the
leading edge, and the re-broadcasts and the late/lost release are swallowed for
a refractory window as one interaction, which then clears the input so a lost
release cannot wedge it on and swallow the next press.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import custom_components.is3_export.event as event_module
from custom_components.is3_export.event import PRESS, Is3ButtonEvent
from custom_components.is3_export.export import (
    expected_entities,
    is_press_button,
    parse_export,
)

FIXTURES = Path(__file__).parent / "fixtures"
UP = 0x01010070  # WSB3-20-Hum rocker input


@pytest.fixture(name="export")
def export_fixture():
    return parse_export((FIXTURES / "wall_switches.is3").read_text(encoding="utf-8-sig"))


def test_wsb_buttons_are_the_digital_inputs(export) -> None:
    """Buttons are a switch's digital inputs -- not its LEDs or thermometers."""
    assert is_press_button(export.by_address(0x01010070))  # rocker
    assert is_press_button(export.by_address(0x01010072))  # DIN1
    assert not is_press_button(export.by_address(0x0102006A))  # green LED (relay)
    assert not is_press_button(export.by_address(0x01050001))  # thermometer


def test_a_button_is_an_event_not_a_binary_sensor(export) -> None:
    """Each button is an event entity, not the binary sensor its input would be."""
    expected = expected_entities(export, "e")
    up = export.by_address(UP)
    assert ("event", f"e_{up.unique_id}_event") in expected
    assert ("binary_sensor", f"e_{up.unique_id}") not in expected


class _Coord:
    def __init__(self) -> None:
        self.values: dict[int, int] = {}
        self.reset_calls: list[int] = []

    def async_reset(self, address: int) -> None:
        self.reset_calls.append(address)
        self.values[address] = 0


def _button(monkeypatch) -> tuple[Is3ButtonEvent, list]:
    """A button event wired to a fake refractory timer that fires when told."""
    timers: list[dict] = []

    def fake_call_later(hass, delay, action):
        record = {"delay": delay, "action": action, "cancelled": False}
        timers.append(record)

        def cancel() -> None:
            record["cancelled"] = True

        return cancel

    monkeypatch.setattr(event_module, "async_call_later", fake_call_later)

    entity = Is3ButtonEvent.__new__(Is3ButtonEvent)
    entity.coordinator = _Coord()
    entity.hass = object()
    entity._address = UP
    entity._active = False
    entity._cancel = None
    entity.fired = []
    entity._trigger_event = entity.fired.append
    entity.async_write_ha_state = lambda: None
    return entity, timers


def test_a_press_fires_once_on_the_leading_edge(monkeypatch) -> None:
    entity, timers = _button(monkeypatch)
    entity.coordinator.values[UP] = 1
    entity._handle_change()  # the 0 -> 1 edge
    assert entity.fired == [PRESS]
    assert entity._active
    assert len(timers) == 1, "the refractory window is armed"


def test_rebroadcasts_and_the_release_are_swallowed(monkeypatch) -> None:
    """The on state re-broadcast while held, and the release, add nothing."""
    entity, timers = _button(monkeypatch)
    entity.coordinator.values[UP] = 1
    entity._handle_change()  # press
    entity._handle_change()  # a re-broadcast "=1" while still held
    entity.coordinator.values[UP] = 0
    entity._handle_change()  # the release
    assert entity.fired == [PRESS]


def test_the_window_end_clears_the_input(monkeypatch) -> None:
    """When the refractory ends, the input is forced off so the next press is fresh."""
    entity, timers = _button(monkeypatch)
    entity.coordinator.values[UP] = 1
    entity._handle_change()  # press
    timers[0]["action"](None)  # the window ends
    assert entity._active is False
    assert entity.coordinator.reset_calls == [UP]


def test_the_next_press_fires_after_the_window(monkeypatch) -> None:
    entity, timers = _button(monkeypatch)
    entity.coordinator.values[UP] = 1
    entity._handle_change()  # press 1
    timers[0]["action"](None)  # window ends -> value reset to 0
    entity.coordinator.values[UP] = 1
    entity._handle_change()  # press 2 on a fresh edge
    assert entity.fired == [PRESS, PRESS]


def test_a_lost_release_does_not_wedge_the_button(monkeypatch) -> None:
    """No release arrives; the window still clears the stuck value, so the next
    press is seen -- the "first press nothing, second press wrong" cure."""
    entity, timers = _button(monkeypatch)
    entity.coordinator.values[UP] = 1
    entity._handle_change()  # press 1 (its release will be lost)
    timers[0]["action"](None)  # window ends -> async_reset forces the value off
    assert entity.coordinator.values[UP] == 0
    entity.coordinator.values[UP] = 1
    entity._handle_change()  # press 2 detected despite the lost release
    assert entity.fired == [PRESS, PRESS]
