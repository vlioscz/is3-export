"""A wired switch reports short vs long press; an RF remote reports one press.

On a wired button the hold length is clean (with no Connection Server smearing
the timing): a release before the threshold is a short ``press``, and the
threshold elapsing while still held is a ``long_press``.  A lost release cannot
wedge it -- a safety timeout clears the held state.  An RF button keeps the
single-press behaviour, fired on every un-deduped "on" event.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import custom_components.is3_export.event as event_module
from custom_components.is3_export.event import (
    LONG_PRESS,
    LONG_PRESS_SECONDS,
    MAX_HOLD_SECONDS,
    PRESS,
    Is3ButtonEvent,
)
from custom_components.is3_export.export import (
    expected_entities,
    is_press_button,
    is_rf_button,
    parse_export,
)

FIXTURES = Path(__file__).parent / "fixtures"
UP = 0x01010070  # WSB3-20-Hum rocker input -- wired, so short/long


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


def test_a_wired_switch_is_long_capable(export) -> None:
    """A WSB button is wired, so its hold length is usable for short vs long."""
    assert not is_rf_button(export.by_address(UP))


class _Coord:
    def __init__(self) -> None:
        self.values: dict[int, int] = {}


def _button(monkeypatch, long_capable: bool = True) -> tuple[Is3ButtonEvent, list]:
    """A button event whose timers are captured so a test can fire them by hand."""
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
    entity._long_capable = long_capable
    entity._active = False
    entity._debounce = None
    entity._pressed = False
    entity._long_fired = False
    entity._long_timer = None
    entity._max_timer = None
    entity.fired = []
    entity._trigger_event = entity.fired.append
    entity.async_write_ha_state = lambda: None
    return entity, timers


def _press(entity) -> None:
    entity.coordinator.values[entity._address] = 1
    entity._handle_change()


def _release(entity) -> None:
    entity.coordinator.values[entity._address] = 0
    entity._handle_change()


def _fire(timers, delay) -> None:
    next(t for t in timers if t["delay"] == delay and not t["cancelled"])["action"](None)


# --- wired: short vs long ----------------------------------------------------


def test_short_tap_fires_press_on_release(monkeypatch) -> None:
    """Released before the threshold -> a short press, and only on release."""
    entity, timers = _button(monkeypatch)
    _press(entity)
    assert entity.fired == [], "not classified until we know it is short"
    assert len(timers) == 2, "the long and safety timers are armed"
    _release(entity)
    assert entity.fired == [PRESS]
    assert all(t["cancelled"] for t in timers), "both timers cancelled on release"


def test_long_hold_fires_long_press_at_the_threshold(monkeypatch) -> None:
    """The threshold elapsing while still held -> a long press, no short."""
    entity, timers = _button(monkeypatch)
    _press(entity)
    _fire(timers, LONG_PRESS_SECONDS)  # threshold reached, still held
    assert entity.fired == [LONG_PRESS]
    _release(entity)  # the eventual release adds nothing
    assert entity.fired == [LONG_PRESS]


def test_rebroadcast_during_a_hold_is_ignored(monkeypatch) -> None:
    """The unit re-sends =1 mid-hold; it must not start a second interaction."""
    entity, timers = _button(monkeypatch)
    _press(entity)
    _press(entity)  # re-broadcast of the same hold
    assert len(timers) == 2, "still just one hold armed"
    _fire(timers, LONG_PRESS_SECONDS)
    assert entity.fired == [LONG_PRESS]


def test_a_lost_release_does_not_wedge_the_button(monkeypatch) -> None:
    """With the release lost, the safety timeout frees the button for the next press."""
    entity, timers = _button(monkeypatch)
    _press(entity)
    _fire(timers, LONG_PRESS_SECONDS)  # long fires
    assert entity.fired == [LONG_PRESS]
    _fire(timers, MAX_HOLD_SECONDS)  # release never came; safety clears it
    assert not entity._pressed
    timers.clear()
    entity._handle_change()  # the value is still on; the next press starts fresh
    assert entity._pressed
    assert len(timers) == 2


def test_repeated_release_events_add_nothing(monkeypatch) -> None:
    """The unit sends the release several times; only the first counts."""
    entity, timers = _button(monkeypatch)
    _press(entity)
    _release(entity)
    _release(entity)
    _release(entity)
    assert entity.fired == [PRESS]


# --- RF: one press per interaction -------------------------------------------


def test_rf_button_fires_press_on_every_on_event(monkeypatch) -> None:
    """An RF button keeps the single-press behaviour with its debounce."""
    entity, timers = _button(monkeypatch, long_capable=False)
    _press(entity)
    assert entity.fired == [PRESS]
    assert len(timers) == 1, "the debounce window is armed"
    _press(entity)  # immediate re-send: swallowed
    assert entity.fired == [PRESS]
    timers[0]["action"](None)  # debounce ends
    _press(entity)  # next on-event fires again, even un-deduped
    assert entity.fired == [PRESS, PRESS]
