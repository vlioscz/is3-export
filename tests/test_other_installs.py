"""Classification against an export from a different installation.

The first installation this integration was written against had no blind
drivers, no meters and no unnamed relays, so its export could not show whether
the classification rules generalise.  These cases come from other real sites.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.is3_export.export import (
    is_binary,
    is_controller_internal,
    PLATFORM_NUMBER,
    is_counter,
    is_dimmable,
    is_measured,
    is_number,
    is_readable,
    is_switchable,
    platform_of,
    is_writable,
    parse_export,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(name="export")
def export_fixture():
    """An export from a site with blinds, meters and unnamed relays."""
    return parse_export(
        (FIXTURES / "other_install.is3").read_text(encoding="utf-8-sig")
    )


@pytest.mark.parametrize(
    ("address", "hw_id"),
    [
        (0x0102000E, "SA3-04M_RE2_0A0001"),
        (0x0102002F, "JA3-018M_Up1_0A0002"),
        (0x01020030, "JA3-018M_Down1_0A0002"),
    ],
)
def test_unnamed_relays_are_still_outputs(export, address: int, hw_id: str) -> None:
    """An installer leaving an output unnamed must not make it read-only.

    Real relays and blind drivers are routinely left unnamed; only controller
    internals must be held back.
    """
    entry = export.by_address(address)
    assert entry is not None
    assert entry.hw_id == hw_id
    assert not entry.labelled
    assert is_switchable(entry), "an unnamed relay is still a relay"
    assert not is_binary(entry)


@pytest.mark.parametrize(
    "address",
    [
        0x0102009A,  # Controller_Window-Detector-DIN, in the relay range
        0x01040003,  # Controller_Control-Type-AOUT, in the dimmer range
    ],
)
def test_controller_internals_stay_read_only(export, address: int) -> None:
    """Controller internals share address ranges with outputs but are not ones."""
    entry = export.by_address(address)
    assert is_controller_internal(entry)
    assert not is_writable(entry)


def test_controller_internals_are_matched_by_hardware_id(export) -> None:
    """The distinction is the hardware id, not whether a name was given."""
    unnamed_relay = export.by_address(0x0102000E)
    unnamed_internal = export.by_address(0x0102009A)
    assert not unnamed_relay.labelled
    assert not unnamed_internal.labelled
    assert is_writable(unnamed_relay)
    assert not is_writable(unnamed_internal)


@pytest.mark.parametrize(
    "address",
    [
        0x01070005,  # blind driver overload
        0x0107000E,  # relay power supply failure
        0x01070001,  # dimmer over-temperature
    ],
)
def test_module_faults_are_binary_sensors(export, address: int) -> None:
    """Fault flags are worth surfacing, and must never be written to."""
    entry = export.by_address(address)
    assert is_binary(entry)
    assert not is_writable(entry)


def test_meters_are_counters(export) -> None:
    """Water and electricity meters drive long-term statistics."""
    entry = export.by_address(0x02060001)
    assert entry.name == "Vodomer_1"
    assert is_measured(entry)
    assert is_counter(entry)
    assert not is_writable(entry)


def test_system_integers_are_writable_numbers(export) -> None:
    """System integers are the programme's own variables, not just readings.

    The unit's programme branches on them, so they can be set as well as read.
    """
    entry = export.by_address(0x02020002)
    assert entry.name == "rychlost_vetru"
    assert is_number(entry)
    assert is_writable(entry)
    assert platform_of(entry) == PLATFORM_NUMBER
    assert not is_measured(entry)


def test_groups_and_plans_produce_no_entity(export) -> None:
    """Groups, plans and controller schedules answer GET with N."""
    for address in (0x02040002, 0x02090002, 0x05010001, 0x00030024):
        entry = export.by_address(address)
        assert entry is not None
        assert not is_readable(entry), f"{entry.name} should be ignored"


def test_entity_classes_stay_mutually_exclusive(export) -> None:
    """No entry may be claimed by more than one platform."""
    for entry in export.entries:
        claims = [
            is_switchable(entry),
            is_dimmable(entry),
            is_binary(entry),
            is_measured(entry),
        ]
        assert sum(claims) <= 1, f"{entry.name} claimed by {sum(claims)} platforms"
