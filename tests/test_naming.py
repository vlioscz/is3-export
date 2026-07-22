"""Naming conventions that decide which platform an output lands on.

The address says what an output is; the name says what it is for. An impulse
and a lamp are both just relays, so only the installer's name tells them apart.
"""

from __future__ import annotations

import pytest

from custom_components.is3_export.export import (
    ICON_LAMP,
    ICON_FAN,
    ICON_LED,
    ICON_MIRROR,
    ICON_SOCKET,
    PLATFORM_BINARY_SENSOR,
    PLATFORM_BUTTON,
    PLATFORM_LIGHT,
    PLATFORM_SENSOR,
    PLATFORM_NUMBER,
    PLATFORM_SWITCH,
    Is3Entry,
    effective_unit,
    is_impulse,
    is_named_light,
    is_press_button,
    is_readable,
    is_writable,
    entity_icon,
    platform_of,
    value_scale,
)

RELAY = 0x0102000A
SYSTEM_BIT = 0x0203000B
DIMMER = 0x01040001
INPUT = 0x0101002F
TEMPERATURE = 0x01050017


def _entry(name: str, address: int = RELAY, **kwargs) -> Is3Entry:
    """An entry with a given name and address."""
    return Is3Entry(name=name, address=address, value=0, **kwargs)


@pytest.mark.parametrize(
    "name",
    [
        "imp_wled_pc",
        "imp_WOL_PC",
        "mobil_imp_1",
        "IMP_zvonek",
        "imp-wled-kuch",
    ],
)
def test_impulses_become_buttons(name: str) -> None:
    """An `imp` token marks a momentary impulse, which has no state to show."""
    entry = _entry(name)
    assert is_impulse(entry)
    assert platform_of(entry) == PLATFORM_BUTTON


@pytest.mark.parametrize("name", ["imp_zvonek", "mobil_imp_2"])
def test_impulses_work_on_system_bits_too(name: str) -> None:
    """Impulses are usually system bits rather than physical relays."""
    assert platform_of(_entry(name, SYSTEM_BIT)) == PLATFORM_BUTTON


@pytest.mark.parametrize(
    "name",
    [
        "Sv_loznice",
        "SV_prijezd",
        "sv_zrcadlo_tech",
        "Velke_sv_obyvak",
        "Sv-venku",
        # Supplementary lighting, named apart for the icon.
        "Lamp_loznice_1",
        "Nocni_lamp",
        "Sv_lamp_terasa",
        "Sv_zrc_dole",
        "Sv_zrcadlo_koup_patro",
    ],
)
def test_named_lights_become_lights(name: str) -> None:
    """Light words mark a relay as a light, in any casing or position."""
    entry = _entry(name)
    assert is_named_light(entry)
    assert platform_of(entry) == PLATFORM_LIGHT


@pytest.mark.parametrize(
    ("name", "icon"),
    [
        # Both spellings of the mirror light appear in the exports.
        ("Sv_zrc_dole", ICON_MIRROR),
        ("sv_zrcadlo_tech", ICON_MIRROR),
        ("Lamp_obyvak_1", ICON_LAMP),
        ("Nocni_lamp", ICON_LAMP),
        # A mirror light is usually also named Sv_, so the specific word wins.
        ("Sv_kp_zrcadlo", ICON_MIRROR),
        # Main lighting keeps the default bulb.
        ("Sv_loznice", None),
        ("Velke_sv_obyvak", None),
    ],
)
def test_supplementary_lighting_keeps_its_own_icon(name: str, icon) -> None:
    """The names differ to tell accent lighting apart, so the icons do too."""
    assert entity_icon(_entry(name)) == icon


@pytest.mark.parametrize("name", ["Vent_koup", "VENT_WC_2np", "Vent_sauna"])
def test_fans_get_a_fan_icon(name: str) -> None:
    """A `vent` relay reads as a fan."""
    entry = _entry(name)
    assert platform_of(entry) == PLATFORM_SWITCH
    assert entity_icon(entry) == ICON_FAN


@pytest.mark.parametrize("name", ["Zas_kuchyne", "ZAS_terasa", "zas_dilna"])
def test_sockets_get_a_socket_icon(name: str) -> None:
    """A `zas` relay stays a switch but reads as a power socket."""
    entry = _entry(name)
    assert platform_of(entry) == PLATFORM_SWITCH
    assert entity_icon(entry) == ICON_SOCKET


def test_socket_token_is_whole_word_only(name: str = "Zastineni_obyvak") -> None:
    """`zas` is a whole token, so `zastineni` (shading) gets no socket icon."""
    assert entity_icon(_entry(name)) is None


@pytest.mark.parametrize("name", ["LED_kuchyn", "LEDpas_ob", "Sv_LED_linka", "led_schody"])
def test_led_strips_are_lights_with_the_strip_icon(name: str) -> None:
    """An `LED` relay is a light, and gets the strip icon."""
    entry = _entry(name)
    assert is_named_light(entry)
    assert platform_of(entry) == PLATFORM_LIGHT
    assert entity_icon(entry) == ICON_LED


def test_led_on_a_dimmer_gets_the_strip_icon() -> None:
    """A dimmer named `LED_` is already a light; it still takes the strip icon."""
    assert entity_icon(_entry("LED_pas", DIMMER, unit="%")) == ICON_LED


