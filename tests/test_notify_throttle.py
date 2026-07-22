"""A chatty sensor's notifications are rate-limited; buttons are not.

One analog input can push dozens of events a second, and a state write for each
floods the loop enough to skew how long a button press looks.  So a sensor's
listeners are woken at most once per window -- its value still stored every time
-- while a button wakes its entity on every change.
"""

from __future__ import annotations

from custom_components.is3_export.coordinator import NOTIFY_THROTTLE, Is3Coordinator

SENSOR = 0x01080001  # an analog input
BUTTON = 0x01010074  # a digital input


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        return self.now


class _Loop:
    def __init__(self, clock: _Clock) -> None:
        self._clock = clock
        self.scheduled: list = []

    def time(self) -> float:
        return self._clock.time()

    def call_later(self, delay, callback, *args):
        self.scheduled.append((self._clock.now + delay, callback, args))
        return object()


class _Hass:
    def __init__(self, loop: _Loop) -> None:
        self.loop = loop


def _coord(throttled: set[int]):
    clock = _Clock()
    loop = _Loop(clock)
    coord = Is3Coordinator.__new__(Is3Coordinator)
    coord.hass = _Hass(loop)
    coord._values = {}
    coord._updated_at = {}
    coord._pending = {}
    coord._address_listeners = {}
    coord._throttled = frozenset(throttled)
    coord._notified_at = {}
    coord._flush_scheduled = set()
    coord._momentary = frozenset()
    coord._seed_attempts = {}
    return coord, clock, loop


def test_update_listeners_wake_only_on_availability_change() -> None:
    """The 30s refresh no longer wakes every entity: only an availability flip
    propagates, and iterating the base registry does not touch (or trip over) the
    separate address-listener dict -- the old name collision that crashed it."""
    coord = Is3Coordinator.__new__(Is3Coordinator)
    woke: list[int] = []
    coord._listeners = {0: (lambda: woke.append(1), None)}  # the base registry
    coord._address_listeners = {0x0102000A: [lambda: None]}  # ours, kept apart
    coord._availability_broadcast = True
    coord.last_update_success = True

    coord.async_update_listeners()  # nothing changed -> no wake, and no crash
    assert woke == []

    coord.last_update_success = False
    coord.async_update_listeners()  # availability flipped -> propagate once
    assert woke == [1]


def test_a_button_wakes_on_every_event_even_a_repeat() -> None:
    """A momentary button is not deduped: a repeated on-event (its release was
    lost, so the value never fell) still wakes its entity, so the press is seen."""
    coord, clock, loop = _coord(throttled=set())
    coord._momentary = frozenset({BUTTON})
    woken = []
    coord._address_listeners[BUTTON] = [lambda: woken.append(1)]
    coord.handle_event(BUTTON, 1)  # press
    coord.handle_event(BUTTON, 1)  # the value never fell; the repeat still wakes
    assert len(woken) == 2


def test_a_button_wakes_its_entity_on_every_change() -> None:
    coord, clock, loop = _coord(throttled=set())
    woken = []
    coord._address_listeners[BUTTON] = [lambda: woken.append(clock.now)]
    coord.handle_event(BUTTON, 1)
    coord.handle_event(BUTTON, 0)
    assert len(woken) == 2
    assert loop.scheduled == [], "buttons are never deferred"


def test_a_sensor_flood_wakes_at_most_once_per_window() -> None:
    coord, clock, loop = _coord(throttled={SENSOR})
    woken = []
    coord._address_listeners[SENSOR] = [lambda: woken.append(clock.now)]

    coord.handle_event(SENSOR, 100)  # first change wakes at once
    assert len(woken) == 1

    clock.now += 0.1
    coord.handle_event(SENSOR, 101)  # within the window: coalesced
    clock.now += 0.1
    coord.handle_event(SENSOR, 102)
    assert len(woken) == 1, "rapid changes are coalesced"
    assert len(loop.scheduled) == 1, "one deferred wake is scheduled"
    assert coord.values[SENSOR] == 102, "the value is stored regardless"

    # When the deferred wake fires, listeners run once with the latest value.
    _fire_at, callback, args = loop.scheduled[0]
    clock.now += NOTIFY_THROTTLE
    callback(*args)
    assert len(woken) == 2


def test_a_sensor_change_after_the_window_wakes_at_once() -> None:
    coord, clock, loop = _coord(throttled={SENSOR})
    woken = []
    coord._address_listeners[SENSOR] = [lambda: woken.append(1)]

    coord.handle_event(SENSOR, 100)
    assert len(woken) == 1
    clock.now += NOTIFY_THROTTLE + 1.0  # well past the window
    coord.handle_event(SENSOR, 101)
    assert len(woken) == 2
    assert loop.scheduled == [], "no need to defer once the window has passed"
