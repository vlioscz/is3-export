"""Noticing when a unit's firmware stops speaking what this version expects.

The protocol was recovered by observation, so a firmware update can change it
with no warning at all.  Two things guard against that being a silent death: the
client says something the first time a unit sends bytes it does not recognise,
and ``tools/compat_check.py`` records a fingerprint that can be compared across
an update.
"""

from __future__ import annotations

import importlib.util
import logging
import struct
from pathlib import Path

from custom_components.is3_export import checksum as crc
from custom_components.is3_export import protocol as proto
from custom_components.is3_export.client import Is3Client


def _load_tool():
    """Import the compatibility tool, which lives outside the package."""
    path = Path(__file__).parent.parent / "tools" / "compat_check.py"
    spec = importlib.util.spec_from_file_location("compat_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the client notices a unit that changed its language --------------------


def _packet(version: bytes) -> bytes:
    """A datagram opening with the given protocol version."""
    body = (
        version
        + b"\x00" * 56
        + b"\x00" * 8
        + struct.pack(">H", proto.HEADER_LEN + 2)
        + struct.pack(">I", 1)
        + bytes((proto.ADDR_CU_ACK, proto.T_STARTSTOP, 0x03, 0x00))
    )
    return body + crc.crc_bytes(body)


def test_a_unit_speaking_something_else_is_reported(caplog) -> None:
    """Silently dropping those would leave an integration that connects, shows
    no error, and never sees a reply again -- the worst way to fail."""
    client = Is3Client("unit.example", password="")

    with caplog.at_level(logging.WARNING):
        client._on_datagram(_packet(b"\x99" * 8))

    assert "does not recognise" in caplog.text
    assert "9999999999999999" in caplog.text, "the observed bytes are quoted"
    assert proto.PROTOCOL_VERSION.hex() in caplog.text, "and what was expected"


def test_it_is_said_once_not_per_datagram(caplog) -> None:
    """A unit sending these sends them constantly; one warning is the point."""
    client = Is3Client("unit.example", password="")

    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            client._on_datagram(_packet(b"\x99" * 8))

    assert caplog.text.count("does not recognise") == 1


def test_a_normal_datagram_says_nothing(caplog) -> None:
    client = Is3Client("unit.example", password="")

    with caplog.at_level(logging.WARNING):
        client._on_datagram(_packet(proto.PROTOCOL_VERSION))

    assert "does not recognise" not in caplog.text


# --- the fingerprint comparison ---------------------------------------------


BEFORE = {
    "protocol_version": "81631f55db182aab",
    "checksum_ok": True,
    "header_len": 82,
    "read_shape": "count=3 bytes=13 expected=13",
    "idm3_version": "03-04-19",
    "classified": 313,
}


def test_an_unchanged_unit_reports_nothing(capsys) -> None:
    tool = _load_tool()

    assert tool.compare(BEFORE, dict(BEFORE)) == 0
    assert "Nothing this integration relies on has changed" in capsys.readouterr().out


def test_a_changed_assumption_is_named_and_explained(capsys) -> None:
    """The point is not the bytes; it is knowing what each change costs."""
    tool = _load_tool()
    after = dict(BEFORE, protocol_version="deadbeefdeadbeef")

    assert tool.compare(BEFORE, after) == 1

    out = capsys.readouterr().out
    assert "protocol_version" in out
    assert "81631f55db182aab" in out and "deadbeefdeadbeef" in out
    assert "nothing works at all" in out, "the consequence is spelled out"


def test_every_recorded_key_has_a_meaning(capsys) -> None:
    """A fingerprint entry nobody can interpret is noise in a diff."""
    tool = _load_tool()
    recorded = set(BEFORE)

    assert recorded <= set(tool.MEANING), (
        f"no explanation for: {sorted(recorded - set(tool.MEANING))}"
    )


def test_a_firmware_version_change_alone_is_reported(capsys) -> None:
    """Even when nothing else moved -- it is the label for 'what this unit runs'."""
    tool = _load_tool()

    assert tool.compare(BEFORE, dict(BEFORE, idm3_version="03-05-03")) == 1
    assert "03-05-03" in capsys.readouterr().out
