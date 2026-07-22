"""The periodic refresh reads an output once, then leaves it to events.

Re-reading every output on every scan put a burst of GETs on the one shared
ASCII connection, and the unit answered them ahead of pushing its own events --
so a button press could arrive seconds late, queued behind the replies.  The
event stream already keeps an output current, so the refresh seeds it once at
startup and only retries an address that has never answered.
"""

from __future__ import annotations

import asyncio

from custom_components.is3_export.coordinator import MAX_SEED_ATTEMPTS, Is3Coordinator
from custom_components.is3_export.export import Is3Entry, Is3Export

RELAY = 0x0102000A  # a writable output, re-read every cycle before this fix
RELAY_HEX = "0x0102000A"
SENSOR = 0x0108004F  # a readable input that answers no GET here
SENSOR_HEX = "0x0108004F"


class _Loop:
    def time(self) -> float:
        return 1000.0

    def call_later(self, delay, callback, *args):  # pragma: no cover - unused
        return object()


class _Hass:
    def __init__(self) -> None:
        self.loop = _Loop()


class _Client:
    """Records every address read, and answers only those it is given."""

    def __init__(self, answers: dict[str, int]) -> None:
        self._answers = answers
        self.reads: list[str] = []

    async def async_get(self, address: str) -> int | None:
        self.reads.append(address)
        return self._answers.get(address)


def _coordinator(export: Is3Export, client: _Client) -> Is3Coordinator:
    coord = Is3Coordinator.__new__(Is3Coordinator)
    coord.hass = _Hass()
    coord.client = client
    coord.reads_supported = True
    coord._seeded = False
    coord._values = {}
    coord._pending = {}
    coord._updated_at = {}
    coord._listeners = {}
    coord._throttled = frozenset()
    coord._notified_at = {}
    coord._flush_scheduled = set()
    coord._momentary = frozenset()
    coord._seed_attempts = {}

    async def _read_export() -> Is3Export:
        return export

    coord._async_read_export = _read_export  # type: ignore[method-assign]
    return coord


def test_a_reported_output_is_not_reread() -> None:
    """The startup seed reads the relay; a later refresh does not read it again."""
    export = Is3Export(entries=[Is3Entry(name="Rele_kuchyne", address=RELAY, value=0)])
    client = _Client({RELAY_HEX: 1})
    coord = _coordinator(export, client)

    asyncio.run(coord._async_update_data())
    assert client.reads == [RELAY_HEX], "the baseline reads the output once"
    assert coord.values[RELAY] == 1

    client.reads.clear()
    asyncio.run(coord._async_update_data())
    assert client.reads == [], "an output that has reported is left to its events"


def test_a_no_value_address_is_retried_then_left_alone() -> None:
    """An address that keeps answering "no value" is retried a few times, then
    dropped -- otherwise a permanently-"N" schedule keeps a GET burst going."""
    export = Is3Export(
        entries=[Is3Entry(name="Program", address=SENSOR, value=None, unit="°C")]
    )
    client = _Client({})  # the address never answers a value
    coord = _coordinator(export, client)

    for _ in range(MAX_SEED_ATTEMPTS):
        asyncio.run(coord._async_update_data())
    assert client.reads == [SENSOR_HEX] * MAX_SEED_ATTEMPTS, "retried up to the cap"
    assert SENSOR not in coord.values

    client.reads.clear()
    asyncio.run(coord._async_update_data())
    assert client.reads == [], "past the cap it is left to the event stream"
