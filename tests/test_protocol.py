"""The wire protocol on UDP 9999, and the async client that speaks it.

Framing and checksum are pinned against a packet captured from a real unit, so a
change to either is caught here rather than on someone's install.  The client is
driven against a fake transport: UDP correlation, batch reads, reauth-and-retry
and event dispatch, none of which need a unit to exercise.
"""

from __future__ import annotations

import asyncio
import struct

import pytest

from custom_components.is3_export import checksum as crc
from custom_components.is3_export import protocol as proto
from custom_components.is3_export.client import Is3Client
from custom_components.is3_export.errors import Is3AuthError, Is3ConnectionError

# A GetState reply captured from a live CU, verbatim.  It is the ground truth
# for the header layout and for the checksum model.
REAL_GETSTATE_REPLY = bytes.fromhex(
    "81631f55db182aab" + "00" * 56 + "0000000000000000"
    "0063" "00000001" "02" "40" "03" "00"
    "2046020028153a15060807ea04003c" "d713"
)


# --- framing and checksum ---------------------------------------------------


def test_crc_roundtrip() -> None:
    """A packet this module builds validates against its own checksum."""
    packet = proto.build(proto.T_STARTSTOP, 0x03, 0x00, packet_id=4)
    assert crc.crc_ok(packet)

    parsed = proto.parse(packet)
    assert parsed.tis == (proto.T_STARTSTOP, 0x03, 0x00)
    assert parsed.packet_id == 4
    assert parsed.address == proto.ADDR_PC
    assert parsed.crc_ok


def test_header_offsets() -> None:
    """Every header field sits where the unit expects it."""
    packet = proto.build(
        proto.T_GET, 0x01, 0x00, b"\x01\x02\x03", token=b"AB" * 4, packet_id=6
    )
    assert packet[0:8] == proto.PROTOCOL_VERSION
    assert packet[64:72] == b"AB" * 4
    assert struct.unpack(">H", packet[72:74])[0] == len(packet)
    assert struct.unpack(">I", packet[74:78])[0] == 6
    assert packet[78:82] == bytes((proto.ADDR_PC, proto.T_GET, 0x01, 0x00))


def test_crc_matches_a_real_packet() -> None:
    """The checksum model must validate genuine traffic, not just our own."""
    assert crc.crc_ok(REAL_GETSTATE_REPLY)

    packet = proto.parse(REAL_GETSTATE_REPLY)
    assert packet.tis == (0x40, 0x03, 0x00)
    assert packet.is_ack
    assert packet.data[0] == 0x20  # FastRun


@pytest.mark.parametrize("length", [0, 1, 40, 81, 82, 90, 200, 245, 400])
def test_crc_extrapolates_to_any_length(length: int) -> None:
    """Only three lengths were calibrated; the rest are stepped to from those."""
    body = b"\x5a" * length
    assert crc.crc_ok(body + struct.pack("<H", crc.crc(body)))


def test_crc_needs_no_data_file() -> None:
    """The model is code, not a file read at import (that would block the loop)."""
    assert crc.crc(b"") == crc._k_of_len(0)


# --- request bodies and reply parsing ---------------------------------------


def test_authorization_body() -> None:
    """Authorization is the user level followed by SHA-1 of the password."""
    body = proto.authorization_data("", proto.USER_ADMIN)
    assert body[0] == 0x01  # Admin: the level that opens the data plane
    assert body[1:] == bytes.fromhex("da39a3ee5e6b4b0d3255bfef95601890afd80709")


def test_get_and_set_bodies() -> None:
    """Both bodies are a count followed by fixed-width records."""
    assert proto.get_values_data([0x01020001, 0x01040002]) == bytes.fromhex(
        "02" "01020001" "01040002"
    )
    assert proto.set_value_data([(0x01020009, 1)]) == bytes.fromhex(
        "01" "01020009" "00000001"
    )
    # Values are signed, so a negative one is not mangled into a huge unsigned.
    assert proto.set_value_data([(0x02020001, -5)])[5:] == struct.pack(">i", -5)