def test_tl_and_din_inputs_are_press_buttons() -> None:
    """A digital input named `TL_`, or a bare `DIN`, is a momentary button --
    on the central unit's own inputs as much as a wall switch's."""
    tl = _entry("Tl_kumbal", INPUT, hw_id="In-Out-CU3-01M-CU3-02M_DIN1_0F0001")
    assert is_press_button(tl)
    din = _entry("DIN2", INPUT, hw_id="In-Out-CU3-01M-CU3-02M_DIN2_0F0001")
    assert is_press_button(din)
    # An ordinary input that is neither stays a plain binary sensor.
    other = _entry("Okno_loznice", INPUT, hw_id="SA3-022M_IN2_0F0002")
    assert not is_press_button(other)
    assert platform_of(other) == PLATFORM_BINARY_SENSOR


def test_lamp_icon_does_not_reach_a_switch() -> None:
    """`blok_noc_lamp` is a switch, so it must not pick up the lamp icon."""
    assert entity_icon(_entry("blok_noc_lamp", SYSTEM_BIT)) is None


def test_icons_are_confined_to_their_platform() -> None:
    """A fan word on a light, or a lamp word on a switch, is ignored."""
    # `vent` only means a fan on a switch; a dimmer named so is still a light.
    assert entity_icon(_entry("Vent_obyv", DIMMER, unit="%")) is None
    # Ordinary switches carry no icon.
    assert entity_icon(_entry("Stykac_napajeni")) is None


def test_light_words_on_a_system_bit_are_not_lights() -> None:
    """A program flag naming a light is not one.

    `blok_noc_lamp` is a switch that stops an automatic programme from running,
    not a light, and across seven installations it is the only system bit
    carrying a light word.
    """
    entry = _entry("blok_noc_lamp", SYSTEM_BIT)
    assert not is_named_light(entry)
    assert platform_of(entry) == PLATFORM_SWITCH


@pytest.mark.parametrize(
    "name",
    [
        # Merely containing the letters is not enough.
        "Impus_rolety_patro",
        "Kompresor",
        "Svod_vody",
        "Rozvadec",
        "TOP_rele_kuch",
        "Stykac_napajeni",
        "Vent_koup",
        "Zas_venku",
    ],
)
def test_other_names_stay_switches(name: str) -> None:
    """Whole tokens are matched, so ordinary names are left alone."""
    entry = _entry(name)
    assert not is_impulse(entry)
    assert not is_named_light(entry)
    assert platform_of(entry) == PLATFORM_SWITCH


def test_impulse_wins_over_light() -> None:
    """A name carrying both tokens is an impulse; it is fired, not held."""
    entry = _entry("imp_sv_chodba")
    assert is_impulse(entry)
    assert not is_named_light(entry)
    assert platform_of(entry) == PLATFORM_BUTTON


def test_dimmers_stay_lights_whatever_they_are_called() -> None:
    """A dimmer is a light by its address; the name changes nothing."""
    assert platform_of(_entry("Sv_obyv", DIMMER, unit="%")) == PLATFORM_LIGHT
    assert platform_of(_entry("Cokoliv", DIMMER, unit="%")) == PLATFORM_LIGHT


def test_naming_never_makes_an_input_writable() -> None:
    """The conventions only refine outputs, never promote a read-only entry.

    Calling an input `Sv_okno` must not turn it into something switchable.
    """
    assert platform_of(_entry("Sv_okno", INPUT)) == PLATFORM_BINARY_SENSOR
    assert platform_of(_entry("imp_teplota", TEMPERATURE, unit="°C")) == PLATFORM_SENSOR


def test_system_integers_are_numbers() -> None:
    """System integers are variables the unit's programme reads and writes."""
    entry = _entry("rychlost_vetru", 0x02020002)
    assert platform_of(entry) == PLATFORM_NUMBER
    assert is_writable(entry)
    assert is_readable(entry), "a writable value still has to be polled"


def test_naming_does_not_reach_system_integers() -> None:
    """A system integer stays a number whatever it is called."""
    assert platform_of(_entry("Sv_cosi", 0x02020002)) == PLATFORM_NUMBER
    assert platform_of(_entry("imp_cosi", 0x02020002)) == PLATFORM_NUMBER


@pytest.mark.parametrize("name", ["rychlost_vetru", "Therm_setpoint", "TIN_pamet"])
def test_system_integers_are_never_scaled(name: str) -> None:
    """A system integer holds a raw value, whatever it is called.

    What the number means is decided by the installer's programme, so there is
    no rule to apply. In particular the temperature guess, which repairs
    unit-less readings, must not reach a variable named after one.
    """
    entry = _entry(name, 0x02020002)
    assert effective_unit(entry) is None
    assert value_scale(entry) == 1


def test_every_entry_lands_on_at_most_one_platform() -> None:
    """platform_of is the single decision, so overlap is impossible by design."""
    for name in ("imp_x", "Sv_x", "Rele_x"):
        for address in (RELAY, SYSTEM_BIT, DIMMER, INPUT, TEMPERATURE):
            entry = _entry(name, address, unit="%" if address == DIMMER else None)
            assert platform_of(entry) in {
                PLATFORM_BUTTON,
                PLATFORM_LIGHT,
                PLATFORM_SWITCH,
                PLATFORM_BINARY_SENSOR,
                PLATFORM_SENSOR,
                None,
            }
