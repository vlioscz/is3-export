"""Cooling regulator internals stay read-only; a lux input reads illuminance.

Both come from a real 3.3.34 export.  The ``Cool-Regulator_`` block mirrors
``Heat-Regulator_`` but was not marked internal, so its Enable/Status channels
leaked out as writable switches -- fixed here.  The DLS3 light sensor's
``Light-IN`` channel carries no unit in the export, so it was a bare number;
it now reads lux.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import LIGHT_LUX

from custom_components.is3_export.export import (
    Is3Entry,
    is_controller_internal,
    is_illuminance,
    is_switchable,
    platform_of,
)
from custom_components.is3_export.sensor import Is3Sensor

# A relay-type channel (data type 0x02) on each regulator block.
COOL_RELAY = Is3Entry(
    name="_", address=0x01020099, hw_id="Cool-Regulator_Enable-DIN_0C0001", labelled=False
)
HEAT_RELAY = Is3Entry(
    name="_", address=0x0102009A, hw_id="Heat-Regulator_Enable-DIN_0C0001", labelled=False
)
LUX = Is3Entry(name="Osvit", address=0x01080001, hw_id="DLS3-1_Light-IN_0C0001")


class _Coord:
    class _Entry:
        entry_id = "unit"
        title = "Unit"

    config_entry = _Entry()
    values: dict[int, int] = {}


def test_cooling_regulator_is_internal_like_heating() -> None:
    """Both regulator blocks are internal, so a relay channel is read-only."""
    for relay in (HEAT_RELAY, COOL_RELAY):
        assert is_controller_internal(relay)
        assert not is_switchable(relay)  # never written to
        assert platform_of(relay) == "binary_sensor"  # not a writable switch


def test_light_input_reads_illuminance_in_lux() -> None:
    assert is_illuminance(LUX)
    assert platform_of(LUX) == "sensor"

    sensor = Is3Sensor(_Coord(), LUX)
    assert sensor.device_class is SensorDeviceClass.ILLUMINANCE
    assert sensor.native_unit_of_measurement == LIGHT_LUX


def test_a_plain_relay_is_not_mistaken_for_a_regulator() -> None:
    """The prefix match must not catch an ordinary relay."""
    relay = Is3Entry(name="Sv_kuchyn", address=0x0102000A, hw_id="SA3-012M_RE1_0C0001")
    assert not is_controller_internal(relay)
    assert is_switchable(relay)


def test_a_light_output_is_not_an_illuminance_sensor() -> None:
    """`is_illuminance` matches the Light-IN input role, not a light output."""
    dimmer = Is3Entry(name="Sv_obyvak", address=0x01040002, hw_id="DA3-22M_OUT1_0C0001")
    assert not is_illuminance(dimmer)