def test_no_value_markers_read_as_none() -> None:
    """A failed sensor reports the top of the int32 range, not a temperature."""
    keys = [0x01050001, 0x01050002, 0x01020001]
    data = (
        b"\x03"
        + struct.pack(">i", 2806)
        + struct.pack(">I", 0x7FFFFFFC)
        + struct.pack(">i", 1)
    )
    packet = proto.Packet(
        b"", 0, 0, proto.ADDR_CU_ACK, proto.T_GET, 1, 0, data, True
    )

    values = proto.parse_values(packet, keys)
    assert values[0x01050001] == 2806
    assert values[0x01050002] is None
    assert values[0x01020001] == 1


def test_event_payload_is_address_value_pairs() -> None:
    """A push carries its own addresses, so it is decoded without a request."""
    data = (
        b"\x02"
        + struct.pack(">I", 0x01050015)
        + struct.pack(">i", 1394)
        + struct.pack(">I", 0x01080001)
        + struct.pack(">I", 0x7FFFFFFF)
    )
    assert list(proto.iter_events(data)) == [(0x01050015, 1394), (0x01080001, None)]


# --- the client, against a fake transport -----------------------------------


class FakeTransport(asyncio.DatagramTransport):
    """Captures what the client sends and scripts the unit's replies."""

    def __init__(self, client: Is3Client) -> None:
        self.client = client
        self.sent: list[bytes] = []
        self.responder = None  # callable(parsed_request) -> bytes | None
        self.closed = False

    def sendto(self, data: bytes, addr: object = None) -> None:
        self.sent.append(data)
        if self.responder is not None:
            reply = self.responder(proto.parse(data))
            if reply is not None:
                self.client._on_datagram(reply)

    def close(self) -> None:
        self.closed = True


def _reply(request, data: bytes, address: int = proto.ADDR_CU_ACK) -> bytes:
    """A reply echoing the request's type and instruction, with id + 1."""
    body = (
        proto.PROTOCOL_VERSION
        + b"\x00" * 56
        + b"\x00" * 8
        + struct.pack(">H", proto.HEADER_LEN + len(data) + 2)
        + struct.pack(">I", request.packet_id + 1)
        + bytes((address, request.type, request.instr1, request.instr2))
        + data
    )
    return body + crc.crc_bytes(body)


def _make_client(responder) -> Is3Client:
    """A client that believes it is already connected and authorized.

    The timeout is tiny because some of these tests deliberately leave a request
    unanswered, and the real one is seconds long.
    """
    client = Is3Client("test", password="", request_timeout=0.01)
    client._transport = FakeTransport(client)
    client._transport.responder = responder
    client._connected = True
    client._token = b"\x01" * 8

    async def _keep_the_fake_transport() -> None:
        """async_connect would otherwise open a real socket over the top."""

    client._open = _keep_the_fake_transport
    return client


def test_read_correlates_its_reply() -> None:
    """A value read comes back matched to the request that asked for it."""

    def responder(request):
        if request.tis == (proto.T_GET, 0x01, 0x00):
            return _reply(request, b"\x01" + struct.pack(">i", 2806))
        return None

    client = _make_client(responder)
    assert asyncio.run(client.async_get("0x01050001")) == 2806


def test_reads_split_into_batches() -> None:
    """More addresses than fit one datagram still come back as one mapping."""

    def responder(request):
        count = request.data[0]
        keys = [
            struct.unpack(">I", request.data[1 + 4 * i : 5 + 4 * i])[0]
            for i in range(count)
        ]
        body = bytes((count,)) + b"".join(struct.pack(">i", k & 0xFF) for k in keys)
        return _reply(request, body)

    client = _make_client(responder)
    addresses = [f"0x{0x01020000 + i:08X}" for i in range(50)]  # forces two batches

    values = asyncio.run(client.async_get_many(addresses))
    assert len(values) == 50
    assert values["0x01020005"] == 0x05
    assert len(client._transport.sent) == 2


