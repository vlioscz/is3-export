"""Async client for a CU3 central unit, over UDP 9999.

That is the port the unit's own configuration software uses, so it is open on
every generation with nothing switched on first.  Reads come back in batches --
one datagram covers tens of addresses, and a whole installation well under a
second.

Design notes:

* **One request in flight.** Requests are serialized behind a lock and matched to
  their reply by packet id (the unit echoes ``id + 1``) and type/instruction, so
  UDP loss or reordering -- real over Tailscale/IPv6 -- never crosses two
  requests.  A lost reply is retried by resending.
* **The data plane needs authorization.** ``GetValue`` / ``SetValue`` are silently
  ignored until authorized as user 1 (Admin); an empty password works on units
  with none set.  The 8-byte token goes in every authorized packet and can
  expire, so a NACK or a run of timeouts triggers one reauth-and-retry.
* **The unit pushes changes.** After connecting, ``EventStart`` turns on the
  ``EventValue`` stream; each pushed (address, value) is delivered to
  ``on_event`` -- no polling.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from . import protocol as proto
from .errors import Is3AuthError, Is3ConnectionError

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 9999
CONNECT_TIMEOUT = 5.0
REQUEST_TIMEOUT = 3.0
REQUEST_RETRIES = 4
RECONNECT_DELAY = 3.0
KEEPALIVE_INTERVAL = 30.0
# GetValue body is count(1) + key(4)*N; keep a batch comfortably inside one MTU.
MAX_BATCH = 40

type EventCallback = Callable[[int, int | None], None]
type ReconnectCallback = Callable[[], None]


# This transport raises the integration's own errors (see .errors), so nothing
# downstream has to know which protocol is underneath.


async def async_password_required(
    host: str, port: int = DEFAULT_PORT
) -> bool | None:
    """Whether a unit has a password set, asked without authorizing.

    A standalone question for the setup dialog, which needs the answer exactly
    when it has no working session: it is how "you left the password blank and
    this unit wants one" is told apart from "the password you typed is wrong".
    None means the unit did not answer.
    """
    client = Is3Client(host, port, "")
    try:
        await client._open()
        return await client.async_password_required()
    except Is3ConnectionError:
        return None
    finally:
        await client.async_close()


class _Protocol(asyncio.DatagramProtocol):
    """Feeds every datagram back to the client and reports link loss."""

    def __init__(self, client: Is3Client) -> None:
        self._client = client

    def datagram_received(self, data: bytes, addr: object) -> None:
        self._client._on_datagram(data)

    def error_received(self, exc: Exception) -> None:  # pragma: no cover
        _LOGGER.debug("Datagram error from %s: %s", self._client.host, exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc is not None:
            self._client._on_link_lost(exc)


class Is3Client:
    """One authorized UDP session to a central unit."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        password: str = "",
        *,
        user: int = proto.USER_ADMIN,
        on_event: EventCallback | None = None,
        on_reconnect: ReconnectCallback | None = None,
        request_timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self._password = password
        self._user = user
        self._on_event = on_event
        self._on_reconnect = on_reconnect
        self._request_timeout = request_timeout

        self._transport: asyncio.DatagramTransport | None = None
        self._token = b"\x00" * 8
        self._pid = 0
        self._lock = asyncio.Lock()
        self._pending: tuple[tuple[int, int, int], int, asyncio.Future[proto.Packet]] | None = None
        self._connected = False
        self._closing = False
        self._keepalive_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._warned_foreign = False
        # Datagrams that arrived looking right and failed their checksum.  Never
        # normal on a working link, and invisible from the outside, so it is
        # counted and reported in the diagnostics rather than only logged.
        self.corrupt_datagrams = 0
        # Whether the unit turned its push stream on.  Not every one does, and
        # it is not fatal -- it only means changes wait for the next refresh.
        self.events_started = False
        self._events_refused = False
        # The unit's own description, captured raw at connect: run state, unit
        # info, protocol info.  A firmware fingerprint for the diagnostics; see
        # _authorize.  Empty until the first handshake.
        self.fingerprint: dict[str, str | None] = {}

    # ---- public interface (mirrors Is3Client) ----------------------------

    @property
    def connected(self) -> bool:
        return self._connected and self._transport is not None

    async def async_connect(self) -> None:
        """Open the socket, authorize, and start the event stream."""
        self._closing = False
        await self._open()

        # Ask something unauthenticated first.  A UDP socket "opens" against an
        # address with nothing behind it, so without this the handshake runs its
        # four steps into silence -- three quarters of a minute -- and then
        # reports the only failure it can distinguish, a refused password.
        # Being told the password is wrong when the unit is simply not there
        # sends people looking in exactly the wrong place.
        if await self._send_once(proto.T_STARTSTOP, 0x03, 0x00, auth=False) is None:
            await self._teardown()
            raise Is3ConnectionError(
                f"No answer from {self.host}:{self.port}"
            )

        outcome = await self._authorize()
        if outcome == "silent":
            # It answered the question before this one, so it is there and the
            # network is fine.  A unit that then goes quiet is not refusing a
            # password -- it is not holding the conversation, which is what one
            # left half-started after a project was written looks like.
            await self._teardown()
            raise Is3ConnectionError(
                f"{self.host} answered the first request but never the one that "
                f"signs in. The unit is reachable but is not completing the "
                f"connection: it may still be starting up, or need restarting"
            )
        if outcome != "ok":
            await self._teardown()
            raise Is3AuthError(
                f"Authorization refused by {self.host} (wrong password?)"
            )
        # A unit that will not turn on its event stream is still a perfectly
        # good unit: reads and writes work, and the coordinator re-reads
        # everything on its cycle anyway.  Refusing to set up over this was
        # worse than the problem -- it took an installation that was working
        # and made it not work at all.  So say so, loudly and once, and carry
        # on: changes then show up within the refresh interval instead of
        # instantly.
        self.events_started = await self._event_start()
        if not self.events_started:
            _LOGGER.warning(
                "%s did not acknowledge the event stream. Values will follow "
                "the periodic refresh instead of arriving as they change",
                self.host,
            )
        self._connected = True
        self._start_keepalive()

    async def async_close(self) -> None:
        """Tear down the session and stop all background tasks."""
        self._closing = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        await self._teardown()

    async def async_get(self, address: str) -> int | None:
        """Read one address.  None means the unit reports no value for it.

        A read that does not get through raises instead of returning None: the
        difference matters to the caller probing whether the password was
        accepted, which would otherwise read a refusal as "this address is
        simply blank".
        """
        return (await self.async_get_many([address])).get(address)

    async def async_get_many(self, addresses: list[str]) -> dict[str, int | None]:
        """Read many addresses; one datagram per batch of ``MAX_BATCH``.

        A batch that goes unanswered fails the read, and with it the refresh
        cycle -- which is the right answer when the unit has gone away, and is
        the usual reason.  The error names the batch, because the alternative
        (retrying smaller and smaller batches) would turn an unreachable unit
        into minutes of timeouts instead of one.
        """
        result: dict[str, int | None] = {}
        for i in range(0, len(addresses), MAX_BATCH):
            batch = addresses[i : i + MAX_BATCH]
            keys = [int(a, 16) for a in batch]
            reply = await self._data_request(
                proto.T_GET, 0x01, 0x00, proto.get_values_data(keys)
            )
            if reply is None:
                raise Is3ConnectionError(
                    f"Reading {len(keys)} addresses was refused or unanswered "
                    f"({batch[0]}..{batch[-1]}, batch {i // MAX_BATCH + 1} of "
                    f"{(len(addresses) + MAX_BATCH - 1) // MAX_BATCH})"
                )
            try:
                values = proto.parse_values(reply, keys)
            except ValueError as err:
                raise Is3ConnectionError(f"Malformed read reply: {err}") from err
            for addr, key in zip(batch, keys):
                result[addr] = values.get(key)
        return result

    async def async_set(self, address: str, value: int) -> None:
        """Write a value to one address."""
        await self.async_set_many([(address, value)])

    async def async_set_many(self, writes: list[tuple[str, int]]) -> None:
        """Write several addresses in a single packet.

        The unit applies the whole packet or none of it, which matters wherever
        two outputs have to move together -- releasing both directions of a
        blind, say, where landing one write and losing the other would leave
        the motor running.
        """
        pairs = [(int(address, 16), value) for address, value in writes]
        reply = await self._data_request(
            proto.T_SET, 0x01, 0x00, proto.set_value_data(pairs)
        )
        if reply is None:
            written = ", ".join(address for address, _ in writes)
            raise Is3ConnectionError(f"Write to {written} was not acknowledged")

    # ---- what the unit says about itself -----------------------------------

    async def async_project_digest(self) -> bytes | None:
        """The digest of the project loaded in the unit, or None if unavailable.

        Only the digest is read -- the rest of that reply carries the
        installation's own name, which this integration has no reason to touch.
        """
        reply = await self._data_request(proto.T_GET, 0x03, 0x00, b"")
        return proto.parse_project_digest(reply) if reply is not None else None

    async def async_password_required(self) -> bool | None:
        """Whether the unit has a password set.  Answered without authorizing."""
        reply = await self._send_once(proto.T_STARTSTOP, 0x06, 0x00, auth=False)
        return proto.parse_password_required(reply) if reply is not None else None

    async def async_unit_state(self) -> proto.UnitState | None:
        """The unit's run state and its own clock.  Needs no authorization."""
        reply = await self._send_once(proto.T_STARTSTOP, 0x03, 0x00, auth=False)
        return proto.parse_unit_state(reply) if reply is not None else None

    # ---- session -----------------------------------------------------------

    async def _open(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            self._transport, _ = await asyncio.wait_for(
                loop.create_datagram_endpoint(
                    lambda: _Protocol(self), remote_addr=(self.host, self.port)
                ),
                CONNECT_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise Is3ConnectionError(f"Cannot reach {self.host}:{self.port}") from err

    async def _authorize(self) -> str:
        """Run the connect handshake and store the session token.

        Returns ``"ok"``, ``"refused"`` or ``"silent"``, and the last two are
        not the same thing.  A refusal is the unit saying no, which for this
        request means the password.  Silence is the unit not answering a
        question it answers when it is well -- seen on a unit that replied to
        "what state are you in" and to nothing else, having been left in some
        half-started state.  Reporting that as a refused password sent a whole
        evening looking for a password that did not exist.

        The old token is deliberately **not** cleared first.  Every handshake
        send below passes ``auth=False`` and so carries an explicit zero token
        anyway, while blanking the live one would hand that zero to any other
        caller that takes the lock mid-handshake -- which the unit ignores in
        silence, costing it a full round of timeouts and a reauth of its own.
        Leaving the old token in place loses nothing: a stale one is ignored
        exactly the same way, and if it turns out to still be good, that caller
        simply succeeds.
        """
        # The three replies before authorization are the unit describing
        # itself, and they come back whether or not the sign-in will.  Kept raw
        # as a firmware fingerprint: protocol-info differs by firmware (its
        # length most visibly), and the run state's first byte is the run mode.
        # Captured here so a bug report carries what otherwise needs the probe.
        state = await self._send_once(proto.T_STARTSTOP, 0x03, 0x00, auth=False)
        info = await self._send_once(
            proto.T_UNITINFO, 0x03, 0x02, proto.UNIT_INFO_DATA, auth=False
        )
        protocol_info = await self._send_once(proto.T_STARTSTOP, 0x05, 0x00, auth=False)
        self.fingerprint = {
            "run_state": state.data.hex() if state else None,
            "unit_info": info.data.hex() if info else None,
            "protocol_info": protocol_info.data.hex() if protocol_info else None,
        }
        reply = await self._send_once(
            proto.T_STARTSTOP,
            0x06,
            0x01,
            proto.authorization_data(self._password, self._user),
            auth=False,
        )
        if reply is None:
            return "silent"
        token = proto.parse_token(reply)
        if token is None:
            return "refused"
        self._token = token
        return "ok"

    async def _event_start(self) -> bool:
        """Turn on the push stream; False if the unit did not acknowledge.

        Asked for over four attempts the first time -- a datagram lost on the
        way must not cost a session its events for as long as it lasts.  But
        once a unit has declined, repeating that on every reconnect is twelve
        seconds of waiting for a silence we have already been told about, so
        after the first refusal it is asked once and dropped.

        Which of the two it was is worth knowing and cannot be told apart from
        the outside: a unit that says nothing may not understand the request,
        while one that refuses it understood and said no.
        """
        reply = await self._send_once(
            proto.T_EVENT, 0x02, 0x00, retries=1 if self._events_refused else None
        )
        if reply is None:
            _LOGGER.debug("%s did not answer the request to start events", self.host)
        elif not reply.is_ack:
            _LOGGER.debug("%s refused the request to start events", self.host)
        self._events_refused = reply is None or not reply.is_ack
        return not self._events_refused

    def _start_keepalive(self) -> None:
        if self._keepalive_task is None or self._keepalive_task.done():
            self._keepalive_task = asyncio.ensure_future(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        """Poke the unit periodically; a dead link triggers a reconnect."""
        while not self._closing and self._connected:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            if self._closing or not self._connected:
                return
            reply = await self._send_once(proto.T_STARTSTOP, 0x03, 0x00, auth=False)
            if reply is None:
                _LOGGER.debug("Keepalive to %s failed; reconnecting", self.host)
                self._handle_disconnect()
                return

    # ---- request/response --------------------------------------------------

    async def _data_request(
        self, typ: int, i1: int, i2: int, data: bytes
    ) -> proto.Packet | None:
        """An authorized request, with one reauth-and-retry on failure.

        Returns an **acknowledged** reply, or None.  A refusal is never handed
        back: its body is an error, not the payload the caller is about to
        parse, and letting one through is how a NACK becomes a relay that reads
        as switched on.

        The token can expire; rather than track its lifetime, a NACK or a run of
        timeouts is taken as "token stale", the session is reauthorized once, and
        the request retried.
        """
        reply = await self._send_once(typ, i1, i2, data, auth=True)
        if reply is not None and reply.is_ack:
            return reply
        if self._closing:
            return None
        if await self._authorize() == "ok":
            retry = await self._send_once(typ, i1, i2, data, auth=True)
            return retry if retry is not None and retry.is_ack else None
        self._handle_disconnect()
        return None

    async def _send_once(
        self,
        typ: int,
        i1: int,
        i2: int,
        data: bytes = b"",
        *,
        auth: bool = True,
        retries: int | None = None,
    ) -> proto.Packet | None:
        """Send one request and await its matching reply, resending on loss."""
        if self._transport is None:
            return None
        async with self._lock:
            loop = asyncio.get_running_loop()
            token = self._token if auth else b"\x00" * 8
            for _ in range(retries if retries is not None else REQUEST_RETRIES):
                # Awaiting a reply yields the loop, and both the keepalive and a
                # config-entry unload drop the transport without taking the
                # lock -- so it can vanish between two attempts of a request
                # that is holding it.  Re-read it each pass and send through
                # that snapshot, or the next attempt raises AttributeError into
                # whichever service call started this.
                transport = self._transport
                if transport is None or self._closing:
                    return None
                self._pid = (self._pid + 2) & 0xFFFFFFFF
                pid = self._pid
                packet = proto.build(typ, i1, i2, data, token=token, packet_id=pid)
                future: asyncio.Future[proto.Packet] = loop.create_future()
                self._pending = ((typ, i1, i2), pid, future)
                try:
                    transport.sendto(packet)
                except OSError as err:
                    _LOGGER.debug("Send to %s failed: %s", self.host, err)
                    self._pending = None
                    return None
                try:
                    return await asyncio.wait_for(future, self._request_timeout)
                except asyncio.TimeoutError:
                    continue
                finally:
                    self._pending = None
            return None

    def _on_datagram(self, raw: bytes) -> None:
        """Route a datagram to the event callback or the waiting request."""
        # Anything that does not open with the protocol version is not this
        # protocol -- a stray or spoofed datagram.  Checking eight bytes first
        # rejects it for a fraction of what verifying the checksum costs.
        if raw[:8] != proto.PROTOCOL_VERSION:
            # Said once, and loudly, because there is one way this happens that
            # is not noise: a firmware update changing what the unit speaks.
            # Silently dropping those would leave an integration that connects,
            # reports no error, and never sees a reply again.
            if not self._warned_foreign:
                self._warned_foreign = True
                _LOGGER.warning(
                    "%s is sending datagrams this version does not recognise "
                    "(they begin %s, not %s). If the unit's firmware was just "
                    "updated, this integration may need updating too",
                    self.host,
                    raw[:8].hex(),
                    proto.PROTOCOL_VERSION.hex(),
                )
            return
        pkt = proto.parse(raw)
        if pkt is None:
            return
        if not pkt.crc_ok:
            # UDP's own checksum is only 16 bits and optional; without this a
            # corrupted reply is parsed as values and a corrupted push is
            # written into entity state as though the unit had said it.
            self.corrupt_datagrams += 1
            _LOGGER.debug("Dropping a corrupt datagram from %s", self.host)
            return
        # Unsolicited push (EventValue 04/01/00).
        if pkt.type == proto.T_EVENT and pkt.instr1 == 0x01:
            if self._on_event is not None:
                for address, value in proto.iter_events(pkt.data):
                    try:
                        self._on_event(address, value)
                    except Exception:  # noqa: BLE001 - one entity must not
                        # swallow the rest of the addresses in this datagram.
                        _LOGGER.exception(
                            "Event callback failed for %#010x", address
                        )
            return
        pending = self._pending
        if pending is None:
            return
        expect_tis, expect_pid, future = pending
        if (
            pkt.tis == expect_tis
            and pkt.packet_id in (expect_pid, expect_pid + 1)
            and not future.done()
        ):
            future.set_result(pkt)

    # ---- disconnect / reconnect -------------------------------------------

    def _on_link_lost(self, exc: Exception) -> None:
        if not self._closing:
            _LOGGER.debug("Link to %s lost: %s", self.host, exc)
            self._handle_disconnect()

    def _handle_disconnect(self) -> None:
        """Mark the session down and start trying to get it back."""
        self._connected = False
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        if not self._closing and (
            self._reconnect_task is None or self._reconnect_task.done()
        ):
            self._reconnect_task = asyncio.ensure_future(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Reopen and reauthorize until back up or closing."""
        while not self._closing:
            await asyncio.sleep(RECONNECT_DELAY)
            if self._closing:
                return
            try:
                await self._open()
                if await self._authorize() != "ok":
                    await self._teardown()
                    continue
                # Asked for again on every reconnect: a unit that would not
                # turn the stream on before may well now, and if it still
                # will not, the session is worth having either way.
                self.events_started = await self._event_start()
            except Is3ConnectionError:
                continue
            self._connected = True
            self._start_keepalive()
            _LOGGER.info("Reconnected to %s", self.host)
            if self._on_reconnect is not None:
                self._on_reconnect()
            return

    async def _teardown(self) -> None:
        self._connected = False
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._pending = None
