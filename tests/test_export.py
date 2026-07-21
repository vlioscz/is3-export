"""Tests for the export file parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.is3_export.export import (
    Is3Entry,
    is_binary,
    is_dimmable,
    is_measured,
    is_number,
    is_readable,
    is_switchable,
    is_writable,
    parse_export,
    value_scale,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    """Read a fixture the way Home Assistant would read the real file."""
    return (FIXTURES / name).read_text(encoding="utf-8-sig")


@pytest.fixture(name="is3")
def is3_fixture():
    """Parsed .is3 sample."""
    return parse_export(_load("sample.is3"))


@pytest.fixture(name="imm")
def imm_fixture():
    """Parsed .imm sample."""
    return parse_export(_load("sample.imm"))


def test_header_is_parsed(is3) -> None:
    """The VERSION_ line yields the unit id used as the config entry id."""
    assert is3.header is not None
    assert is3.header.version == "01-03-03"
    assert is3.header.created == "2022-05-08-21-38-51"
    assert is3.header.idm3 == "03-03-34"
    assert is3.header.unit_id == "44444444"
    assert is3.header.name == "Test-house"


def test_imm_has_no_header(imm) -> None:
    """The .imm variant carries no metadata line."""
    assert imm.header is None


def test_header_line_is_not_an_entry(is3) -> None:
    """The header must not be mistaken for a device."""
    assert len(is3.entries) == 12
    assert all(not e.name.startswith("VERSION") for e in is3.entries)


def test_named_entry_with_hw_id(is3) -> None:
    """A labelled .is3 line keeps both the label and the hardware id."""
    entry = is3.by_address(0x01020003)
    assert entry is not None
    assert entry.name == "Rele_kuchyne"
    assert entry.hw_id == "SA3-012M_RE3_0D0001"
    assert entry.value == 0
    assert entry.unit is None


def test_unlabelled_entry_falls_back_to_hw_id(is3) -> None:
    """Entries labelled `_` are named after their hardware id instead."""
    entry = is3.by_address(0x01120005)
    assert entry is not None
    assert entry.name == "Controller_Actual-Therm-AIN_0D0005"
    assert entry.unit == "°C"
    assert entry.value == 0xD8


def test_unit_is_captured(is3) -> None:
    """A trailing non-hex token is the unit of measurement."""
    assert is3.by_address(0x01040001).unit == "%"
    assert is3.by_address(0x01080001).unit == "mV"


def test_controller_extra_fields(is3) -> None:
    """The trailing blob on controller lines is kept, not treated as a unit."""
    entry = is3.by_address(0x0003001B)
    assert entry is not None
    assert entry.value == 6
    assert entry.unit is None
    assert entry.extra == [
        "0x05",
        "0x05010002_0x00000000_0x05010002_0x00000000_0x00000000_0x00000000",
    ]


def test_imm_entry_without_hw_id(imm) -> None:
    """The .imm variant has no hardware id column."""
    entry = imm.by_address(0x01020003)
    assert entry is not None
    assert entry.name == "Rele_kuchyne"
    assert entry.hw_id is None
    assert entry.value == 0


def test_imm_entry_without_value(imm) -> None:
    """Scene entries list an address but no value."""
    entry = imm.by_address(0x02040008)
    assert entry is not None
    assert entry.name == "Byt_all_digital"
    assert entry.value is None
    assert entry.unit is None


def test_imm_short_value(imm) -> None:
    """Values are not always eight hex digits."""
    entry = imm.by_address(0x0208001B)
    assert entry is not None
    assert entry.value == 2


def test_address_decomposition(is3) -> None:
    """Address bytes split into space / data type / index."""
    entry = is3.by_address(0x01040001)
    assert entry.space == 0x01
    assert entry.data_type == 0x04
    assert entry.index == 1
    assert entry.address_hex == "0x01040001"
    assert entry.unique_id == "0x01040001"


def test_fingerprint_ignores_values(is3) -> None:
    """Re-exporting an unchanged installation must not look like a change.

    Values and the header timestamp differ on every export, so keying on them
    would reload the integration constantly.
    """
    same_devices_new_values = _load("sample.is3").replace("0x00000000", "0x00000001")
    assert parse_export(same_devices_new_values).fingerprint == is3.fingerprint


def test_fingerprint_notices_a_new_device(is3) -> None:
    """Publishing another device in IDM3 must be detected."""
    with_extra = _load("sample.is3") + "Sv_garaz SA3-06M_RE7_0D0006 0x01020070 0x0\r\n"
    assert parse_export(with_extra).fingerprint != is3.fingerprint


def test_fingerprint_notices_a_rename(is3) -> None:
    """A renamed device changes the entity, so it counts as a change."""
    renamed = _load("sample.is3").replace("Rele_kuchyne", "Rele_kuchyn")
    assert parse_export(renamed).fingerprint != is3.fingerprint


def test_bom_is_stripped() -> None:
    """A BOM left in the payload must not corrupt the first entry name."""
    export = parse_export("﻿Rele_kuchyne 0x01020003 0x00000000\r\n")
    assert export.entries[0].name == "Rele_kuchyne"


@pytest.mark.parametrize(
    "line", ["", "   ", "# comment", "garbage without address", "0x01020003"]
)
def test_unparseable_lines_are_skipped(line: str) -> None:
    """Blank, comment and malformed lines produce no entries."""
    assert parse_export(line).entries == []


@pytest.mark.parametrize(
    "address",
    [
        0x0102000A,  # Sv_loznice, driven by telnet_loz_ON.py
        0x0102000C,  # Sv_chodba_ob, driven by telnet_ch_ob_ON.py
        0x0203000B,  # mobil_imp_1, driven by telnet_mob1_ON.py
        0x0203000D,  # mobil_imp_2, driven by telnet_mob2_ON.py
    ],
)
def test_addresses_known_to_be_writable_are_switchable(address: int) -> None:
    """Addresses that working scripts SET must be classified as switches."""
    assert is_switchable(Is3Entry(name="x", address=address, value=0))


@pytest.mark.parametrize(
    "address",
    [
        0x01120005,  # Controller_Actual-Therm-AIN, a temperature input
        0x05010002,  # HEATCOOL_WEEK, a heating plan slot
        0x0003001B,  # Controller entry in the .is3 address space
    ],
)
def test_non_outputs_are_not_switchable(address: int) -> None:
    """Inputs and plan slots must never be offered as switches."""
    assert not is_switchable(Is3Entry(name="x", address=address, value=0))


def test_window_detector_is_not_a_switch() -> None:
    """An unnamed input in the relay range must never become writable.

    The real export has `_ Controller_Window-Detector-DIN_0D0005 0x0102003A`,
    which sits in the relay range but is a window sensor.
    """
    detector = Is3Entry(
        name="Controller_Window-Detector-DIN_0D0005",
        address=0x0102003A,
        hw_id="Controller_Window-Detector-DIN_0D0005",
        value=0,
        labelled=False,
    )
    assert not is_switchable(detector)
    assert is_binary(detector)


def test_controller_config_is_not_a_light() -> None:
    """An unnamed, unitless entry in the dimmer range must not be writable.

    `_ Controller_Control-Type-AOUT_0D0005 0x01040007` shares the dimmer range;
    writing a brightness there would reconfigure a thermostat.
    """
    control_type = Is3Entry(
        name="Controller_Control-Type-AOUT_0D0005",
        address=0x01040007,
        value=0,
        unit=None,
        labelled=False,
    )
    assert not is_dimmable(control_type)
    assert is_measured(control_type)


def test_labelled_flag_comes_from_the_export(is3) -> None:
    """The `_` placeholder marks an entry as unnamed."""
    assert is3.by_address(0x01020003).labelled is True
    assert is3.by_address(0x01120005).labelled is False


def test_dimmers_are_lights_not_switches() -> None:
    """Dimmer addresses drive a light, and must not also appear as switches."""
    dimmer = Is3Entry(
        name="Sv_obyvak", address=0x01040001, value=50, unit="%", labelled=True
    )
    assert is_dimmable(dimmer)
    assert not is_switchable(dimmer)
    assert not is_measured(dimmer)


def test_digital_inputs_are_read_only() -> None:
    """Inputs become binary sensors and are never written to."""
    din = Is3Entry(name="Controller_Status-DOUT", address=0x0101002F, value=0)
    assert is_binary(din)
    assert not is_switchable(din)


@pytest.mark.parametrize(
    ("address", "unit"),
    [(0x01050017, "%"), (0x01080001, "mV"), (0x01120005, "°C")],
)
def test_analog_readings_are_sensors(address: int, unit: str) -> None:
    """Humidity, analog inputs and thermostat channels are sensors."""
    assert is_measured(Is3Entry(name="x", address=address, value=1, unit=unit))


def test_entity_classes_are_mutually_exclusive(is3) -> None:
    """No entry may be claimed by more than one platform."""
    for item in is3.entries:
        claims = [
            is_switchable(item),
            is_dimmable(item),
            is_binary(item),
            is_measured(item),
        ]
        assert sum(claims) <= 1, f"{item.name} claimed by {sum(claims)} platforms"


def test_readable_covers_every_entity(is3) -> None:
    """Anything that becomes an entity must also be polled."""
    for item in is3.entries:
        if is_switchable(item) or is_dimmable(item) or is_binary(item):
            assert is_readable(item)
        elif is_measured(item):
            assert is_readable(item)


@pytest.mark.parametrize(
    ("unit", "address", "expected"),
    [
        # A thermostat channel reporting 2550 is at 25.50 degrees.
        ("°C", 0x01120005, 100),
        # Humidity is scaled the same way: 4549 is 45.49 percent.
        ("%", 0x01050017, 100),
        # A dimmer is already a plain percentage, despite sharing the unit.
        ("%", 0x01040001, 1),
        # Millivolts come back raw.
        ("mV", 0x01080001, 1),
    ],
)
def test_value_scale(unit: str, address: int, expected: int) -> None:
    """Only temperatures and humidity readings are multiplied by 100."""
    entry = Is3Entry(name="x", address=address, value=1, unit=unit, labelled=True)
    assert value_scale(entry) == expected


def test_writable_matches_the_platforms_that_send_values(is3) -> None:
    """Everything that can be commanded is writable, and nothing else is."""
    for item in is3.entries:
        assert is_writable(item) == (
            is_switchable(item) or is_dimmable(item) or is_number(item)
        )
        if is_binary(item) or is_measured(item):
            assert not is_writable(item)


def test_writable_entries_are_polled(is3) -> None:
    """A value that can be set still has to be read back."""
    for item in is3.entries:
        if is_writable(item):
            assert is_readable(item)


def test_plan_slots_produce_no_entity(is3) -> None:
    """Heating plan slots and schedule entries are ignored entirely."""
    for address in (0x05010002, 0x0003001B):
        entry = is3.by_address(address)
        assert entry is not None
        assert not is_readable(entry)


def test_both_formats_agree_on_shared_addresses(is3, imm) -> None:
    """The same installation exported both ways yields the same addresses."""
    shared = {e.address for e in is3.entries} & {e.address for e in imm.entries}
    assert len(shared) >= 8
    for address in shared:
        assert is3.by_address(address).unit == imm.by_address(address).unit
