"""Checksum for the protocol a CU3 unit speaks on UDP 9999.

The unit's checksum is **not** a catalogue CRC-16.  It is a linear (affine)
function over GF(2), recovered by measuring a unit's responses and verified
against captured packets and live CU3 traffic::

    CRC(M) = K(len(M)) XOR fold(M)
    fold:  reg = 0;  for b in M:  reg = T*reg XOR (reverse8(b) << 8)

The 16-bit result is stored little-endian as the packet's last two bytes; every
other field is big-endian.

The calibrated model is three constants, so it is written here as code rather
than loaded from a data file.  That is deliberate: Home Assistant runs this in
the event loop, and reading a file at import time is a blocking call.  The
calibration that produced these numbers is a one-off and lives in the research
notes, not in the integration.
"""

from __future__ import annotations

import struct
from functools import lru_cache

# Rows of the 16x16 GF(2) state-transition matrix, each row a 16-bit mask.
_T: tuple[int, ...] = (
    418, 580, 1160, 2064, 4145, 8290, 16580, 32904,
    17, 51, 102, 204, 152, 48, 96, 209,
)

# K(length) at the lengths that were calibrated directly; any other length is
# extrapolated with K(L+1) = T*K(L) XOR C.
_K: dict[int, int] = {82: 56415, 90: 17892, 245: 8734}
_C = 0

_REV8 = [int(f"{i:08b}"[::-1], 2) for i in range(256)]


def _apply_T(x: int) -> int:
    """Multiply the 16-bit state by T over GF(2)."""
    out = 0
    for r in range(16):
        if bin(_T[r] & x).count("1") & 1:
            out |= 1 << r
    return out


def _fold(data: bytes) -> int:
    """Run the message through the state machine."""
    reg = 0
    for b in data:
        reg = _apply_T(reg) ^ ((_REV8[b] << 8) & 0xFFFF)
    return reg


def _invert_T() -> list[int]:
    """Gauss-Jordan inverse of T, for lengths below the calibrated ones."""
    rows = [((_T[r] & 0xFFFF) << 16) | (1 << r) for r in range(16)]  # [T | I]
    for col in range(16):
        pivot = next((r for r in range(col, 16) if (rows[r] >> (16 + col)) & 1), None)
        if pivot is None:  # pragma: no cover - T is fixed and invertible
            raise ValueError("T is not invertible")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(16):
            if r != col and (rows[r] >> (16 + col)) & 1:
                rows[r] ^= rows[col]
    return [rows[r] & 0xFFFF for r in range(16)]


def _apply(x: int, matrix: list[int]) -> int:
    """Multiply by an arbitrary matrix (used for T inverse)."""
    out = 0
    for r in range(16):
        if bin(matrix[r] & x).count("1") & 1:
            out |= 1 << r
    return out


@lru_cache(maxsize=512)
def _k_of_len(length: int) -> int:
    """K(length): from the table, or stepped to it from the nearest known one."""
    if length in _K:
        return _K[length]

    lower = [k for k in _K if k <= length]
    if lower:
        known = max(lower)
        reg = _K[known]
        for _ in range(length - known):
            reg = _apply_T(reg) ^ _C
        return reg

    known = min(_K)  # shorter than anything calibrated: step down through T^-1
    reg = _K[known]
    inverse = _invert_T()
    for _ in range(known - length):
        reg = _apply(reg ^ _C, inverse)
    return reg


def crc(body_without_crc: bytes) -> int:
    """The 16-bit checksum of a packet body (everything but the last two bytes)."""
    return _k_of_len(len(body_without_crc)) ^ _fold(body_without_crc)


def crc_bytes(body_without_crc: bytes) -> bytes:
    """The checksum as the two little-endian bytes appended to a packet."""
    return struct.pack("<H", crc(body_without_crc))


def crc_ok(packet: bytes) -> bool:
    """Whether a full packet's trailing checksum matches its body."""
    if len(packet) < 2:
        return False
    return crc(packet[:-2]) == struct.unpack("<H", packet[-2:])[0]
