"""Seeding: read every readable address each cycle, without undoing live events.

The unit answers tens of addresses per datagram, so the coordinator re-reads the
whole address book on every cycle rather than tracking which addresses have
reported.  That is what makes a channel whose events stop arriving correct
itself.  The one hazard is timing: a wall switch can change an output while the
batch is in flight, and the reply was captured before that -- applying it would
put the old value back.
"""

from __future__ import annotations

import asyncio

from custom_components.is3_export.coordinator import Is3Coordinator
from custom_components.is3_export.errors import Is3AuthError, Is3ConnectionError
from custom_components.is3_export.export import Is3Entry, Is3Export

LAMP = 0x0102000D
LAMP_HEX = "0x0102000D"
SCHEDULE = 0x0108004F  # readable, but the unit reports no value for it
SCHEDULE_HEX = "0x0108004F"
BUTTON = 0x01010074  # a wall-switch input: momentary, never re-read
BUTTON_HEX = "0x01010074"


class _Clock:
    """A loop clock the test advances by hand."""

    def __init__(self) -> None:
        self._now = 1000.0

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def call_later(self, delay, callback, *args):  # pragma: no cover - unused
        return object()


class _Hass:
    def __init__(self, clock: _Clock) -> None:
        self.loop = clock


class _Client:
    """Answers a batch read, and records which addresses were asked for."""

    def __init__(self, answers: dict[str, int | None], error: Exception | None = None):
        self._answers = answers
        self._error = error
        self.batches: list[list[str]] = []

    async def async_get_many(self, addresses: list[str]) -> dict[str, int | None]:
        self.batches.append(list(addresses))
        if self._error is not None:
            raise self._error
        return {a: self._answers.get(a) for a in addresses}

    async def async_unit_state(self):
        """The refresh asks each cycle; this unit does not answer."""
        return None

    async def async_project_digest(self) -> bytes | None:
        """Likewise -- so the export falls back to its reload timer."""
        return None


def _coordinator(clock: _Clock, client) -> Is3Coordinator:
    """A coordinator with its base class and I/O bypassed."""
    coord = Is3Coordinator.__new__(Is3Coordinator)
    coord.hass = _Hass(clock)
    coord.client = client
    coord.reads_supported = True
    coord._seeded = False
    coord._values = {}
    coord._pending = {}
    coord._updated_at = {}
    coord._address_listeners = {}
    coord._throttled = frozenset()
    coord._notified_at = {}
    coord._flush_scheduled = set()
    coord._momentary = frozenset()
    coord.unit_state = None
    coord._unit_state_listeners = []
    coord._project_digest = None
    return coord


def test_every_readable_address_is_read_every_cycle() -> None:
    """No address is ever written off, so a channel that goes quiet self-heals."""
    export = Is3Export(
        entries=[
            Is3Entry(name="Lampa", address=LAMP, value=0),
            Is3Entry(name="Program", address=SCHEDULE, value=None, unit="°C"),
        ]
    )
    client = _Client({LAMP_HEX: 1})  # the schedule reports no value, ever
    coord = _coordinator(_Clock(), client)

    async def _read_export() -> Is3Export:
        return export

    coord._async_read_export = _read_export  # type: ignore[method-assign]

    asyncio.run(coord._async_update_data())
    asyncio.run(coord._async_update_data())

    assert client.batches == [[LAMP_HEX, SCHEDULE_HEX]] * 2
    assert coord.values[LAMP] == 1
    assert SCHEDULE not in coord.values, "no value means no value, not zero"


def test_buttons_are_never_re_read() -> None:
    """Reading a button can only invent a press.

    Everything a button means is in the transition, which arrives as an event.
    Its level says nothing -- and an RF input whose release went missing sits at
    "down", so re-reading it each cycle would fire a press every 30 seconds for
    as long as it stayed stuck.
    """
    export = Is3Export(
        entries=[
            Is3Entry(name="Lampa", address=LAMP, value=0),
            Is3Entry(name="TL_chodba", address=BUTTON, value=0),
        ]
    )
    client = _Client({LAMP_HEX: 1, BUTTON_HEX: 1})
    coord = _coordinator(_Clock(), client)

    async def _read_export() -> Is3Export:
        return export

    coord._async_read_export = _read_export  # type: ignore[method-assign]

    asyncio.run(coord._async_update_data())

    assert client.batches == [[LAMP_HEX]], "the button address was read anyway"
    assert BUTTON not in coord.values


def test_a_read_in_flight_does_not_undo_a_fresher_event() -> None:
    """An event that lands mid-batch wins over the older reply."""
    clock = _Clock()

    class _SlowClient(_Client):
        async def async_get_many(self, addresses):
            # The wall switch turns the lamp on while the batch is in flight.
            clock.advance(0.2)
            coord.handle_event(LAMP, 1)
            clock.advance(0.2)
            return {a: 0 for a in addresses}  # captured before the change

    coord = _coordinator(clock, None)
    coord.client = _SlowClient({})
    coord._values[LAMP] = 0

    asyncio.run(coord._async_seed([LAMP_HEX]))

    assert coord.values[LAMP] == 1, "the event turned it on; the read must not undo it"


def test_a_read_is_applied_when_nothing_changed() -> None:
    """With no event in flight the reply is the baseline."""
    clock = _Clock()
    coord = _coordinator(clock, _Client({LAMP_HEX: 1}))

    asyncio.run(coord._async_seed([LAMP_HEX]))

    assert coord.values[LAMP] == 1


def test_a_refused_password_asks_for_a_new_one() -> None:
    """An auth failure must reach Home Assistant as an auth failure."""
    from homeassistant.exceptions import ConfigEntryAuthFailed

    coord = _coordinator(_Clock(), _Client({}, error=Is3AuthError("refused")))

    try:
        asyncio.run(coord._async_seed([LAMP_HEX]))
    except ConfigEntryAuthFailed:
        return
    raise AssertionError("a refused password must raise ConfigEntryAuthFailed")


def test_an_unreachable_unit_fails_the_update() -> None:
    """A transport failure is a failed update, not a password problem."""
    from homeassistant.helpers.update_coordinator import UpdateFailed

    coord = _coordinator(_Clock(), _Client({}, error=Is3ConnectionError("gone")))

    try:
        asyncio.run(coord._async_seed([LAMP_HEX]))
    except UpdateFailed:
        return
    raise AssertionError("an unreachable unit must raise UpdateFailed")
