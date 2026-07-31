"""A heating zone's temperature self-heals if its change events stop.

The coordinator seeds every readable address once, then leaves it to the event
stream.  But a controller's computed temperature outputs are, on some units,
never sent as events -- so a value read once at startup would freeze (a warm
room still reading its cold-morning value).  A small rotating batch of the
temperature channels is re-read each cycle to catch such a value up, kept tiny
so it never becomes the GET burst that once delayed button presses.
"""

from __future__ import annotations

import asyncio

from custom_components.is3_export.api import Is3ConnectionError
from custom_components.is3_export.coordinator import (
    STALE_REFRESH_BATCH,
    Is3Coordinator,
)
from custom_components.is3_export.export import Is3Controller

ACTUAL = 0x01080007
REQUIRED = 0x01080008
COOL_REQUIRED = 0x01080009


def _controller(**overrides) -> Is3Controller:
    fields = dict(
        name="Loznice",
        serial="0E0001",
        actual=ACTUAL,
        required=REQUIRED,
        manual=0x01120007,
        heat_demand=0x01010063,
        preset_select=0x01110004,
    )
    fields.update(overrides)
    return Is3Controller(**fields)


def test_temperature_addresses_are_the_computed_temp_outputs() -> None:
    assert _controller().temperature_addresses == [ACTUAL, REQUIRED]
    with_cool = _controller(cool_required=COOL_REQUIRED)
    assert with_cool.temperature_addresses == [ACTUAL, REQUIRED, COOL_REQUIRED]


def _rotating_coord(addresses: list[int]) -> Is3Coordinator:
    coord = Is3Coordinator.__new__(Is3Coordinator)
    coord._refresh_addresses = addresses
    coord._refresh_cursor = 0
    return coord


def test_refresh_batch_rotates_over_every_address() -> None:
    addresses = list(range(1, 16))  # 15, not a multiple of the batch size
    coord = _rotating_coord(addresses)

    covered: set[int] = set()
    for _ in range(5):  # 5 * batch comfortably covers 15 with wrap-around
        batch = coord._next_refresh_batch()
        assert len(batch) == STALE_REFRESH_BATCH
        covered |= set(batch)
    assert covered == set(addresses)


def test_refresh_batch_is_empty_without_heating_zones() -> None:
    assert _rotating_coord([])._next_refresh_batch() == []


class _Loop:
    def time(self) -> float:
        return 1000.0


class _Hass:
    loop = _Loop()


class _Client:
    def __init__(self, answers: dict[str, int] | None = None, error: bool = False) -> None:
        self._answers = answers or {}
        self._error = error
        self.reads: list[str] = []

    async def async_get(self, address: str) -> int | None:
        self.reads.append(address)
        if self._error:
            raise Is3ConnectionError("unreachable")
        return self._answers.get(address)


def _refresh_coord(client: _Client, values: dict[int, int]) -> Is3Coordinator:
    coord = Is3Coordinator.__new__(Is3Coordinator)
    coord.hass = _Hass()
    coord.client = client
    coord._values = values
    coord._updated_at = {}
    coord._address_listeners = {}
    coord._throttled = frozenset()
    coord._momentary = frozenset()
    coord._notified_at = {}
    coord._flush_scheduled = set()
    return coord


def test_refresh_heals_a_frozen_temperature() -> None:
    """A value stuck at the old reading is corrected when the unit reports anew."""
    client = _Client({"0x01080007": 2387})  # unit now reports 23.87 C
    coord = _refresh_coord(client, {ACTUAL: 950})  # HA frozen at 9.50 C

    asyncio.run(coord._async_refresh([ACTUAL]))

    assert client.reads == ["0x01080007"]
    assert coord.values[ACTUAL] == 2387


def test_refresh_ignores_a_read_error() -> None:
    """A read that errors leaves the value untouched and does not raise."""
    client = _Client(error=True)
    coord = _refresh_coord(client, {ACTUAL: 950})

    asyncio.run(coord._async_refresh([ACTUAL]))

    assert coord.values[ACTUAL] == 950  # unchanged, no exception
