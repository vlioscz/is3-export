"""Tests for command construction and line parsing.

No hardware or sockets involved. Every expected string here was observed on a
live unit, so these pin the protocol down against regressions.
"""

from __future__ import annotations

import pytest

from custom_components.is3_export.api import Is3Client, parse_line
from custom_components.is3_export.const import (
    BASE_DEC,
    DELIMITER_SEMICOLON,
    DELIMITER_SPACE,
    DELIMITERS,
)


def _client(delimiter: str = DELIMITER_SPACE) -> Is3Client:
    """A client that never connects, used only to build command bytes."""
    return Is3Client("192.168.1.10", 22272, delimiter)


def test_space_command_matches_the_working_script() -> None:
    """The space form must match telnet_loz_ON.py byte for byte.

    That script is confirmed to switch a real light.
    """
    assert _client()._command("SET", "0x0102000A", 1) == b"SET 0x0102000A 1 \r\n"


def test_semicolon_command_matches_abetka() -> None:
    """The semicolon form must match abetka/InelsHA, with no trailing space."""
    client = _client(DELIMITER_SEMICOLON)
    assert client._command("SET", "0x0102000A", 1) == b"SET;0x0102000A;1\r\n"


def test_read_command() -> None:
    """The read form the unit was observed to answer."""
    assert _client()._command("GET", "0x0102000A") == b"GET 0x0102000A \r\n"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # Replies echo the address in lower case.
        ("GET 0x0102000a 0x00000001", ("GET", 0x0102000A, 1)),
        ("GET 0x0102000a 0x00000000", ("GET", 0x0102000A, 0)),
        ("GET 0x01050017 0x000011c5", ("GET", 0x01050017, 4549)),
        # EVENT carries an extra id between the verb and the address.
        ("EVENT 15 0x01080001 0x00001770", ("EVENT", 0x01080001, 6000)),
        ("EVENT 1d 0x0205ffff 0x000003e9", ("EVENT", 0x0205FFFF, 1001)),
    ],
)
def test_parse_line(line: str, expected: tuple[str, int, int | None]) -> None:
    """Lines split into kind, address and value."""
    assert parse_line(line) == expected


def test_a_line_in_another_dialect_is_not_guessed_at() -> None:
    """A semicolon line is not parsed by a client configured for spaces.

    Being lenient here would mean silently working with the wrong delimiter
    configured, and would corrupt lines whose values contain the delimiter.
    Rejecting it surfaces the misconfiguration instead.
    """
    assert parse_line("GET;0x0102000a;0x00000001") is None
    assert parse_line("GET;0x0102000a;0x00000001", ";") == ("GET", 0x0102000A, 1)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # "Remote control + IDM" mode: an IDM field sits before the address.
        ("EVENT 15 0x01080001 0x00001770", ("EVENT", 0x01080001, 6000)),
        # "Remote control" mode: no id field at all.
        ("EVENT 0x01080001 0x00001770", ("EVENT", 0x01080001, 6000)),
    ],
)
def test_both_modes_are_understood(line: str, expected) -> None:
    """The + IDM mode adds a field, which must not shift the address."""
    assert parse_line(line) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # IDM3's number base can be switched from hexadecimal to decimal.
        ("GET 0x0102000a 0x00000001", ("GET", 0x0102000A, 1)),
        ("GET 16908298 1", ("GET", 0x0102000A, 1)),
        ("EVENT 15 16908289 4549", ("EVENT", 0x01020001, 4549)),
    ],
)
def test_both_number_bases_are_understood(line: str, expected) -> None:
    """Hexadecimal values carry an 0x prefix; decimal ones do not."""
    assert parse_line(line) == expected


@pytest.mark.parametrize("delimiter", list(DELIMITERS))
def test_every_delimiter_idm_offers_round_trips(delimiter: str) -> None:
    """Every separator the IDM3 dropdown offers must be sent and parsed.

    The dialect is chosen per installation, so all of them are real cases.
    """
    client = Is3Client("192.168.1.10", 22272, delimiter)
    sent = client._command("GET", "0x0102000A").decode("ascii").strip()

    reply = sent.replace("GET", "GET", 1) + delimiter + "0x00000001"
    assert parse_line(reply, delimiter) == ("GET", 0x0102000A, 1)


