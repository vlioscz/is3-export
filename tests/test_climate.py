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
    assert loz.control_hc == 0x01110016
    assert loz.cool_demand == 0x01010064
    assert loz.cool_required == 0x01080027
    assert loz.cool_manual == 0x01120044
    assert loz.status == 0x01010061


def test_zone_without_control_in_cannot_be_turned_off(controllers) -> None:
    """The off switch is optional; a zone lacking Control-IN has none."""
    assert controllers["TOP_ob"].control_on is None


def test_plan_channel_is_picked_up(controllers) -> None:
    """A zone with Control-Plan-IN gets a plan select address."""
    assert controllers["TOP_loz"].plan_select == 0x01110017
    assert controllers["TOP_ob"].plan_select is None


def test_plan_values_are_the_verified_ones() -> None:
    """All three plans are offered, each verified writable on a live unit.

    0 normal, 64 (0x40) vacation, 128 (0x80) public holiday -- the last was
    confirmed once a unit had the festive daily programme configured.
    """
    from custom_components.is3_export.export import PLAN_OPTIONS

    assert PLAN_OPTIONS == {0: "Normal", 64: "Vacation", 128: "Public holiday"}


def test_name_comes_from_the_root(controllers) -> None:
    """The friendly name is the labelled root entry, not a channel."""
    assert controllers["TOP_loz"].name == "TOP_loz"


def test_a_zone_without_cooling_has_no_cool_channels(controllers) -> None:
    """The cooling channels are optional; a heat-only zone has none of them."""
    ob = controllers["TOP_ob"]
    assert ob.cool_demand is None
    assert ob.control_hc is None
    assert ob.cool_required is None
    assert ob.cool_manual is None


def test_read_addresses_cover_every_polled_channel(controllers) -> None:
    """Every channel the zone reads is listed for the listeners and the poll."""
    loz = controllers["TOP_loz"]
    for address in (loz.actual, loz.required, loz.heat_demand, loz.preset_select):
        assert address in loz.read_addresses
    # The heat/cool switch and the cool setpoint are polled so the mode and the
    # cooling target stay current.
    assert loz.control_hc in loz.read_addresses
    assert loz.cool_required in loz.read_addresses
    # The write-only manual setpoints are not read.
    assert loz.manual not in loz.read_addresses
    assert loz.cool_manual not in loz.read_addresses


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


# --- Confirming a setpoint the zone will not report back --------------------
#
# A zone that is switched off reports no setpoint at all -- Required-Therm-AOUT
# comes back as "no value", on this transport and on the old one alike.  There
# is then nothing to verify a write against, which is not the same as the write
# having failed.  Reporting it as a failure told people their heating command
# had not landed every single time they touched a zone that was off.


class _SetpointClient:
    """Accepts writes, and reports whatever setpoint it is told to."""

    def __init__(self, reports) -> None:
        self._reports = reports
        self.writes: list[tuple[str, int]] = []

    async def async_set(self, address: str, value: int) -> None:
        self.writes.append((address, value))

    async def async_get(self, address: str) -> int | None:
        return self._reports


class _SetpointCoordinator:
    def __init__(self, client) -> None:
        self.client = client
        self.values: dict[int, int] = {}
        self.noted: list[tuple[int, int]] = []

    def async_note_write(self, address: int, value: int) -> None:
        self.noted.append((address, value))


def _climate_entity(controller, reports):
    import asyncio

    from custom_components.is3_export.climate import Is3Climate

    entity = Is3Climate.__new__(Is3Climate)
    entity.controller = controller
    entity.coordinator = _SetpointCoordinator(_SetpointClient(reports))
    entity._attr_name = controller.name
    entity._setpoint_lock = asyncio.Lock()
    entity._setpoint_wanted = None
    entity.async_write_ha_state = lambda: None
    return entity


def test_a_zone_reporting_no_setpoint_is_not_an_error(controllers, monkeypatch) -> None:
    """The zone is off; the write is taken at its word instead of being failed."""
    import asyncio

    import custom_components.is3_export.climate as climate

    monkeypatch.setattr(climate, "MANUAL_SETTLE", 0)
    monkeypatch.setattr(climate, "SETPOINT_VERIFY_DELAY", 0)

    entity = _climate_entity(controllers["TOP_loz"], reports=None)

    asyncio.run(entity.async_set_temperature(temperature=8.0))

    assert (controllers["TOP_loz"].required, 800) in entity.coordinator.noted


def test_a_contradicted_setpoint_is_still_an_error(controllers, monkeypatch) -> None:
    """The zone reports a setpoint, and it is not the one asked for."""
    import asyncio

    from homeassistant.exceptions import HomeAssistantError

    import custom_components.is3_export.climate as climate

    monkeypatch.setattr(climate, "MANUAL_SETTLE", 0)
    monkeypatch.setattr(climate, "SETPOINT_VERIFY_DELAY", 0)

    entity = _climate_entity(controllers["TOP_loz"], reports=2200)

    with pytest.raises(HomeAssistantError):
        asyncio.run(entity.async_set_temperature(temperature=8.0))


def test_dragging_the_slider_writes_only_the_last_value(controllers, monkeypatch) -> None:
    """Each slider step is its own service call, and each takes seconds.

    Overlapping, they read back the value another had just written, concluded
    their own had been refused, wrote again, and finally reported a failure for
    a temperature nobody wanted any more.  Only the last request should reach
    the unit; the ones overtaken on the way step aside.
    """
    import asyncio

    import custom_components.is3_export.climate as climate

    monkeypatch.setattr(climate, "MANUAL_SETTLE", 0)
    monkeypatch.setattr(climate, "SETPOINT_VERIFY_DELAY", 0)

    entity = _climate_entity(controllers["TOP_loz"], reports=None)
    manual_hex = f"0x{controllers['TOP_loz'].manual:08X}"

    async def drag() -> None:
        await asyncio.gather(
            *(entity.async_set_temperature(temperature=t) for t in (20.0, 19.5, 8.0))
        )

    asyncio.run(drag())

    written = [value for address, value in entity.coordinator.client.writes
               if address == manual_hex]
    assert written, "nothing was written at all"
    # The first call was already on its way before the others existed, so it
    # goes out -- that cannot be helped.  What matters is that the zone is left
    # holding the temperature that was asked for last, and that a value already
    # overtaken while it queued never reaches the unit at all.
    assert written[-1] == 800, f"the zone was left on the wrong setpoint: {written}"
    assert 1950 not in written, f"a superseded setpoint was still written: {written}"


def test_unique_id_is_stable(controllers) -> None:
    """The zone id is derived from the serial, not the position."""
    assert controllers["TOP_loz"].unique_id == "climate_0e0001"
