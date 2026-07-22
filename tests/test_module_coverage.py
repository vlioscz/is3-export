"""Buttons are recognised across the whole wall/glass controller family.

The classifier used to know only WSB and RFKEY; every other panel's touch keys
fell through to binary_sensor.  Now the wired controllers (GSB, MSB, GSP, GBP,
GRT, GMR, GCR, GHR, GCH, GDB, WMR, IDRT) are buttons with short/long, RF sources
(RFKEY, IBWL) are single-press buttons, and a plain module's DIN input is a
maintained contact -- a button only on the central unit itself or when named.
"""

from __future__ import annotations

import pytest

from custom_components.is3_export.export import (
    Is3Entry,
    detector_binary_class,
    is_press_button,
    is_rf_button,
)

DIN = 0x01010001  # a digital input


def _in(hw_id: str, name: str = "x") -> Is3Entry:
    return Is3Entry(name=name, address=DIN, hw_id=hw_id, value=0)


@pytest.mark.parametrize(
    "model",
    ["GSB3-60", "GSB3-40ProSx", "MSB3-40", "GSP3-100", "GBP3-60", "GRT3-50",
     "GMR3-61", "GCR3-11", "GHR3-11", "GCH3-31", "GDB3-10", "WMR3-21", "IDRT3-1",
     "WSB3-40"],
)
def test_wall_controller_keys_are_wired_buttons(model: str) -> None:
    """A touch/rocker key on any wall controller is a button, short/long capable."""
    entry = _in(f"{model}_Up1_0A0001")
    assert is_press_button(entry), model
    assert not is_rf_button(entry), f"{model} is wired, so long-press capable"


def test_rfkey_remote_is_a_single_press_button() -> None:
    """An RFKEY key fob is all buttons, single-press only (release is lost)."""
    entry = _in("RFKEY_IN1_0A0002")
    assert is_press_button(entry)
    assert is_rf_button(entry)


def test_ibwl_rf_inputs_default_to_binary_sensors() -> None:
    """An IBWL RF input mirrors whatever RF unit was paired (a button, but also a
    door/motion contact), so it is a binary_sensor unless named a tlačítko."""
    assert not is_press_button(_in("IBWL-20B_IN1_0A0003"))
    named = _in("IBWL-20B_IN2_0A0004", name="TL_zvonek")
    assert is_press_button(named)
    assert is_rf_button(named), "a named IBWL button is still RF -> single press"


def test_detector_modules_carry_a_presence_device_class() -> None:
    """A motion/occupancy detector's input gets the matching device_class."""
    assert detector_binary_class(_in("PMS3-01_Motion_0A0005")) == "motion"
    assert detector_binary_class(_in("DMD3-1_Motion_0A0006")) == "motion"
    assert detector_binary_class(_in("MCD3-01_Occ_0A0007")) == "occupancy"
    assert detector_binary_class(_in("SA3-04M_IN1_0A0008")) is None


def test_a_plain_modules_din_is_a_contact_not_a_button() -> None:
    """An input module's DIN is a maintained contact -> binary_sensor, not a press."""
    assert not is_press_button(_in("IM3-40B_DIN1_0A0003"))
    assert not is_press_button(_in("SA3-04M_DIN2_0A0004"))


def test_a_named_tlacitko_is_a_button_on_any_module() -> None:
    """The installer can still opt a contact in by naming it a tlačítko."""
    assert is_press_button(_in("SA3-04M_DIN2_0A0005", name="TL_zvonek"))


def test_central_unit_din_stays_a_button() -> None:
    """A bare DIN input on the unit's own In-Out terminals is still a button."""
    entry = _in("In-Out-CU3-01M-CU3-02M_DIN2_0A0006", name="DIN2")
    assert is_press_button(entry)
    assert not is_rf_button(entry)


def test_proximity_and_card_inputs_are_not_buttons() -> None:
    """A panel's wake sensor and card reader must not fire presses."""
    assert not is_press_button(_in("GSB3-40ProSx_Prox_0A0007"))
    assert not is_press_button(_in("GCR3-11_Card_0A0008"))
    assert not is_press_button(_in("GSP3-100Pro_x_0A0009", name="Priblizeni"))
