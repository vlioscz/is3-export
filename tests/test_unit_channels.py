"""What the unit will tell you about itself, beyond the values it holds.

Three side channels, all cheap and none of them tied to an address: the digest
of the project it is running, whether it has a password set, and how it is
running plus its own clock.
"""

from __future__ import annotations

import asyncio
import struct

from custom_components.is3_export import protocol as proto
from custom_components.is3_export.coordinator import Is3Coordinator
from custom_components.is3_export.export import Is3Entry, Is3Export, expected_entities


def _packet(data: bytes, address: int = proto.ADDR_CU_ACK) -> proto.Packet:
    return proto.Packet(b"", 0, 0, address, proto.T_GET, 3, 0, data, True)


# --- the project digest -----------------------------------------------------


def test_only_the_digest_is_taken_from_the_project_reply() -> None:
    """The rest of that reply carries the installation's name, so it is not read."""
    digest = bytes(range(16))
    reply = _packet(digest + b"My House" + b"\x00" * 40)

    assert proto.parse_project_digest(reply) == digest
    assert len(proto.parse_project_digest(reply)) == proto.PROJECT_DIGEST_LEN


def test_a_refused_project_reply_has_no_digest() -> None:
    assert proto.parse_project_digest(_packet(b"", proto.ADDR_CU_NACK)) is None
    assert proto.parse_project_digest(_packet(b"short")) is None


class _DigestClient:
    """Answers with a scripted sequence of project digests."""

    def __init__(self, digests: list[bytes | None]) -> None:
        self._digests = digests
        self.calls = 0

    async def async_project_digest(self) -> bytes | None:
        digest = self._digests[min(self.calls, len(self._digests) - 1)]
        self.calls += 1
        return digest


def _coordinator(client) -> Is3Coordinator:
    coord = Is3Coordinator.__new__(Is3Coordinator)
    coord.client = client
    coord._project_digest = None
    return coord


def test_the_first_digest_is_not_a_change() -> None:
    """Nothing to compare against yet, so the export is read on its own terms."""
    coord = _coordinator(_DigestClient([b"a" * 16]))
    assert asyncio.run(coord._async_project_changed()) is False


def test_the_same_digest_means_nothing_was_republished() -> None:
    coord = _coordinator(_DigestClient([b"a" * 16, b"a" * 16]))
    asyncio.run(coord._async_project_changed())
    assert asyncio.run(coord._async_project_changed()) is False


def test_a_new_digest_reports_a_change() -> None:
    """Republishing from IDM3 is what changes the device list."""
    coord = _coordinator(_DigestClient([b"a" * 16, b"b" * 16]))
    asyncio.run(coord._async_project_changed())
    assert asyncio.run(coord._async_project_changed()) is True


def test_a_silent_unit_leaves_the_decision_to_the_timer() -> None:
    """None, not False -- the caller must not read silence as "unchanged"."""
    coord = _coordinator(_DigestClient([None]))
    assert asyncio.run(coord._async_project_changed()) is None


# --- whether the unit wants a password --------------------------------------


def test_password_required_is_read_from_the_user_info() -> None:
    assert proto.parse_password_required(_packet(b"\x01")) is True
    assert proto.parse_password_required(_packet(b"\x00")) is False
    assert proto.parse_password_required(_packet(b"")) is None
    assert proto.parse_password_required(_packet(b"\x01", proto.ADDR_CU_NACK)) is None


# --- run state and the unit's clock -----------------------------------------


def test_the_run_state_is_decoded() -> None:
    """A stopped unit still answers the network; this is what says so."""
    assert proto.parse_unit_state(_packet(b"\x20")).state == "running_fast"
    assert proto.parse_unit_state(_packet(b"\x10")).state == "running"
    assert proto.parse_unit_state(_packet(b"\x00")).state == "stopped"
    assert proto.parse_unit_state(_packet(b"\x99")).state == "unknown"


def test_the_unit_clock_is_decoded() -> None:
    """Pinned against a live reply captured at a known time."""
    captured = bytes.fromhex("20 46 02 00 28 0f 01 02 07 08 07 ea 05 00 3c".replace(" ", ""))

    state = proto.parse_unit_state(_packet(captured))

    assert state.state == "running_fast"
    assert state.clock == "2026-08-07T15:01:02"


def test_a_nonsense_clock_is_dropped_rather_than_shown() -> None:
    """A short or garbled reply must not produce a plausible-looking date."""
    assert proto.parse_unit_state(_packet(b"\x10")).clock is None
    impossible = b"\x10" + bytes([0, 0, 0, 0, 99, 99, 99, 99, 99]) + struct.pack(">H", 2026)
    assert proto.parse_unit_state(_packet(impossible)).clock is None


def test_an_empty_reply_is_no_state_at_all() -> None:
    assert proto.parse_unit_state(_packet(b"")) is None


# --- the sensor survives the registry pruning -------------------------------


def test_the_status_sensor_is_not_pruned_as_an_orphan() -> None:
    """It comes from the connection, not the export.

    Everything the integration creates has to appear in ``expected_entities``
    or startup deletes it as a leftover from an older classification -- and
    this one has no export entry to be derived from.
    """
    export = Is3Export(entries=[Is3Entry(name="Rele", address=0x0102000A, value=0)])

    assert ("sensor", "entry_unit_status") in expected_entities(export, "entry")
