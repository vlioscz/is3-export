"""Classification against a newer central unit (IDM3 03-05-03).

Newer firmware exports the same format, but some modules label their channels
differently, which is what these cases pin down.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.is3_export.export import (
    Is3Entry,
    effective_unit,
    is_binary,
    is_dimmable,
    is_measured,
    is_switchable,
    is_writable,
    parse_export,
    value_scale,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(name="export")
def export_fixture():
    """An export from a newer generation unit."""
    return parse_export((FIXTURES / "new_unit.is3").read_text(encoding="utf-8-sig"))


def test_header_of_newer_firmware(export) -> None:
    """A newer IDM3 build writes the same header layout."""
    assert export.header.idm3 == "03-05-03"
    assert export.header.name == "New-unit"


@pytest.mark.parametrize("address", [0x0108004F, 0x01080050])
def test_temperature_without_a_unit_is_still_a_temperature(
    export, address: int
) -> None:
    """IOU3-108M omits the unit on its temperature inputs.

    Taking that at face value would show an outdoor reading of 25.50 degrees as
    a bare 2550, so the unit is inferred from the hardware id.
    """
    entry = export.by_address(address)
    assert entry.unit is None, "the export really does omit it"
    assert effective_unit(entry) == "°C"
    assert value_scale(entry) == 100


def test_temperature_inference_does_not_overreach(export) -> None:
    """Unitless controller channels must not be turned into temperatures."""
    for address in (0x01040005, 0x02030000):
        entry = export.by_address(address)
        assert effective_unit(entry) is None


@pytest.mark.parametrize(
    "hw_id",
    [
        "IOU3-108M_TIN1_0B0003",
        "IOU3-108M_TIN2_0B0003",
        "DA3-22M_TIN_0A0003",
        "WSB3-40_Inter-Therm_0B0001",
        "WSB3-40_AIN1-AIN2-Therm_0B0001",
    ],
)
def test_temperature_channels_are_recognised(hw_id: str) -> None:
    """Every spelling of a temperature channel seen in the exports."""
    entry = Is3Entry(name="x", address=0x0108004F, hw_id=hw_id, value=2550)
    assert effective_unit(entry) == "°C"
    assert value_scale(entry) == 100


@pytest.mark.parametrize(
    "hw_id",
    [
        # The same module carries digital inputs and relays, so nothing may be
        # inferred from the module type itself.
        "IOU3-108M_DIN1_0B0003",
        "IOU3-108M_RE1_0B0003",
        # Names that merely contain the letters must not match.
        "SomeModule_MARTIN_0001",
        "XY_Thermostat-Setpoint_0001",
        "DLS3-1_Light-IN_025D85",
    ],
)
def test_temperature_inference_is_per_channel(hw_id: str) -> None:
    """IOU3-108M is a universal input module; only its TIN channels are warm."""
    entry = Is3Entry(name="x", address=0x0108004F, hw_id=hw_id, value=2550)
    assert effective_unit(entry) is None
    assert value_scale(entry) == 1


def test_fault_flag_on_a_temperature_input_is_not_a_temperature() -> None:
    """`OUF-Alert_TIN1` reports that an input failed, not what it reads."""
    alert = Is3Entry(
        name="OUF-Alert_TIN1",
        address=0x01070032,
        hw_id="IOU3-108M_OUF-Alert_TIN1_0B0003",
        value=0,
    )
    assert is_binary(alert)
    assert effective_unit(alert) is None


def test_labelled_temperatures_keep_their_own_unit(export) -> None:
    """Modules that do label the unit are left alone."""
    entry = export.by_address(0x01050001)
    assert entry.unit == "°C"
    assert effective_unit(entry) == "°C"
    assert value_scale(entry) == 100


@pytest.mark.parametrize(
    "address",
    [
        0x0107001F,  # SA3-014M synchronisation alert
        0x01070026,  # IOU3-108M output fault
    ],
)
def test_new_module_faults_are_binary_sensors(export, address: int) -> None:
    """Newer modules add fault channels; they must not become switches."""
    entry = export.by_address(address)
    assert is_binary(entry)
    assert not is_writable(entry)


@pytest.mark.parametrize("address", [0x01020079, 0x01020087, 0x0102008F])
def test_relays_on_new_modules_are_switches(export, address: int) -> None:
    """SA3-014M, IOU3-108M and JA3-014M relays behave like any other."""
    assert is_switchable(export.by_address(address))


def test_panel_leds_are_dimmable_on_newer_units(export) -> None:
    """Indicator LEDs moved into the dimmer range and carry a percent unit."""
    entry = export.by_address(0x01040001)
    assert is_dimmable(entry)


def test_unnamed_entries_are_still_classified(export) -> None:
    """Being unnamed changes presentation, never capability."""
    unnamed_led = export.by_address(0x01040049)
    assert not unnamed_led.labelled
    assert is_dimmable(unnamed_led)

    unnamed_button = export.by_address(0x0101010B)
    assert not unnamed_button.labelled
    assert is_binary(unnamed_button)


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