@pytest.mark.parametrize("delimiter", list(DELIMITERS))
def test_events_parse_with_every_delimiter(delimiter: str) -> None:
    """Pushed events use the same separator as replies."""
    line = delimiter.join(["EVENT", "15", "0x01080001", "0x00001770"])
    assert parse_line(line, delimiter) == ("EVENT", 0x01080001, 6000)


def test_delimiter_list_matches_idm() -> None:
    """The options offered must be exactly what IDM3's dropdown contains.

    Characters that can appear inside an address or a value -- digits, `+`,
    `-`, `.`, `%`, `x` -- are deliberately not selectable there.
    """
    assert " " in DELIMITERS
    assert ";" in DELIMITERS
    assert len(DELIMITERS) == 27
    for forbidden in "0123456789+-.%x":
        assert forbidden not in DELIMITERS
    # A tab is not on the list, however plausible it looks.
    assert "\t" not in DELIMITERS


def test_question_mark_delimiter_does_not_mangle_a_failed_sensor() -> None:
    """A failed sensor answers `???`, which collides with `?` as a delimiter.

    Splitting on every known delimiter rather than the configured one would
    corrupt this line; the address must still survive.
    """
    kind, address, value = parse_line("GET?0x0105001a????1", "?")
    assert kind == "GET"
    assert address == 0x0105001A
    assert value == 1


def test_values_are_sent_in_the_units_number_base() -> None:
    """The number base applies to values, not just addresses.

    A dimmer set to full is 100. Sent to a unit configured for hexadecimal as
    the decimal digits "100" it reads as 0x100, which is 256 and outside a
    percentage, so the dimmer never moves. Relays hide the bug, because 0 and 1
    read the same either way.
    """
    hex_client = _client()
    dec_client = Is3Client("192.168.1.10", 22272, DELIMITER_SPACE, BASE_DEC)

    assert hex_client.format_value(100) == "64"
    assert dec_client.format_value(100) == "100"

    # The values that made relays look fine.
    for both_bases in (0, 1):
        assert hex_client.format_value(both_bases) == str(both_bases)
        assert dec_client.format_value(both_bases) == str(both_bases)


def test_full_brightness_command_in_hex() -> None:
    """What a dimmer at 100 percent actually goes on the wire as."""
    client = _client()
    command = client._command(
        "SET", client.format_address("0x01040002"), client.format_value(100)
    )
    assert command == b"SET 0x01040002 64 \r\n"


def test_decimal_addresses_are_sent_in_decimal() -> None:
    """A unit set to decimal must not be sent 0x-prefixed addresses."""
    hex_client = _client()
    dec_client = Is3Client("192.168.1.10", 22272, DELIMITER_SPACE, BASE_DEC)
    assert hex_client.format_address("0x0102000A") == "0x0102000A"
    assert dec_client.format_address("0x0102000A") == "16908298"
    assert dec_client._command("SET", dec_client.format_address("0x0102000A"), 1) == (
        b"SET 16908298 1 \r\n"
    )


@pytest.mark.parametrize(
    "line",
    [
        "GET 0x05010002 N",  # heating plan slot
        "GET 0x0003001b N",  # controller schedule
        "GET 0x02040003 N",  # scene
    ],
)
def test_unreadable_addresses_report_no_value(line: str) -> None:
    """A literal N means the address has no value -- it must not read as zero."""
    kind, _, value = parse_line(line)
    assert kind == "GET"
    assert value is None


def test_failed_sensor_reports_no_value() -> None:
    """A broken sensor answers with question marks, not a number."""
    _, address, value = parse_line("GET 0x0105001a ???1")
    assert address == 0x0105001A
    assert value is None


@pytest.mark.parametrize(
    "line", ["", "   ", "garbage", "HELLO 0x01020003", "GET", "GET notanaddress 1"]
)
def test_unparseable_lines_are_rejected(line: str) -> None:
    """Anything that is not a recognisable line yields None."""
    assert parse_line(line) is None


def test_event_and_reply_for_the_same_address_are_distinguished() -> None:
    """An EVENT must never be mistaken for the answer to a GET.

    Replies arrive interleaved with pushed events, so the kind is what tells
    them apart.
    """
    reply = parse_line("GET 0x01080001 0x00001770")
    event = parse_line("EVENT 15 0x01080001 0x00001770")
    assert reply[0] == "GET"
    assert event[0] == "EVENT"
    assert reply[1] == event[1] == 0x01080001
