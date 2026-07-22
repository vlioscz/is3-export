"""Assembling blinds out of the addresses an export exposes."""

from __future__ import annotations

from pathlib import Path

import pytest

import asyncio

from homeassistant.helpers.update_coordinator import CoordinatorEntity

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
