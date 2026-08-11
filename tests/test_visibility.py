"""Which entities are put in front of people, and which merely exist.

Home Assistant has two separate switches for this and they are not
interchangeable:

* **disabled** — the entity is not created at all, so nothing can use it;
* **hidden** — the entity exists and works, it is just left out of the
  generated dashboards and the default views.

A wall panel's indicator LEDs want the second one. They are real outputs worth
driving from an automation, but a generated "lights" dashboard collects every
switch in the building, and a house full of wall panels has more backlights than
lamps.
"""

from __future__ import annotations

import pytest

from custom_components.is3_export.export import (
    Is3Entry,
    enabled_by_default,
    is_indicator_led,
    visible_by_default,
)

RELAY = 0x0102000A


def _entry(name: str, hw_id: str | None = None) -> Is3Entry:
    return Is3Entry(name=name, address=RELAY, value=0, hw_id=hw_id)


@pytest.mark.parametrize(
    "hw_id",
    [
        "WSB3-20_Green_0B0001",
        "WSB3-40_Green2_0B0002",
        "WSB3-20-Hum_Red_0B0003",
        "WSB3-40_Red1_0B0002",
    ],
)
def test_indicator_leds_are_hidden_but_kept(hw_id: str) -> None:
    """Hidden, not disabled: still there to be switched, just not on show."""
    entry = _entry("_", hw_id=hw_id)

    assert is_indicator_led(entry)
    assert not visible_by_default(entry)
    assert enabled_by_default(entry), "hiding one must not take it away"


def test_a_named_led_is_hidden_too() -> None:
    """The role decides, not the name -- an installer may have labelled it."""
    entry = _entry("Podsviceni_chodba", hw_id="WSB3-40_Green1_0B0004")

    assert not visible_by_default(entry)
    assert enabled_by_default(entry)


@pytest.mark.parametrize(
    ("name", "hw_id"),
    [
        ("Sv_loznice", "SA3-04M_RE1_0A0001"),
        ("Zas_kuchyne", "SA3-04M_RE2_0A0001"),
        # A role that merely begins with a colour word is not an indicator.
        ("Redukce", "SA3-04M_Redukce_0A0001"),
        # A wall switch's button contact is not its LED.
        ("_", "WSB3-40_DIN3_0B0002"),
    ],
)
def test_everything_else_stays_visible(name: str, hw_id: str) -> None:
    """Only the indicator LEDs are hidden; nothing else is quietly removed."""
    assert visible_by_default(_entry(name, hw_id=hw_id))


def test_an_entry_without_a_hardware_id_is_visible() -> None:
    """System bits and integers carry no role to be mistaken for one."""
    assert visible_by_default(_entry("Rezim_dovolena"))
