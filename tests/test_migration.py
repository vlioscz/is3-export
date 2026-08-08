"""Upgrading an installation set up by 0.1.x.

The transport changed, so every 0.1.x entry points at a port nothing listens on
any more and carries two settings that no longer mean anything.  Rewriting them
in place is what keeps entity ids, areas and recorder history: the unique id is
untouched, so Home Assistant sees the same unit it always did.
"""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT

from custom_components.is3_export import migrated_data
from custom_components.is3_export.config_flow import Is3ConfigFlow
from custom_components.is3_export.const import (
    CONF_DELIMITER,
    CONF_EXPORT_FILE,
    CONF_NUMBER_BASE,
    DEFAULT_PORT,
)

# What 0.1.x wrote for a typical install.
OLD_ENTRY = {
    CONF_HOST: "192.168.1.10",
    CONF_PORT: 22272,
    CONF_DELIMITER: " ",
    CONF_NUMBER_BASE: "hex",
    CONF_EXPORT_FILE: "",
}


def test_the_old_port_is_replaced() -> None:
    """22272 was the ASCII port; nothing answers there now."""
    assert migrated_data(OLD_ENTRY)[CONF_PORT] == DEFAULT_PORT


def test_any_port_is_replaced_not_just_the_documented_ones() -> None:
    """The port was configurable, so a hand-set one is just as dead."""
    hand_set = dict(OLD_ENTRY, **{CONF_PORT: 4242})
    assert migrated_data(hand_set)[CONF_PORT] == DEFAULT_PORT


def test_the_retired_settings_are_dropped() -> None:
    """Delimiter and number base configured a protocol that is gone."""
    migrated = migrated_data(OLD_ENTRY)
    assert CONF_DELIMITER not in migrated
    assert CONF_NUMBER_BASE not in migrated


def test_an_empty_password_is_added() -> None:
    """Which is what authorizes a unit with no password set -- the usual case."""
    assert migrated_data(OLD_ENTRY)[CONF_PASSWORD] == ""


def test_a_password_already_set_is_kept() -> None:
    """Migrating twice, or over a half-upgraded entry, must not blank it."""
    with_password = dict(OLD_ENTRY, **{CONF_PASSWORD: "hunter2"})
    assert migrated_data(with_password)[CONF_PASSWORD] == "hunter2"


def test_the_host_and_export_survive() -> None:
    """Everything the new transport still needs is carried over untouched."""
    migrated = migrated_data(dict(OLD_ENTRY, **{CONF_EXPORT_FILE: "/config/a.is3"}))
    assert migrated[CONF_HOST] == "192.168.1.10"
    assert migrated[CONF_EXPORT_FILE] == "/config/a.is3"


def test_migrating_is_idempotent() -> None:
    """Running it over an already-migrated entry changes nothing further."""
    once = migrated_data(OLD_ENTRY)
    assert migrated_data(once) == once


def test_the_original_is_not_mutated() -> None:
    """Config entry data is read-only; the rewrite must return a new dict."""
    before = dict(OLD_ENTRY)
    migrated_data(OLD_ENTRY)
    assert OLD_ENTRY == before


def test_the_flow_version_matches_what_the_migration_writes() -> None:
    """A mismatch here means Home Assistant migrates on every single start."""
    assert Is3ConfigFlow.VERSION == 2
    assert Is3ConfigFlow.MINOR_VERSION == 1
