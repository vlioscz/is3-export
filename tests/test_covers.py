"""Assembling blinds out of the addresses an export exposes."""

from __future__ import annotations

from pathlib import Path

import pytest

import asyncio

from homeassistant.components.cover import CoverEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.is3_export.errors import Is3ConnectionError

from custom_components.is3_export import cover as cover_module
from custom_components.is3_export import number as number_module
from custom_components.is3_export.cover import Is3CoverEntity, needs_release_first
from custom_components.is3_export.export import (
    find_covers,
    is_switchable,
    parse_export,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    """Parse a fixture."""
    return parse_export((FIXTURES / name).read_text(encoding="utf-8-sig"))


@pytest.fixture(name="export")
def export_fixture():
    """A site with both blind conventions and a repurposed blind channel."""
    return _load("covers.is3")


@pytest.fixture(name="covers")
def covers_fixture(export):
    """Blinds keyed by name."""
    return {cover.name: cover for cover in find_covers(export)}


def test_program_bits_win_over_relays(export, covers) -> None:
    """A site driving blinds through program bits must not get relay blinds too.

    The relays and the bits move the same motor, so building both would show
    every blind twice.
    """
    assert all(cover.source == "systembit" for cover in covers.values())
    assert set(covers) == {"ZALUZIE_pokoj", "ZAL_kuchyn"}


def test_cover_subscribes_to_its_direction_channels(covers, monkeypatch) -> None:
    """The blind wakes on its own up/down channels, not the coordinator's blanket
    refresh -- so it updates live once that refresh stops waking every entity."""

    async def _noop(self) -> None:
        return None

    monkeypatch.setattr(CoordinatorEntity, "async_added_to_hass", _noop)

    subscribed: list[int] = []

    class _Coord:
        def async_add_address_listener(self, address, _cb):
            subscribed.append(address)
            return lambda: None

    cover = covers["ZALUZIE_pokoj"]
    entity = Is3CoverEntity.__new__(Is3CoverEntity)
    entity.cover = cover
    entity.coordinator = _Coord()
    entity.async_on_remove = lambda _fn: None
    entity.async_write_ha_state = lambda: None

    asyncio.run(entity.async_added_to_hass())
    assert set(subscribed) == {cover.open.address, cover.close.address}


def test_first_naming_convention(covers) -> None:
    """`..._Bit_Pohyb_Nahoru_0000` and friends."""
    cover = covers["ZALUZIE_pokoj"]
    assert cover.open.address == 0x02030000
    assert cover.close.address == 0x02030001
    assert cover.tilt_open.address == 0x02030002
    assert cover.tilt_close.address == 0x02030003
    assert cover.stop.address == 0x02030004
    assert cover.has_tilt


def test_second_naming_convention(covers) -> None:
    """`..._bit_ZAL_cuk_dolu_000A` and friends, which also list out of order."""
    cover = covers["ZAL_kuchyn"]
    assert cover.open.address == 0x02030009
    assert cover.close.address == 0x0203000D
    assert cover.tilt_open.address == 0x0203000B
    assert cover.tilt_close.address == 0x0203000A
    assert cover.stop.address == 0x0203000C
    assert cover.has_tilt


def test_auxiliary_stop_is_not_used(covers) -> None:
    """Blind programs expose a second stop bit; the primary one is enough."""
    assert covers["ZALUZIE_pokoj"].stop.address == 0x02030004
    assert covers["ZAL_kuchyn"].stop.address == 0x0203000C


def test_auxiliary_bit_is_claimed_not_exposed(covers) -> None:
    """The auxiliary interrupt is internal to the blind program, so it is
    consumed rather than left to surface as its own switch."""
    pokoj = covers["ZALUZIE_pokoj"]
    assert pokoj.internal == (0x02030005,)
    # Consumed means it counts among the cover's addresses, so the switch
    # platform skips it, but it is not one of the driven controls.
    assert 0x02030005 in pokoj.addresses
    assert 0x02030005 not in {pokoj.open.address, pokoj.close.address, pokoj.stop.address}
    assert covers["ZAL_kuchyn"].internal == (0x0203000E,)


def test_unrelated_system_bits_are_not_blinds(export, covers) -> None:
    """Dimmer memory and blocking flags share the system bit range."""
    claimed = {a for c in covers.values() for a in c.addresses}
    assert 0x02030063 not in claimed  # Stmivac_sauna_Nastaveni_pameti
    assert 0x0203003A not in claimed  # blok_auto_rol


def test_blind_addresses_are_not_also_switches(export, covers) -> None:
    """A blind must be drivable one way only, not two."""
    claimed = {a for c in covers.values() for a in c.addresses}
    switches = {
        e.address
        for e in export.entries
        if is_switchable(e) and e.address not in claimed
    }
    assert not (claimed & switches)


def test_no_address_serves_two_blinds(covers) -> None:
    """Overlapping groups would make one blind move another."""
    seen: set[int] = set()
    for cover in covers.values():
        for address in cover.addresses:
            assert address not in seen
            seen.add(address)


# --- Relay pairs, used when a site has no blind program ----------------------

RELAYS_ONLY = """VERSION_01-03-03_ID_ABC_NAME_Relays
Up_zaluzie_A JA3-018M_Up1_0C0001 0x01020006 0x00000000
Down_zaluzie_A JA3-018M_Down1_0C0001 0x01020007 0x00000000
Up2 JA3-018M_Up2_0C0001 0x01020008 0x00000000
Down2 JA3-018M_Down2_0C0001 0x01020009 0x00000000
NIC JA3-014M_Up5_0C0002 0x01020097 0x00000000
Svetlo_chodba JA3-014M_Down5_0C0002 0x01020098 0x00000000
_ JA3-018M_Up9_0C0001 0x01020016 0x00000000
_ JA3-018M_Down9_0C0001 0x01020017 0x00000000
Svetlo_venku SA3-06M_RE1_0C0003 0x0102002B 0x00000000
"""


@pytest.fixture(name="relay_covers")
def relay_covers_fixture():
    """Blinds from a site whose export has no blind program bits."""
    return {cover.name: cover for cover in find_covers(parse_export(RELAYS_ONLY))}


def test_relay_pairs_become_blinds(relay_covers) -> None:
    """Up and down on the same driver channel are one blind."""
    cover = relay_covers["zaluzie_A"]
    assert cover.source == "relay"
    assert cover.open.address == 0x01020006
    assert cover.close.address == 0x01020007
    assert cover.stop is None
    assert not cover.has_tilt


def test_bare_channel_numbers_pair_up(relay_covers) -> None:
    """`Up2` and `Down2` say nothing but the channel, which is enough."""
    assert "Up2" in relay_covers or "2" in str(list(relay_covers))
    assert any(c.open.address == 0x01020008 for c in relay_covers.values())


def test_unnamed_channels_pair_up(relay_covers) -> None:
    """An unnamed pair is still a blind, named after its channel."""
    assert any(c.open.address == 0x01020016 for c in relay_covers.values())


def test_repurposed_channel_is_not_a_blind(relay_covers) -> None:
    """`Up5` labelled NIC with `Down5` switching a light is not a blind.

    Blind drivers get reused as ordinary relays; pairing these would offer a
    blind that actually toggles a corridor light.
    """
    addresses = {a for c in relay_covers.values() for a in c.addresses}
    assert 0x01020097 not in addresses
    assert 0x01020098 not in addresses


def test_ordinary_relays_are_left_alone(relay_covers) -> None:
    """A relay that is not on a blind driver stays a switch."""
    addresses = {a for c in relay_covers.values() for a in c.addresses}
    assert 0x0102002B not in addresses


# --- Relay pairs whose direction is in the name (SA modules, not JA) ---------
#
# A blind need not sit on a JA3 driver.  On a plain relay module two outputs are
# wired to one motor and interlocked, and only the entry name says which way each
# runs -- the hardware id is a bare `SA3-04M_RE3_<serial>`.  Seen on a real
# box-module installation.

NAMED_RELAYS = """VERSION_01-03-03_ID_DEF_NAME_Named
Roleta_loznice_UP SA3-04M_RE3_0C0004 0x01020021 0x00000000
Roleta_loznice_DOWN SA3-04M_RE4_0C0004 0x01020022 0x00000000
Roleta_koupelna_UP SA3-04M_RE1_0C0004 0x0102001F 0x00000000
Roleta_koupelna_DOWN SA3-04M_RE2_0C0004 0x01020020 0x00000000
Dvere_ter_UP SA3-04M_RE1_0C0005 0x0102001B 0x00000000
Dvere_ter_DOWN SA3-04M_RE2_0C0005 0x0102001C 0x00000000
Vent_koup_UP SA3-06M_RE3_0C0006 0x01020017 0x00000000
Sv_koup SA3-06M_RE1_0C0006 0x01020015 0x00000000
Half_UP SA3-04M_RE1_0AAAAA 0x010200E1 0x00000000
Half_DOWN SA3-04M_RE1_0BBBBB 0x010200E2 0x00000000
Rol_UP_pokoj SA3-02B_RE1_0C0009 0x010200F6 0x00000000
Rol_DOWN_pokoj SA3-02B_RE2_0C0009 0x010200F7 0x00000000
"""


@pytest.fixture(name="named_covers")
def named_covers_fixture():
    """Blinds from a site whose relays carry the direction in the name."""
    return {cover.name: cover for cover in find_covers(parse_export(NAMED_RELAYS))}


def test_named_relay_pairs_become_blinds(named_covers) -> None:
    """`Roleta_loznice_UP` and `_DOWN` on one module are one relay blind."""
    cover = named_covers["Roleta_loznice"]
    assert cover.source == "relay"
    assert cover.open.address == 0x01020021
    assert cover.close.address == 0x01020022
    assert cover.stop is None  # stop comes from releasing both relays
    assert not cover.has_tilt


def test_two_named_blinds_on_one_module_stay_separate(named_covers) -> None:
    """The base name, not the module, tells two blinds on one board apart."""
    assert named_covers["Roleta_loznice"].open.address == 0x01020021
    assert named_covers["Roleta_koupelna"].open.address == 0x0102001F
    assert named_covers["Dvere_ter"].open.address == 0x0102001B


def test_direction_in_the_middle_of_the_name_pairs(named_covers) -> None:
    """`Rol_UP_pokoj` / `Rol_DOWN_pokoj` pair, direction word and all.

    A box relay module writes the direction in the middle of the name rather than
    as a suffix; it is matched as a whole token wherever it sits and removed to
    get the base the two halves share.
    """
    cover = named_covers["Rol_pokoj"]
    assert cover.source == "relay"
    assert cover.open.address == 0x010200F6
    assert cover.close.address == 0x010200F7


def test_a_lone_direction_is_not_a_blind(named_covers) -> None:
    """`_UP` with no matching `_DOWN` stays an ordinary switch."""
    claimed = {a for c in named_covers.values() for a in c.addresses}
    assert 0x01020017 not in claimed


def test_a_plain_relay_is_left_alone(named_covers) -> None:
    """A relay with no direction in its name is not swept into a blind."""
    claimed = {a for c in named_covers.values() for a in c.addresses}
    assert 0x01020015 not in claimed


def test_a_name_reused_across_modules_is_not_paired(named_covers) -> None:
    """The interlock is wired on one module, so the two halves must share it.

    `Half_UP` and `Half_DOWN` share a base name but sit on different modules, so
    pairing them would offer a blind whose directions cannot actually block each
    other; they stay two switches instead.
    """
    claimed = {a for c in named_covers.values() for a in c.addresses}
    assert 0x010200E1 not in claimed
    assert 0x010200E2 not in claimed


# --- Two heating outputs are not a blind ------------------------------------
#
# A house with an upstairs and a downstairs heating zone names their output
# relays `..._up` and `..._down`, on one module, off one base name -- which is
# exactly the shape a blind has.  Pairing them offered a cover whose "open"
# turned one room's heating on and the other's off.  Found on a live
# installation; the names below have the same shape as the real ones.

HEATING_LOOKALIKE = """VERSION_01-03-03_ID_GHI_NAME_Heating
TOP_schd_up Controller_0C0007 0x0003001D 0x00000000
Control-IN Controller_Control-IN_0C0007 0x01110001 0x00000000
Actual-Therm-AOUT Controller_Actual-Therm-AOUT_0C0007 0x01080007 0x00000000
Required-Therm-AOUT Controller_Required-Therm-AOUT_0C0007 0x01080008 0x00000000
Manual-Therm-AIN Controller_Manual-Therm-AIN_0C0007 0x01120007 0x00000000
Required-Heat-DOUT Controller_Required-Heat-DOUT_0C0007 0x01010031 0x00000000
Control-Manual-IN Controller_Control-Manual-IN_0C0007 0x01110004 0x00000000
TOP_schd_down Controller_0C0008 0x0003001E 0x00000000
Control-IN Controller_Control-IN_0C0008 0x01110005 0x00000000
Actual-Therm-AOUT Controller_Actual-Therm-AOUT_0C0008 0x0108000D 0x00000000
Required-Therm-AOUT Controller_Required-Therm-AOUT_0C0008 0x0108000E 0x00000000
Manual-Therm-AIN Controller_Manual-Therm-AIN_0C0008 0x01120013 0x00000000
Required-Heat-DOUT Controller_Required-Heat-DOUT_0C0008 0x0101003B 0x00000000
Control-Manual-IN Controller_Control-Manual-IN_0C0008 0x01110008 0x00000000
TOP_rele_schd_up SA3-012M_RE7_0C0009 0x01020007 0x00000000
TOP_rele_schd_down SA3-012M_RE8_0C0009 0x01020008 0x00000000
Roleta_loznice_UP SA3-04M_RE1_0C000A 0x01020021 0x00000000
Roleta_loznice_DOWN SA3-04M_RE2_0C000A 0x01020022 0x00000000
"""


@pytest.fixture(name="heating_covers")
def heating_covers_fixture():
    """Blinds from a site whose heating relays look like a blind pair."""
    return {c.name: c for c in find_covers(parse_export(HEATING_LOOKALIKE))}


def test_two_heating_outputs_are_not_paired_into_a_blind(heating_covers) -> None:
    """Each half answers to a different zone, so they drive two rooms, not one motor."""
    claimed = {a for c in heating_covers.values() for a in c.addresses}
    assert 0x01020007 not in claimed
    assert 0x01020008 not in claimed


def test_a_real_blind_on_the_same_site_still_pairs(heating_covers) -> None:
    """The check must not cost the site its actual blinds."""
    assert "Roleta_loznice" in heating_covers
    assert heating_covers["Roleta_loznice"].open.address == 0x01020021


def test_heating_relays_stay_switches(heating_covers) -> None:
    """Dropped from the cover, they are still perfectly good switches."""
    export = parse_export(HEATING_LOOKALIKE)
    claimed = {a for c in find_covers(export) for a in c.addresses}
    relays = [
        e for e in export.entries
        if e.address in (0x01020007, 0x01020008) and is_switchable(e)
    ]
    assert len(relays) == 2
    assert not {e.address for e in relays} & claimed


# --- The timed release covers every relay blind, JA3 and named alike --------
#
# The drive principle is identical on both conventions -- a JA3 board merely
# hard-wires the direction interlock -- so the auto-release after the travel
# time (and the travel-time number that feeds it) must not depend on which
# convention paired the relays.  All three modules meet on the one source
# string ``relay``.


class _FakeConfigEntry:
    entry_id = "entry"
    title = "Unit"


class _FakeCoordinator:
    config_entry = _FakeConfigEntry()
    cover_travel_times: dict[int, float] = {}


def test_every_relay_blind_is_timed(relay_covers, named_covers) -> None:
    """A JA3 pair and a named pair both get the timed release and stop."""
    assert relay_covers and named_covers
    for cover in (*relay_covers.values(), *named_covers.values()):
        assert cover.source == cover_module.RELAY == number_module.RELAY
        entity = Is3CoverEntity(_FakeCoordinator(), cover)
        assert entity._timed
        assert entity.supported_features & CoverEntityFeature.STOP


def test_a_program_blind_is_not_timed(covers) -> None:
    """The unit's blind program times its own moves; nothing to release here."""
    entity = Is3CoverEntity(_FakeCoordinator(), covers["ZALUZIE_pokoj"])
    assert not entity._timed


# --- Stopping is one packet, not two writes ---------------------------------


class _RecordingClient:
    """Records what reached the unit, and how it was grouped into packets."""

    def __init__(self, fail: bool = False) -> None:
        self.packets: list[list[tuple[str, int]]] = []
        self._fail = fail

    async def async_set_many(self, writes: list[tuple[str, int]]) -> None:
        if self._fail:
            raise Is3ConnectionError("unreachable")
        self.packets.append(list(writes))

    async def async_set(self, address: str, value: int) -> None:  # pragma: no cover
        raise AssertionError("stopping must not fall back to single writes")


class _StopCoordinator:
    config_entry = _FakeConfigEntry()
    cover_travel_times: dict[int, float] = {}

    def __init__(self, client) -> None:
        self.client = client
        self.noted: list[tuple[int, int]] = []

    def async_note_write(self, address: int, value: int) -> None:
        self.noted.append((address, value))


def _stopping_cover(client) -> Is3CoverEntity:
    cover = next(
        c for c in find_covers(parse_export(NAMED_RELAYS)) if c.name == "Roleta_loznice"
    )
    entity = Is3CoverEntity(_StopCoordinator(client), cover)
    entity.async_write_ha_state = lambda: None
    return entity


def test_stopping_releases_both_directions_in_one_packet() -> None:
    """Two writes could land half-done, and a blind with one relay still closed
    keeps running with nothing left to stop it.  The unit takes a packet whole."""
    client = _RecordingClient()
    entity = _stopping_cover(client)

    asyncio.run(entity.async_stop_cover())

    assert len(client.packets) == 1, "the stop went out as more than one packet"
    assert client.packets[0] == [("0x01020021", 0), ("0x01020022", 0)]
    assert entity.coordinator.noted == [(0x01020021, 0), (0x01020022, 0)]


def test_the_timed_release_also_goes_out_as_one_packet() -> None:
    """The auto-stop after the travel time runs the same path."""
    client = _RecordingClient()
    entity = _stopping_cover(client)
    entity._stop_unsub = None

    asyncio.run(entity._async_auto_stop())

    assert len(client.packets) == 1
    assert client.packets[0] == [("0x01020021", 0), ("0x01020022", 0)]


def test_a_failed_stop_is_reported_not_swallowed() -> None:
    """A stop that did not reach the unit must not look like it worked."""
    entity = _stopping_cover(_RecordingClient(fail=True))

    with pytest.raises(HomeAssistantError):
        asyncio.run(entity.async_stop_cover())


# --- Reversing --------------------------------------------------------------
#
# On a relay pair, 1 runs the motor and 0 stops it, but the two directions are
# interlocked in hardware, so a command's effect can depend on the other
# relay's state.


def test_reversing_a_running_relay_releases_the_other_first() -> None:
    """Driving against a running direction must release it first."""
    assert needs_release_first("relay", 1)


def test_driving_from_standstill_is_a_single_write() -> None:
    """Nothing to release when the other direction is already off."""
    assert not needs_release_first("relay", 0)
    assert not needs_release_first("relay", None)


@pytest.mark.parametrize("other", [0, 1, None])
def test_program_bits_never_need_a_release(other) -> None:
    """Program bits are commands to the blind program, which handles the motor."""
    assert not needs_release_first("systembit", other)
