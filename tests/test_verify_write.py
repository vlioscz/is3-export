"""The read-back that corrects a write which did not take.

After a write the coordinator reads the address back; if the output did not
follow -- rejected, or moved from the wall meanwhile -- the real value replaces
the optimistic one, so the entity is not left stuck.
"""

from __future__ import annotations

import asyncio

import custom_components.is3_export.coordinator as coordinator_module
from custom_components.is3_export.api import Is3Error
from custom_components.is3_export.coordinator import Is3Coordinator

ADDR = 0x0102000A


class _Clock:
    def __init__(self) -> None:
        self._now = 1000.0

    def time(self) -> float:
        return self._now


class _Hass:
    def __init__(self, clock: _Clock) -> None:
        self.loop = clock


class _Client:
    """Returns a fixed read-back value, or raises."""

    def __init__(self, read_value: int | None, *, fail: bool = False) -> None:
        self._read_value = read_value
        self._fail = fail

    async def async_get(self, address: str) -> int | None:
        if self._fail:
            raise Is3Error("boom")
        return self._read_value


def _coordinator(clock: _Clock, client: _Client) -> Is3Coordinator:
    coord = Is3Coordinator.__new__(Is3Coordinator)
    coord.hass = _Hass(clock)
    coord.client = client
    coord._values = {}
    coord._pending = {}
    coord._updated_at = {}
    coord._listeners = {}
    return coord


def _confirm(coord: Is3Coordinator, value: int, monkeypatch) -> None:
    """Run the read-back confirmation without the real delay."""
    monkeypatch.setattr(coordinator_module, "WRITE_VERIFY_DELAY", 0)
    asyncio.run(coord._async_confirm_write(ADDR, value))


def test_read_back_corrects_a_write_that_did_not_take(monkeypatch) -> None:
    """We wrote 1, but the output is really 0: the entity is corrected to 0."""
    clock = _Clock()
    coord = _coordinator(clock, _Client(read_value=0))
    coord.async_note_write(ADDR, 1)  # optimistic ON
    assert coord.values[ADDR] == 1

    _confirm(coord, 1, monkeypatch)
    assert coord.values[ADDR] == 0, "the read-back should correct the stuck value"


def test_read_back_leaves_a_write_that_took(monkeypatch) -> None:
    """We wrote 1 and the output is 1: nothing changes."""
    clock = _Clock()
    coord = _coordinator(clock, _Client(read_value=1))
    coord.async_note_write(ADDR, 1)

    _confirm(coord, 1, monkeypatch)
    assert coord.values[ADDR] == 1


def test_a_confirming_event_skips_the_read_back(monkeypatch) -> None:
    """If an event already cleared the pending write, no correction happens."""
    clock = _Clock()
    # The client would report 0, but the event settled it, so it must be ignored.
    coord = _coordinator(clock, _Client(read_value=0))
    coord.async_note_write(ADDR, 1)
    coord.handle_event(ADDR, 1)  # unit confirms; clears pending

    _confirm(coord, 1, monkeypatch)
    assert coord.values[ADDR] == 1, "a settled write must not be second-guessed"


def test_a_newer_write_is_not_overwritten(monkeypatch) -> None:
    """A read-back for an old value must not undo a newer command."""
    clock = _Clock()
    coord = _coordinator(clock, _Client(read_value=0))
    coord.async_note_write(ADDR, 1)
    coord.async_note_write(ADDR, 0)  # user toggled off before the first confirm

    _confirm(coord, 1, monkeypatch)  # confirm for the stale ON
    assert coord.values[ADDR] == 0, "the newer OFF stands"


def test_a_read_error_leaves_the_value_alone(monkeypatch) -> None:
    """If the read-back fails, the optimistic value is kept, not cleared."""
    clock = _Clock()
    coord = _coordinator(clock, _Client(read_value=None, fail=True))
    coord.async_note_write(ADDR, 1)

    _confirm(coord, 1, monkeypatch)
    assert coord.values[ADDR] == 1
