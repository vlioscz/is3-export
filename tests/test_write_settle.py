"""The write-settle logic that keeps a stale event from undoing a command.

The coordinator needs a HomeAssistant to construct, so these drive the two
callbacks -- handle_event and async_note_write -- against a minimal stand-in
that supplies just the loop clock and the value store they touch. The logic
under test is synchronous and self-contained.
"""

from __future__ import annotations

from custom_components.is3_export.coordinator import Is3Coordinator, WRITE_SETTLE

ADDR = 0x0102000A


class _Clock:
    """A loop whose time only moves when the test says so."""

    def __init__(self) -> None:
        self._now = 1000.0

    def time(self) -> float:
        """Current fake time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move time forward."""
        self._now += seconds


class _Hass:
    """Just enough HomeAssistant for the callbacks under test."""

    def __init__(self, clock: _Clock) -> None:
        self.loop = clock


def _coordinator(clock: _Clock) -> Is3Coordinator:
    """A coordinator with its I/O and base class bypassed."""
    coord = Is3Coordinator.__new__(Is3Coordinator)
    coord.hass = _Hass(clock)
    coord._values = {}
    coord._pending = {}
    coord._updated_at = {}
    coord._address_listeners = {}
    coord._throttled = frozenset()
    coord._notified_at = {}
    coord._flush_scheduled = set()
    coord._momentary = frozenset()
    coord._seed_attempts = {}
    return coord


def test_stale_event_does_not_undo_a_command() -> None:
    """ON then OFF: the late echo of ON must not flip the switch back on."""
    clock = _Clock()
    coord = _coordinator(clock)

    coord.async_note_write(ADDR, 1)
    clock.advance(0.2)
    coord.async_note_write(ADDR, 0)  # user toggled off almost at once
    assert coord.values[ADDR] == 0

    # The unit now echoes the first command, arriving after the second was sent.
    clock.advance(0.3)
    coord.handle_event(ADDR, 1)
    assert coord.values[ADDR] == 0, "the stale ON echo must be ignored"

    # The echo of the second command confirms the real state.
    coord.handle_event(ADDR, 0)
    assert coord.values[ADDR] == 0


def test_confirming_event_clears_the_wait() -> None:
    """Once the unit confirms the written value, later events are trusted."""
    clock = _Clock()
    coord = _coordinator(clock)

    coord.async_note_write(ADDR, 1)
    coord.handle_event(ADDR, 1)  # confirmed

    # A genuine change now comes straight through, no settle window in the way.
    coord.handle_event(ADDR, 0)
    assert coord.values[ADDR] == 0


def test_external_change_is_honoured_after_the_window() -> None:
    """A contradicting event past the settle window is a real change."""
    clock = _Clock()
    coord = _coordinator(clock)

    coord.async_note_write(ADDR, 1)
    clock.advance(WRITE_SETTLE + 0.1)
    coord.handle_event(ADDR, 0)  # someone used the wall switch
    assert coord.values[ADDR] == 0


def test_a_new_command_restarts_the_window() -> None:
    """Rapid toggles each extend authority to the latest command."""
    clock = _Clock()
    coord = _coordinator(clock)

    coord.async_note_write(ADDR, 1)
    clock.advance(WRITE_SETTLE - 0.5)
    coord.async_note_write(ADDR, 0)  # newer command, its own fresh window
    clock.advance(1.0)  # past the first window, inside the second

    coord.handle_event(ADDR, 1)  # stale echo of the first command
    assert coord.values[ADDR] == 0


def test_events_pass_through_when_nothing_was_written() -> None:
    """With no pending command, every event is applied as is."""
    clock = _Clock()
    coord = _coordinator(clock)

    coord.handle_event(ADDR, 1)
    assert coord.values[ADDR] == 1
    coord.handle_event(ADDR, 0)
    assert coord.values[ADDR] == 0
