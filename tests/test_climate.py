"""Assembling heating controllers into climate zones."""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.is3_export.export import (
    CONTROLLER_PRESETS,
    find_controllers,
    parse_export,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(name="controllers")
def controllers_fixture():
    """Controllers keyed by name."""
    export = parse_export((FIXTURES / "climate.is3").read_text(encoding="utf-8-sig"))
    return {c.name: c for c in find_controllers(export)}


def test_a_zone_is_assembled_from_its_channels(controllers) -> None:
    """Channels sharing a serial, plus the named root, form one zone."""
    assert set(controllers) == {"TOP_loz", "TOP_ob"}
    loz = controllers["TOP_loz"]
    assert loz.serial == "0E0001"
    assert loz.actual == 0x01080025
    assert loz.required == 0x01080026
    assert loz.manual == 0x01120043
    assert loz.heat_demand == 0x01010063
    assert loz.preset_select == 0x01110018
    assert loz.control_on == 0x01110015
    assert loz.cool_demand == 0x01010064
    assert loz.status == 0x01010061


def test_zone_without_control_in_cannot_be_turned_off(controllers) -> None:
    """The off switch is optional; a zone lacking Control-IN has none."""
    assert controllers["TOP_ob"].control_on is None


def test_plan_channel_is_picked_up(controllers) -> None:
    """A zone with Control-Plan-IN gets a plan select address."""
    assert controllers["TOP_loz"].plan_select == 0x01110017
    assert controllers["TOP_ob"].plan_select is None


def test_plan_values_are_the_verified_ones() -> None:
    """Only the verified plans are offered: 0 normal, 64 vacation.

    Public holiday (0x80) is left out until it can be confirmed on a unit that
    has it configured.
    """
    from custom_components.is3_export.export import PLAN_OPTIONS

    assert PLAN_OPTIONS == {0: "Normal", 64: "Vacation"}
    assert 128 not in PLAN_OPTIONS


def test_name_comes_from_the_root(controllers) -> None:
    """The friendly name is the labelled root entry, not a channel."""
    assert controllers["TOP_loz"].name == "TOP_loz"


def test_a_zone_without_cooling_has_no_cool_demand(controllers) -> None:
    """The cooling channel is optional."""
    assert controllers["TOP_ob"].cool_demand is None


def test_read_addresses_cover_every_polled_channel(controllers) -> None:
    """Every channel the zone reads is listed for the listeners and the poll."""
    loz = controllers["TOP_loz"]
    for address in (loz.actual, loz.required, loz.heat_demand, loz.preset_select):
        assert address in loz.read_addresses
    # The write-only manual setpoint is not read.
    assert loz.manual not in loz.read_addresses


def test_incomplete_controllers_are_skipped() -> None:
    """A controller missing an essential channel is not a zone."""
    partial = (
        "VERSION_01-03-03_ID_ABC_NAME_Partial\r\n"
        # Only an actual-temperature channel, nothing to control.
        "_ Controller_Actual-Therm-AOUT_0F0001 0x01080001 0x00000999 °C\r\n"
        "Cont_x Controller_0F0001 0x00030001 0x00000006\r\n"
    )
    assert find_controllers(parse_export(partial)) == []


def test_preset_values_line_up_with_control_manual_in() -> None:
    """The Control-Manual-IN encoding: 0 Schedule, 1-4 presets, 7 Manual."""
    assert CONTROLLER_PRESETS[0] == "Schedule"
    assert CONTROLLER_PRESETS[1] == "Preset 1"
    assert CONTROLLER_PRESETS[4] == "Preset 4"
    assert CONTROLLER_PRESETS[7] == "Manual"
    assert 5 not in CONTROLLER_PRESETS, "5 gives frost, not Manual"
    assert len(CONTROLLER_PRESETS) == 6


def test_setpoint_write_settles_then_verifies() -> None:
    """The setpoint write waits for the Manual switch, then confirms and retries.

    Writing the setpoint immediately after switching to Manual corrupts it, so
    the entity must settle first and read Required-Therm-AOUT back.
    """
    from custom_components.is3_export.climate import (
        MANUAL_SETTLE,
        SETPOINT_ATTEMPTS,
    )

    assert MANUAL_SETTLE >= 1.0, "the switch to Manual needs time to settle"
    assert SETPOINT_ATTEMPTS >= 2, "a single write is not always accepted"


def test_unique_id_is_stable(controllers) -> None:
    """The zone id is derived from the serial, not the position."""
    assert controllers["TOP_loz"].unique_id == "climate_0e0001"
