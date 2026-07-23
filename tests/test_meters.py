"""A meter with a recognised unit joins the Energy dashboard; others stay totals.

A counter (address class 0x02/0x06) always records a long-term total.  When the
export gives it an energy or volume unit it also gets the matching device class,
which is what the Energy dashboard needs; a unitless or unknown counter is left
exactly as it was -- nothing about its quantity is invented.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy, UnitOfVolume

from custom_components.is3_export.export import Is3Entry
from custom_components.is3_export.sensor import Is3Sensor, counter_metric

COUNTER = 0x02060001  # space 0x02, type 0x06 -> a counter


def _counter(name: str, unit: str | None = None, hw_id: str | None = None) -> Is3Entry:
    return Is3Entry(name=name, address=COUNTER, unit=unit, hw_id=hw_id)


class _Coord:
    class _Entry:
        entry_id = "unit"
        title = "Unit"

    config_entry = _Entry()
    values: dict[int, int] = {}


def test_energy_units_map_to_the_energy_class() -> None:
    assert counter_metric(_counter("Elektromer", "kWh")) == (
        UnitOfEnergy.KILO_WATT_HOUR,
        SensorDeviceClass.ENERGY,
    )
    assert counter_metric(_counter("Teplo", "GJ"))[1] is SensorDeviceClass.ENERGY
    # matched case-insensitively and trimmed
    assert counter_metric(_counter("El", " Wh "))[0] == UnitOfEnergy.WATT_HOUR


def test_cubic_metres_are_water_by_default_and_gas_by_name() -> None:
    unit, device = counter_metric(_counter("Vodomer_1", "m³"))
    assert unit == UnitOfVolume.CUBIC_METERS
    assert device is SensorDeviceClass.WATER
    assert counter_metric(_counter("Plynomer", "m3"))[1] is SensorDeviceClass.GAS


def test_unitless_or_unknown_counter_is_left_a_plain_total() -> None:
    assert counter_metric(_counter("Vodomer_1")) is None  # no unit (the real export)
    assert counter_metric(_counter("Pulzy", "imp")) is None  # unrecognised unit


def test_only_counters_are_metrics() -> None:
    temperature = Is3Entry(name="Teplota", address=0x01050001, unit="°C")
    assert counter_metric(temperature) is None


def test_energy_counter_entity_is_dashboard_ready() -> None:
    sensor = Is3Sensor(_Coord(), _counter("Elektromer", "kWh"))
    assert sensor.device_class is SensorDeviceClass.ENERGY
    assert sensor.native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR
    assert sensor.state_class is SensorStateClass.TOTAL_INCREASING


def test_unitless_counter_entity_is_a_total_without_a_class() -> None:
    sensor = Is3Sensor(_Coord(), _counter("Vodomer_1"))
    assert sensor.state_class is SensorStateClass.TOTAL_INCREASING
    assert sensor.device_class is None
