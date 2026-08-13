"""The wire protocol iNELS CU3 units speak on UDP 9999.

It has a name of its own, which this project has no reason to use: what matters
here is the port, which is the one the unit's configuration software connects
to, and the layout below, which was recovered by watching that traffic.

Pure framing: build and parse packets, and the request/response bodies the
client needs.  No I/O here.  Big-endian throughout except the CRC (little-endian,
last two bytes); see :mod:`checksum`.

Header (82 bytes):

    off len field
      0   8  ProtocolVersion = 81 63 1F 55 DB 18 2A AB   (CU3 constant)
      8  56  reserved (zero)
     64   8  token (0 = unauthenticated)
     72   2  total packet length (incl. version and CRC)
     74   4  packet id (client increments; unit adopts it; reply echoes id+1)
     78   1  address: 0x01 PC->CU;  reply 0x02 ACK / 0x82 NACK
     79   1  type
     80   1  instruction 1
     81   1  instruction 2
     82   N  data
     -2   2  CRC (little-endian)
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from . import checksum as _crc

PROTOCOL_VERSION = bytes.fromhex("81631F55DB182AAB")
HEADER_LEN = 82

# header addresses
ADDR_PC = 0x01
ADDR_CU_ACK = 0x02
ADDR_CU_NACK = 0x82

# packet types
T_GET = 0x01
T_SET = 0x02
T_MONITOR = 0x03
T_EVENT = 0x04
T_STARTSTOP = 0x40
T_UNITINFO = 0x70

# user levels (ELKOep.Types.UserType) -- Admin (1) unlocks the data plane
USER_ADMIN = 0x01
USER_USER = 0x02

# Values at or above this mark "no valid value" (sensor error / unknown /
# out-of-range).  The unit uses the top of the signed int32 range for these;
# they mirror the ASCII interface's ``N`` / ``???`` and read back as None.
NO_VALUE_THRESHOLD = 0x7FFFFFF0


def build(
    typ: int,
    instr1: int,
    instr2: int,
    data: bytes = b"",
    *,
    token: bytes = b"\x00" * 8,
    packet_id: int = 0,
    address: int = ADDR_PC,
) -> bytes:
    """Assemble one packet, CRC included."""
    total = HEADER_LEN + len(data) + 2
    body = (
        PROTOCOL_VERSION
        + b"\x00" * 56
        + token
        + struct.pack(">H", total)
        + struct.pack(">I", packet_id & 0xFFFFFFFF)
        + bytes((address & 0xFF, typ & 0xFF, instr1 & 0xFF, instr2 & 0xFF))
        + data
    )
    return body + _crc.crc_bytes(body)


@dataclass(slots=True)
class Packet:
    """A parsed packet."""

    token: bytes
    length: int
    packet_id: int
    address: int
    type: int
    instr1: int
    instr2: int
    data: bytes
    crc_ok: bool

    @property
    def is_ack(self) -> bool:
        return not (self.address & 0x80)

    @property
    def is_nack(self) -> bool:
        return bool(self.address & 0x80)

    @property
    def tis(self) -> tuple[int, int, int]:
        return (self.type, self.instr1, self.instr2)


def parse(pkt: bytes) -> Packet | None:
    """Parse a packet, or None if it is too short to be one."""
    if len(pkt) < HEADER_LEN + 2:
        return None
    return Packet(
        token=pkt[64:72],
        length=struct.unpack(">H", pkt[72:74])[0],
        packet_id=struct.unpack(">I", pkt[74:78])[0],
        address=pkt[78],
        type=pkt[79],
        instr1=pkt[80],
        instr2=pkt[81],
        data=pkt[82:-2],
        crc_ok=_crc.crc_ok(pkt),
    )


# ---- request builders (data bodies) -------------------------------------

def sha1_password(password: str) -> bytes:
    """SHA-1 of the CU password, as the 20 bytes authorization expects."""
    return hashlib.sha1(password.encode("utf-8")).digest()


def authorization_data(password: str, user: int = USER_ADMIN) -> bytes:
    """Body of GetElanAuthorization (40/06/01): user(1) + SHA1(password)(20)."""
    return bytes((user & 0xFF,)) + sha1_password(password)


def get_values_data(keys: list[int]) -> bytes:
    """Body of GetValue (01/01/00): count(1) + key(4)*N."""
    return bytes((len(keys),)) + b"".join(struct.pack(">I", k) for k in keys)


def set_value_data(pairs: list[tuple[int, int]]) -> bytes:
    """Body of SetValue (02/01/00): count(1) + [key(4) + value(int32)]*N."""
    out = bytearray((len(pairs),))
    for key, value in pairs:
        out += struct.pack(">I", key) + struct.pack(">i", value)
    return bytes(out)


UNIT_INFO_DATA = bytes.fromhex("000000000000000002")  # UnitInfo 70/03/02 body


# ---- response parsers ----------------------------------------------------

def parse_token(pkt: Packet) -> bytes | None:
    """The 8-byte session token from an authorization reply."""
    if pkt.is_nack or len(pkt.data) < 8:
        return None
    return pkt.data[:8]


def clean_value(raw: int) -> int | None:
    """Map a raw int32 to a value, or None for the unit's no-value markers."""
    return None if (raw & 0xFFFFFFFF) >= NO_VALUE_THRESHOLD else raw


