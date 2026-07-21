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
    is_wsb_button,
    parse_export,
)

FIXTURES = Path(__file__).parent / "fixtures"
UP = 0x01010070  # WSB3-20-Hum rocker input


@pytest.fixture(name="export")
def export_fixture():
    return parse_export((FIXTURES / "wall_switches.is3").read_text(encoding="utf-8-sig"))


def test_wsb_buttons_are_the_digital_inputs(export) -> None:
    """Buttons are a switch's digital inputs -- not its LEDs or thermometers."""
    assert is_wsb_button(export.by_address(0x01010070))  # rocker
    assert is_wsb_button(export.by_address(0x01010072))  # DIN1
    assert not is_wsb_button(export.by_address(0x0102006A))  # green LED (relay)
    assert not is_wsb_button(export.by_address(0x01050001))  # thermometer


def test_expected_entities_lists_a_press_event_per_button(export) -> None:
    """Each button carries an event entity, kept from being pruned."""
    expected = expected_entities(export, "e")
    up = export.by_address(UP)
    assert ("event", f"e_{up.unique_id}_event") in expected


class _Coord:
    def __init__(self) -> None:
        self.values: dict[int, int] = {}


def _button(monkeypatch) -> tuple[Is3ButtonEvent, dict]:
    """A button event wired to a fake timer that fires only when told."""
    timer: dict = {}

    def fake_call_later(hass, delay, action):
        timer["action"] = action

        def cancel() -> None:
            timer["cancelled"] = True

        return cancel

    monkeypatch.setattr(event_module, "async_call_later", fake_call_later)

    entity = Is3ButtonEvent.__new__(Is3ButtonEvent)
    entity.coordinator = _Coord()
    entity.hass = object()
    entity._address = UP
    entity._pressed = False
    entity._long_fired = False
    entity._cancel_timer = None
    entity.fired = []
    entity._trigger_event = entity.fired.append
    entity.async_write_ha_state = lambda: None
    return entity, timer


def test_a_quick_release_is_a_short_press(monkeypatch) -> None:
    entity, timer = _button(monkeypatch)
    entity.coordinator.values[UP] = 1
    entity._handle_change()  # pressed; the long-press clock starts
    entity.coordinator.values[UP] = 0
    entity._handle_change()  # released before the threshold
    assert entity.fired == [SHORT_PRESS]
    assert timer.get("cancelled"), "the pending long-press timer is cancelled"


def test_holding_past_the_threshold_is_a_long_press(monkeypatch) -> None:
    entity, timer = _button(monkeypatch)
    entity.coordinator.values[UP] = 1
    entity._handle_change()  # pressed
    timer["action"](None)  # the threshold elapses while still held
    assert entity.fired == [LONG_PRESS]
    entity.coordinator.values[UP] = 0
    entity._handle_change()  # release adds nothing more
    assert entity.fired == [LONG_PRESS]


def test_a_repeated_press_signal_does_not_restart_the_clock(monkeypatch) -> None:
    """The unit re-sends the on state mid-hold; it must not reset the timer."""
    entity, timer = _button(monkeypatch)
    entity.coordinator.values[UP] = 1
    entity._handle_change()
    first = timer["action"]
    entity._handle_change()  # a duplicate "on" arrives
    assert timer["action"] is first