def test_an_unanswered_batch_says_which_one_it_was() -> None:
    """This is the error people paste into an issue, so it has to be worth
    pasting: which addresses, and where in the read it happened."""

    def responder(request):
        return None  # the unit says nothing at all

    client = _make_client(responder)
    addresses = [f"0x{0x01020000 + i:08X}" for i in range(50)]

    with pytest.raises(Is3ConnectionError) as raised:
        asyncio.run(client.async_get_many(addresses))

    message = str(raised.value)
    assert "0x01020000" in message and "0x01020027" in message, message
    assert "batch 1 of 2" in message, message


def test_a_silent_host_is_not_reported_as_a_wrong_password() -> None:
    """A UDP socket opens against an address with nothing behind it.

    Without an unauthenticated question first, the handshake ran all four of
    its steps into silence and then reported the only failure it could tell
    apart -- a refused password -- which sends someone checking credentials
    when the unit is simply not there.
    """
    client = _make_client(lambda request: None)
    client._connected = False

    with pytest.raises(Is3ConnectionError) as raised:
        asyncio.run(client.async_connect())

    assert not isinstance(raised.value, Is3AuthError), "blamed the password"
    assert "No answer" in str(raised.value)


def test_a_unit_that_answers_but_refuses_still_blames_the_password() -> None:
    """The distinction only works if a genuine refusal still reads as one."""

    def responder(request):
        # It answers the unauthenticated question, then refuses to authorize.
        if request.tis == (proto.T_STARTSTOP, 0x03, 0x00):
            return _reply(request, b"\x20")
        if request.tis == (proto.T_STARTSTOP, 0x06, 0x01):
            return _reply(request, b"", address=proto.ADDR_CU_NACK)
        return _reply(request, b"")

    client = _make_client(responder)
    client._connected = False

    with pytest.raises(Is3AuthError):
        asyncio.run(client.async_connect())


def test_a_refused_write_raises() -> None:
    """A NACKed write is an error, not a silently dropped command."""

    def responder(request):
        if request.type == proto.T_SET:
            return _reply(request, b"", address=proto.ADDR_CU_NACK)
        return None

    client = _make_client(responder)
    # The NACK triggers one reauth; authorization is unanswered here, so the
    # session goes down and the caller is told the write did not land.
    with pytest.raises(Is3ConnectionError):
        asyncio.run(client.async_set("0x01020009", 1))


def test_a_stale_token_is_renewed_once() -> None:
    """The unit NACKs an expired token; the client reauthorizes and retries."""
    state = {"authorized": False}

    def responder(request):
        if request.tis == (proto.T_STARTSTOP, 0x06, 0x01):
            state["authorized"] = True
            return _reply(request, b"\x09" * 8)  # a fresh token
        if request.tis == (proto.T_GET, 0x01, 0x00):
            if not state["authorized"]:
                return _reply(request, b"", address=proto.ADDR_CU_NACK)
            return _reply(request, b"\x01" + struct.pack(">i", 42))
        return None

    client = _make_client(responder)
    assert asyncio.run(client.async_get("0x01050001")) == 42
    assert client._token == b"\x09" * 8


def test_events_reach_the_callback() -> None:
    """A push is dispatched without any request being in flight."""
    seen: list[tuple[int, int | None]] = []
    client = Is3Client("test", on_event=lambda a, v: seen.append((a, v)))

    data = b"\x01" + struct.pack(">I", 0x01020009) + struct.pack(">i", 1)
    client._on_datagram(_event_packet(data))

    assert seen == [(0x01020009, 1)]


def _event_packet(data: bytes) -> bytes:
    """An unsolicited EventValue push, as the unit sends it."""
    body = (
        proto.PROTOCOL_VERSION
        + b"\x00" * 56
        + b"\x00" * 8
        + struct.pack(">H", proto.HEADER_LEN + len(data) + 2)
        + struct.pack(">I", 7)
        + bytes((proto.ADDR_CU_ACK, proto.T_EVENT, 0x01, 0x00))
        + data
    )
    return body + crc.crc_bytes(body)
