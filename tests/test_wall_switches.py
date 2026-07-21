"""WSB3 wall switches unpack into one entity per input.

Each wall switch carries several channels: the two/four rocker inputs, the
green and red indicator LEDs, the internal and external thermometers, two
loose digital inputs, and -- on the -Hum variants -- humidity and dew point.
The classification is by address type, so a switch falls out as the right mix
without special-casing; these tests pin the counts and the humidity sensor.
"""

from __future__ import annotations

import collections
from pathlib import Path

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import PERCENTAGE

from custom_components.is3_export.const import DOMAIN
from custom_components.is3_export.export import (
    PLATFORM_BINARY_SENSOR,
    PLATFORM_SENSOR,
    PLATFORM_SWITCH,
    Is3Entry,
    effective_unit,
    enabled_by_default,
    module_of,
    parse_export,
    platform_of,
)
from custom_components.is3_export.sensor import Is3Sensor

FIXTURES = Path(__file__).parent / "fixtures"

WSB3_20 = "0B0001"
WSB3_40 = "0B0002"
WSB3_20_HUM = "0B0003"


@pytest.fixture(name="export")
def export_fixture():
    return parse_export((FIXTURES / "wall_switches.is3").read_text(encoding="utf-8-sig"))


def _platforms(export, serial: str) -> collections.Counter:
    """The platform of every entity-producing channel on one wall switch."""
    return collections.Counter(
        platform_of(entry)
        for entry in export.entries
        if (entry.hw_id or "").endswith("_" + serial) and platform_of(entry) is not None
    )


def test_wsb3_20_unpacks_into_eight_entities(export) -> None:
    """Two rockers, two LEDs, two thermometers, two inputs."""
    platforms = _platforms(export, WSB3_20)
    assert sum(platforms.values()) == 8
    assert platforms[PLATFORM_BINARY_SENSOR] == 4
    assert platforms[PLATFORM_SWITCH] == 2
    assert platforms[PLATFORM_SENSOR] == 2


def test_wsb3_40_unpacks_into_twelve_entities(export) -> None:
    """The four-rocker switch has four inputs and four LEDs more than the two."""
    platforms = _platforms(export, WSB3_40)
    assert sum(platforms.values()) == 12
    assert platforms[PLATFORM_BINARY_SENSOR] == 6
    assert platforms[PLATFORM_SWITCH] == 4
    assert platforms[PLATFORM_SENSOR] == 2


def test_wsb3_hum_adds_humidity_and_dew_point(export) -> None:
    """The -Hum variant is the base eight plus a humidity and a dew-point sensor."""
    platforms = _platforms(export, WSB3_20_HUM)
    assert sum(platforms.values()) == 10
    # Two thermometers, plus humidity and dew point, are all sensors.
    assert platforms[PLATFORM_SENSOR] == 4
    assert platforms[PLATFORM_BINARY_SENSOR] == 4
    assert platforms[PLATFORM_SWITCH] == 2


def test_humidity_channel_is_a_humidity_sensor(export) -> None:
    """The `%` unit marks the internal humidity, not another temperature."""
    humidity = export.by_address(0x01050017)
    assert humidity is not None
    assert platform_of(humidity) == PLATFORM_SENSOR
    assert effective_unit(humidity) == "%"


def test_unnamed_inputs_take_their_role_as_a_name(export) -> None:
    """An input the installer left unnamed reads as its role, not the whole id."""
    up = export.by_address(0x01010070)
    assert up is not None
    assert up.name == "Up"
    assert not up.labelled
    external = export.by_address(0x01050016)
    assert external is not None
    assert external.name == "AIN1-AIN2-Therm"


class _Coord:
    class _Entry:
        entry_id = "wall"
        title = "Unit"

    config_entry = _Entry()
    values: dict[int, int] = {}


def test_humidity_sensor_gets_the_humidity_device_class(export) -> None:
    """Built as an entity, the humidity channel carries the right class and unit."""
    humidity = export.by_address(0x01050017)
    sensor = Is3Sensor(_Coord(), humidity)
    assert sensor.device_class == SensorDeviceClass.HUMIDITY
    assert sensor.native_unit_of_measurement == PERCENTAGE


def test_unnamed_channels_are_disabled_by_default(export) -> None:
    """An ordinary unlabelled channel starts disabled; a named one stays on."""
    unnamed = export.by_address(0x01010070)  # unlabelled rocker input
    assert not unnamed.labelled
    assert enabled_by_default(unnamed) is False
    named = Is3Sensor(_Coord(), export.by_address(0x01050017))
    assert named.entity_registry_enabled_default is True


def test_ain_therm_is_shown_even_when_unnamed(export) -> None:
    """AIN1-AIN2-Therm may be a real temperature depending on the unit's wiring,
    so it is enabled on every switch, named or not."""
    ain = export.by_address(0x01050016)  # unlabelled AIN1-AIN2-Therm
    assert not ain.labelled
    assert enabled_by_default(ain) is True
    sensor = Is3Sensor(_Coord(), ain)
    assert sensor.entity_registry_enabled_default is True


def test_module_of_reads_the_model_and_serial(export) -> None:
    """A channel's hardware id gives the module it belongs to."""
    up = export.by_address(0x01010070)  # WSB3-20-Hum Up
    assert module_of(up) == ("WSB3-20-Hum", WSB3_20_HUM)


def test_module_of_skips_system_and_controller_entries() -> None:
    """System bits have no module, and controller channels belong to a zone."""
    assert module_of(Is3Entry(name="grp", address=0x02030000)) is None
    controller = Is3Entry(
        name="_",
        address=0x01080025,
        hw_id="Controller_Actual-Therm-AOUT_0E0001",
    )
    assert module_of(controller) is None


def test_each_switch_is_its_own_device_under_the_unit(export) -> None:
    """Channels group by module: same switch shares a device, different ones do not."""
    hall_temp = Is3Sensor(_Coord(), export.by_address(0x01050001))  # WSB3-20
    hall_ext = Is3Sensor(_Coord(), export.by_address(0x01050002))  # same switch
    bath_hum = Is3Sensor(_Coord(), export.by_address(0x01050017))  # WSB3-20-Hum

    assert hall_temp.device_info["identifiers"] == hall_ext.device_info["identifiers"]
    assert hall_temp.device_info["identifiers"] != bath_hum.device_info["identifiers"]
    # The module hangs off the central unit (the config entry's own device).
    assert hall_temp.device_info["identifiers"] == {(DOMAIN, f"wall_{WSB3_20}")}
    assert hall_temp.device_info["via_device"] == (DOMAIN, "wall")
