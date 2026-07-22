"""SW state inputs and fault flags are hidden; RF devices group and read right.

A relay module gained SW state inputs and per-output fault flags -- useful to a
few, clutter to most -- so they start disabled whatever their name.  An RF
receiver's devices come in as their own device, and a low-battery input reads as
a battery sensor.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.is3_export.binary_sensor import Is3BinarySensor
from custom_components.is3_export.const import DOMAIN
from custom_components.is3_export.export import (
    Is3Entry,
    enabled_by_default,
    is_battery_input,
    is_press_button,
    is_rf_button,
    module_of,
    parse_export,
    platform_of,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(name="export")
def export_fixture():
    return parse_export((FIXTURES / "rf_and_status.is3").read_text(encoding="utf-8-sig"))


class _Coord:
    class _Entry:
        entry_id = "mod"
        title = "Unit"

    config_entry = _Entry()
    values: dict[int, int] = {}


def test_sw_inputs_are_hidden_even_when_named(export) -> None:
    """A relay's SW state inputs start disabled, name or not."""
    named = export.by_address(0x010100A0)  # "SW1"
    assert named.labelled
    assert enabled_by_default(named) is False
    assert platform_of(named) == "binary_sensor"


def test_fault_flags_are_hidden_and_diagnostic(export) -> None:
    """Per-output fault flags start disabled and read as a diagnostic problem."""
    named = export.by_address(0x01070040)  # labelled OUF-Alert-RE1
    assert enabled_by_default(named) is False
    sensor = Is3BinarySensor(_Coord(), named)
    assert sensor.device_class == BinarySensorDeviceClass.PROBLEM
    # An unnamed one stays hidden too, as any unnamed internal.
    assert enabled_by_default(export.by_address(0x01070041)) is False


def test_relay_outputs_are_unaffected(export) -> None:
    """The relays themselves are still shown; only SW and alerts are hidden."""
    relay = export.by_address(0x01020040)  # Porch_light
    assert enabled_by_default(relay) is True


def test_rf_device_groups_under_its_own_device(export) -> None:
    """An RF key fob's channels group under one RF device, off the central unit."""
    button = export.by_address(0x010100B1)
    assert module_of(button) == ("RFKEY", "0D0009")
    # An RF button keeps single-press: its release is lost too often for timing.
    assert is_rf_button(button)
    sensor = Is3BinarySensor(_Coord(), button)
    assert sensor.device_info["identifiers"] == {(DOMAIN, "mod_0D0009")}
    assert sensor.device_info["via_device"] == (DOMAIN, "mod")


def test_low_battery_input_is_a_battery_sensor(export) -> None:
    """The RF device's Battery_LOW input reads as a battery diagnostic."""
    battery = export.by_address(0x010100B0)
    assert is_battery_input(battery)
    sensor = Is3BinarySensor(_Coord(), battery)
    assert sensor.device_class == BinarySensorDeviceClass.BATTERY
    # It is a real reading worth seeing, so it stays enabled.
    assert enabled_by_default(battery) is True


def test_a_motion_detector_reads_as_motion() -> None:
    """A PMS3/DMD3 detector's digital input carries the motion device_class."""
    entry = Is3Entry(name="Pohyb", address=0x01010001, hw_id="PMS3-01_Motion_0A0001")
    sensor = Is3BinarySensor(_Coord(), entry)
    assert sensor.device_class == BinarySensorDeviceClass.MOTION


def test_rf_remote_buttons_get_a_press_event_but_the_battery_does_not(export) -> None:
    """An RF fob's buttons carry a long press like a wall switch; its battery
    flag is an input too, but not a button."""
    assert is_press_button(export.by_address(0x010100B1))  # Fob_open (IN1)
    assert is_press_button(export.by_address(0x010100B2))  # Fob_close (IN2)
    assert not is_press_button(export.by_address(0x010100B0))  # Battery_LOW
