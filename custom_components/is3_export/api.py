"""Client for the ASCII port of an iNELS central unit.

The protocol was established by probing a live unit.  It is line oriented, needs
no handshake and has no authentication::

    -> SET 0x0102000A 1
    -> GET 0x0102000A
    <- GET 0x0102000a 0x00000000
    <- EVENT 15 0x01080001 0x00001770

Three properties shape this client:

* **Replies are not synchronous.**  A reply arrives as a line in the stream,
  after an arbitrary number of unsolicited ``EVENT`` lines, and sometimes only
  once the *next* command has been sent.  Requests are therefore matched by the
  address echoed in the reply, never by arrival order.
* **The unit pushes changes.**  Holding the connection open yields ``EVENT``
  lines for every value that changes, so state is followed live instead of
  polled.
* **Not every address is readable.**  Schedules, heating plans and scenes reply
  with a literal ``N``, and a failed sensor replies with ``???``.  Neither
  parses as a number, so both surface as "no value" rather than as zero.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable

from .const import BASE_HEX, CONNECT_TIMEOUT, DELIMITER_SPACE

_LOGGER = logging.getLogger(__name__)

_ENCODING = "ascii"
_EOL = b"\r\n"

# Values are hex; anything else (N, ???) means the address has no value.
_NUMBER = re.compile(r"^(0x[0-9A-Fa-f]+|\d+)$")

READ_TIMEOUT = 5.0
# How long to wait between attempts to get a dropped connection back.
RECONNECT_DELAY = 3.0

type EventCallback = Callable[[int, int], None]
type ReconnectCallback = Callable[[], None]


class Is3Error(Exception):
    """Base error for all failures raised by this client."""


class Is3ConnectionError(Is3Error):
    """The central unit could not be reached."""


class Is3Client:
    """Holds one connection to a central unit and multiplexes it.

    A single reader task consumes the stream and routes each line either to a
    waiting request or to the event callback.
    """

    def __init__(
        self,
        host: str,
        port: int,
        delimiter: str = DELIMITER_SPACE,
        number_base: str = BASE_HEX,
        on_event: EventCallback | None = None,
        on_reconnect: ReconnectCallback | None = None,
    ) -> None:
        """Store the connection parameters; no I/O happens here.

        The delimiter and number base must match the "Third part setting" page
        in IDM3; replies are parsed regardless, but commands have to be spoken
        in the dialect the unit expects.
        """
        self._host = host
        self._port = port
        self._delimiter = delimiter
        self._number_base = number_base
        self._on_event = on_event
        self._on_reconnect = on_reconnect

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[int | None]] = {}
        self._lock = asyncio.Lock()
        self._closing = False

    @property
    def delimiter(self) -> str:
        """The field separator this client sends."""
        return self._delimiter

    @property
    def connected(self) -> bool:
        """Whether the persistent connection is currently up."""
        return self._writer is not None and not self._writer.is_closing()

    async def async_connect(self) -> None:
        """Open the connection and start consuming the stream."""
        async with self._lock:
            await self._async_connect_locked()

    async def async_close(self) -> None:
        """Tear down the connection and stop the reader."""
        self._closing = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        async with self._lock:
            await self._async_disconnect_locked()

    async def async_get(self, address: str) -> int | None:
        """Read one address.

        Returns None when the unit reports no value for it, which is normal for
        schedules, plans, scenes and failed sensors.
        """
        key = int(address, 16)
        loop = asyncio.get_running_loop()

        # A second request for the same address rides on the first one's future.
        if (existing := self._pending.get(key)) is not None:
            return await existing

        future: asyncio.Future[int | None] = loop.create_future()
        self._pending[key] = future
        try:
            await self._async_send(self._command("GET", self.format_address(address)))
            return await asyncio.wait_for(future, READ_TIMEOUT)
        except asyncio.TimeoutError:
            _LOGGER.debug("No reply reading %s from %s", address, self._host)
            return None
        finally:
            self._pending.pop(key, None)

    async def async_set(self, address: str, value: int) -> None:
        """Write a value to one address."""
        await self._async_send(
            self._command(
                "SET", self.format_address(address), self.format_value(value)
            )
        )

    def format_address(self, address: str) -> str:
        """Render an address from the export in the unit's number base.

        Exports always write ``0x0102000A``; a unit configured for decimal
        expects ``16908298``.
        """
        if self._number_base == BASE_HEX:
            return address
        return str(int(address, 16))

    def format_value(self, value: int) -> str:
        """Render a value in the unit's number base.

        The number base applies to values as well as addresses.  Sending a
        dimmer 100 to a unit set to hexadecimal asks it for 0x100, which is 256
        and outside a percentage, so nothing happens -- while a relay works
        either way, because 0 and 1 read the same in both bases.

        Written bare, without an ``0x`` prefix, matching the command shape that
        is known to switch real hardware: ``SET 0x0102000A 1``.

        Negative values are sent as two's complement, the same way the unit
        reports them; formatting a negative directly would put a minus sign on
        the wire, which is not a number the unit reads.
        """
        if self._number_base == BASE_HEX:
            return format(value & 0xFFFFFFFF if value < 0 else value, "X")
        return str(value)

    def _command(self, verb: str, *fields: object) -> bytes:
        """Build one command line in this client's dialect.

        The space form carries a trailing space before the line ending, matching
        the scripts known to switch real hardware.
        """
        line = self._delimiter.join([verb, *(str(field) for field in fields)])
        if self._delimiter == DELIMITER_SPACE:
            line += " "
        return line.encode(_ENCODING) + _EOL

    async def _async_send(self, payload: bytes) -> None:
        """Send one command line, reconnecting first if necessary."""
        async with self._lock:
            if not self.connected:
                await self._async_connect_locked()

            assert self._writer is not None
            _LOGGER.debug("Sending to %s:%s: %r", self._host, self._port, payload)
            try:
                self._writer.write(payload)
                await self._writer.drain()
            except OSError as err:
                await self._async_disconnect_locked()
                raise Is3ConnectionError(f"Failed to send {payload!r}") from err

    async def _async_connect_locked(self) -> None:
        """Open the connection.  The caller must hold the lock."""
        await self._async_disconnect_locked()
        self._closing = False

        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), CONNECT_TIMEOUT
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise Is3ConnectionError(f"Cannot reach {self._host}:{self._port}") from err

        self._reader_task = asyncio.create_task(self._async_read_loop())
        _LOGGER.debug("Connected to %s:%s", self._host, self._port)

    async def _async_disconnect_locked(self) -> None:
        """Close the connection.  The caller must hold the lock."""
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None

        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:  # pragma: no cover - teardown is best effort
                _LOGGER.debug("Error closing connection to %s", self._host)
            self._writer = None
        self._reader = None

        self._fail_pending()

    async def _async_read_loop(self) -> None:
        """Consume the stream, routing replies and events."""
        reader = self._reader
        assert reader is not None

        while True:
            try:
                raw = await reader.readuntil(_EOL)
            except asyncio.IncompleteReadError:
                _LOGGER.debug("Unit %s closed the connection", self._host)
                break
            except (OSError, asyncio.LimitOverrunError) as err:
                _LOGGER.debug("Read error from %s: %s", self._host, err)
                break

            self._handle_line(raw.decode(_ENCODING, errors="replace").strip())

        # The stream ended. Unless we are shutting down, the connection dropped
        # under us -- a unit reboot or a network blip -- and we would otherwise
        # be deaf to every wall-switch change until the next command. Get it
        # back, and resync once it is up so nothing missed meanwhile is stale.
        if not self._closing:
            self._fail_pending()
            self._ensure_reconnecting()

    def _fail_pending(self) -> None:
        """Unblock every waiting read; nothing will answer them now."""
        for future in self._pending.values():
            if not future.done():
                future.set_result(None)
        self._pending.clear()

    def _ensure_reconnecting(self) -> None:
        """Start the reconnect loop if it is not already running."""
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._async_reconnect())

    async def _async_reconnect(self) -> None:
        """Reopen the connection, retrying until it is back or we are closing."""
        while not self._closing:
            await asyncio.sleep(RECONNECT_DELAY)
            if self._closing:
                return
            try:
                async with self._lock:
                    if self._closing or self.connected:
                        return
                    await self._async_connect_locked()
            except Is3ConnectionError:
                continue

            _LOGGER.info("Reconnected to %s:%s", self._host, self._port)
            if self._on_reconnect is not None:
                self._on_reconnect()
            return

    def _handle_line(self, line: str) -> None:
        """Route one line to a waiting request or to the event callback."""
        if not line:
            return

        parsed = parse_line(line, self._delimiter)
        if parsed is None:
            _LOGGER.debug("Ignoring unrecognised line: %r", line)
            return

        kind, address, value = parsed

        if kind == "EVENT":
            if self._on_event is not None and value is not None:
                self._on_event(address, value)
            return

        # A reply. Deliver it even if nobody asked, so it is not mistaken for
        # an answer to a later request for a different address.
        if (future := self._pending.get(address)) is not None and not future.done():
            future.set_result(value)


def parse_line(
    line: str, delimiter: str = DELIMITER_SPACE
) -> tuple[str, int, int | None] | None:
    """Split one line into (kind, address, value).

    Everything about the wire format is configurable in IDM3, so as little as
    possible is assumed here:

    * **Any of the delimiters IDM3 offers.** Lines are split on whitespace and
      on the configured delimiter.  Only the configured one is used, because
      splitting on all of them would mangle a reply such as ``???`` from a
      failed sensor when ``?`` is the delimiter in use.
    * **Either number base.** Hexadecimal values carry an ``0x`` prefix and
      decimal ones do not, so the base is read off the value itself.
    * **Either mode.** "Remote control + IDM" puts an IDM field between the
      verb and the address; that is detected from the field count.

    A None value means the unit reports the address as having none::

        GET 0x0102000a 0x00000001   -> ("GET", 0x0102000A, 1)
        EVENT 15 0x01080001 0x1770  -> ("EVENT", 0x01080001, 6000)
        EVENT 0x01080001 0x1770     -> ("EVENT", 0x01080001, 6000)
        GET 16908298 1              -> ("GET", 0x0102000A, 1)
        GET 0x05010002 N            -> ("GET", 0x05010002, None)
    """
    fields = _split(line, delimiter)
    if len(fields) < 2:
        return None

    kind = fields[0].upper()
    if kind not in ("GET", "SET", "EVENT"):
        return None

    rest = fields[1:]
    if kind == "EVENT" and len(rest) >= 3:
        # In "Remote control + IDM" mode an extra IDM field precedes the address.
        rest = rest[1:]

    address = _parse_number(rest[0])
    if address is None:
        return None

    value = _parse_number(rest[1]) if len(rest) > 1 else None
    return kind, address, value


def _split(line: str, delimiter: str) -> list[str]:
    """Split a line on whitespace and on the configured delimiter."""
    normalised = line.strip()
    if delimiter and not delimiter.isspace():
        normalised = normalised.replace(delimiter, " ")
    return normalised.split()


def _parse_number(field: str) -> int | None:
    """Read a number in whichever base the unit is configured to send.

    Values that are not numbers at all -- ``N`` for an address with no value,
    ``???`` for a failed sensor -- come back as None.
    """
    if not _NUMBER.match(field):
        return None
    return int(field, 16) if field.lower().startswith("0x") else int(field)