def parse_values(pkt: Packet, keys: list[int]) -> dict[int, int | None]:
    """GetValue reply: count(1) + value(int32 BE)*N, paired back with ``keys``.

    The reply echoes no addresses, so a value is matched to the key at the same
    position.  That only holds if the unit answered every key exactly once: a
    reply one short would shift every value after the gap onto the wrong
    address, and dozens of entities would show another device's state with
    nothing logged.  A reply that does not line up is therefore refused
    outright rather than partially believed.
    """
    data = pkt.data
    if not data or data[0] != len(keys):
        raise ValueError(
            f"Reply covers {data[0] if data else 0} of {len(keys)} addresses"
        )
    out: dict[int, int | None] = {}
    for i in range(len(keys)):
        chunk = data[1 + 4 * i : 5 + 4 * i]
        if len(chunk) < 4:
            raise ValueError("Reply ended mid-value")
        out[keys[i]] = clean_value(struct.unpack(">i", chunk)[0])
    return out


# --- Side channels: what the unit will tell you about itself ----------------

# The project-hash reply opens with digests and ends with the installation's own
# name.  Only the leading digest is ever taken, and the rest is not looked at:
# the point is to notice a change, not to learn anything about the site.
PROJECT_DIGEST_LEN = 16

# The run state the unit reports for itself.
UNIT_STATES = {0x00: "stopped", 0x10: "running", 0x20: "running_fast"}


def parse_project_digest(pkt: Packet) -> bytes | None:
    """The digest identifying the project loaded in the unit.

    It changes when the installer republishes from IDM3, which is the only
    thing that changes the device list -- so comparing it is a cheap way to
    know the export is worth fetching again.
    """
    if pkt.is_nack or len(pkt.data) < PROJECT_DIGEST_LEN:
        return None
    return pkt.data[:PROJECT_DIGEST_LEN]


def parse_password_required(pkt: Packet) -> bool | None:
    """Whether the unit has a password set, from a GetUserInfo reply.

    ``None`` means the unit did not say, and that now covers every value but
    zero.  Reading this as a plain yes/no was wrong: a unit certain to have no
    password answered ``0x03``, which under that reading told its owner to go
    and find the password it does not have.  Zero has meant "no password" on
    every unit checked; what the other values mean is not known, and guessing
    was worse than admitting it -- the caller falls back to reporting what
    actually happened rather than what this byte implied.
    """
    if pkt.is_nack or not pkt.data:
        return None
    return False if pkt.data[0] == 0 else None


@dataclass(slots=True)
class UnitState:
    """What the unit says about itself: how it is running, and its clock."""

    state: str
    """One of ``stopped`` / ``running`` / ``running_fast``, or ``unknown``."""

    clock: str | None
    """The unit's own date and time, ISO-formatted, or None if unreadable."""


def parse_unit_state(pkt: Packet) -> UnitState | None:
    """Decode a GetStateCU reply.

    Layout, recovered by comparing captures taken at known times::

        0     run state
        5-7   hour, minute, second
        8-9   day, month
        10-11 year (big-endian)

    The clock matters because the unit runs its own heating schedules off it,
    so a unit an hour out does the right thing at the wrong time.
    """
    data = pkt.data
    if pkt.is_nack or not data:
        return None

    state = UNIT_STATES.get(data[0], "unknown")
    clock: str | None = None
    if len(data) >= 12:
        hour, minute, second, day, month = data[5], data[6], data[7], data[8], data[9]
        year = struct.unpack(">H", data[10:12])[0]
        if 1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60:
            clock = (
                f"{year:04d}-{month:02d}-{day:02d}T"
                f"{hour:02d}:{minute:02d}:{min(second, 59):02d}"
            )
    return UnitState(state=state, clock=clock)


def iter_events(data: bytes):
    """Yield (address, value|None) pairs from an EventValue push body.

    Payload: count(1) + [address(4) + value(int32 BE)]*N.
    """
    if not data:
        return
    count = data[0]
    for i in range(count):
        off = 1 + 8 * i
        if off + 8 > len(data):
            return
        address = struct.unpack(">I", data[off : off + 4])[0]
        value = struct.unpack(">i", data[off + 4 : off + 8])[0]
        yield address, clean_value(value)
