"""A wall switch button's short and long press.

The unit reports the input going on and off, so the length of a press is
measured: a hold that outlasts the threshold fires a long press while the
button is still down, a shorter one a short press on release.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import custom_components.is3_export.event as event_module
from custom_components.is3_export.event import (
    LONG_PRESS,
    SHORT_PRESS,
    Is3ButtonEvent,
)
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


def _button(monkeypatch) -> tuple[Is3ButtonEvent, list]:
    """A button event wired to fake timers that fire only when told."""
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
    entity._pressed = False
    entity._long_fired = False
    entity._timers = []
    entity.fired = []
    entity._trigger_event = entity.fired.append
    entity.async_write_ha_state = lambda: None
    return entity, timers


def _timer(timers: list, delay: float) -> dict:
    """The scheduled timer with the given delay."""
    return next(t for t in timers if t["delay"] == delay)


def test_a_quick_release_is_a_short_press(monkeypatch) -> None:
    entity, timers = _button(monkeypatch)
    entity.coordinator.values[UP] = 1
    entity._handle_change()  # pressed; the clocks start
    entity.coordinator.values[UP] = 0
    entity._handle_change()  # released before the threshold
    assert entity.fired == [SHORT_PRESS]
    assert all(t["cancelled"] for t in timers), "both timers are cancelled"


def test_holding_past_the_threshold_is_a_long_press(monkeypatch) -> None:
    entity, timers = _button(monkeypatch)
    entity.coordinator.values[UP] = 1
    entity._handle_change()  # pressed
    _timer(timers, event_module.LONG_PRESS_SECONDS)["action"](None)  # threshold
    assert entity.fired == [LONG_PRESS]
    entity.coordinator.values[UP] = 0
    entity._handle_change()  # release adds nothing more
    assert entity.fired == [LONG_PRESS]


def test_a_repeated_press_signal_does_not_restart_the_clock(monkeypatch) -> None:
    """The unit re-sends the on state mid-hold; it must not reset the timers."""
    entity, timers = _button(monkeypatch)
    entity.coordinator.values[UP] = 1
    entity._handle_change()
    count = len(timers)
    entity._handle_change()  # a duplicate "on" arrives
    assert len(timers) == count


def test_a_lost_release_forces_the_input_off(monkeypatch) -> None:
    """With no release in a plausible time -- the off event was lost -- the input
    is cleared, so it does not stick on and the next press is seen again."""
    entity, timers = _button(monkeypatch)
    entity.coordinator.values[UP] = 1
    entity._handle_change()  # pressed, no release follows
    _timer(timers, event_module.MAX_HOLD_SECONDS)["action"](None)
    assert entity.coordinator.reset_calls == [UP]
