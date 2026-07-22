"""A slow read must not overwrite a value an event changed while it was in flight.

During the periodic refresh the coordinator re-reads outputs one at a time.
Reading takes long enough that a wall switch can change an output mid-read; the
reply was captured before the change, so applying it would undo the fresh event
value and leave the entity wrong until the next cycle.
"""

from __future__ import annotations

import asyncio

from custom_components.is3_export.coordinator import Is3Coordinator

ADDR = 0x0102000D  # a lamp relay
ADDR_HEX = "0x0102000d"


class _Clock:
    """A loop clock the test advances by hand."""

    def __init__(self) -> None:
        self._now = 1000.0

    def time(self) -> float:
        """Current fake time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move time forward."""
        self._now += seconds


class _Hass:
    """Just the loop the coordinator reads the clock from."""

    def __init__(self, clock: _Clock) -> None:
        self.loop = clock


class _Client:
    """A client whose GET returns a stale value, and lets an event land first."""

    def __init__(self, coord: Is3Coordinator, clock: _Clock) -> None:
        self._coord = coord
        self._clock = clock
        self.stale_value = 0

    async def async_get(self, address: str) -> int:
        """Simulate a slow read: an event arrives before the reply does."""
        # The wall switch turns the lamp on while our read is in flight.
        self._clock.advance(0.2)
        self._coord.handle_event(int(address, 16), 1)
        self._clock.advance(0.2)
        return self.stale_value  # the reply, captured before the change


def _coordinator(clock: _Clock) -> Is3Coordinator:
    """A coordinator with its base class and I/O bypassed."""
    coord = Is3Coordinator.__new__(Is3Coordinator)
    coord.hass = _Hass(clock)
    coord._values = {}
    coord._pending = {}
    coord._updated_at = {}
    coord._listeners = {}
    coord._throttled = frozenset()
    coord._notified_at = {}
    coord._flush_scheduled = set()
    coord._momentary = frozenset()
    return coord


def test_seed_does_not_clobber_a_fresher_event() -> None:
    """The stale read is dropped; the event's value stands."""
    clock = _Clock()
    coord = _coordinator(clock)
    coord.client = _Client(coord, clock)

    coord._values[ADDR] = 0  # entity starts off
    asyncio.run(coord._async_seed([ADDR_HEX]))

    assert coord.values[ADDR] == 1, "the event turned it on; the read must not undo it"


def test_seed_applies_a_read_when_nothing_changed() -> None:
    """With no event in flight, the read is applied as the baseline."""
    clock = _Clock()
    coord = _coordinator(clock)

    class _Quiet:
        async def async_get(self, address: str) -> int:
            clock.advance(0.1)
            return 1

    coord.client = _Quiet()
    asyncio.run(coord._async_seed([ADDR_HEX]))
    assert coord.values[ADDR] == 1
