"""The set of entities an export is expected to produce.

Used to prune the registry of entities left behind when an output changed
platform. The platform is part of the key, because an output and its stale
predecessor share the address-based unique id.
"""

from __future__ import annotations

from pathlib import Path

from custom_components.is3_export.export import expected_entities, parse_export

FIXTURES = Path(__file__).parent / "fixtures"
ENTRY = "abc123"


def _load(name: str):
    """Parse a fixture."""
    return parse_export((FIXTURES / name).read_text(encoding="utf-8-sig"))


def test_system_integer_is_expected_as_a_number_only() -> None:
    """FX_wled_pc is a number now; a sensor with the same id is not expected.

    That mismatch is what lets the pruning remove the stale sensor while
    keeping the number.
    """
    export = _load("sample.is3")
    expected = expected_entities(export, ENTRY)

    unique_id = f"{ENTRY}_0x02020005"
    assert ("number", unique_id) in expected
    assert ("sensor", unique_id) not in expected


def test_every_platform_uses_the_entry_prefixed_id() -> None:
    """Unique ids carry the config entry id, matching what the entities set."""
    export = _load("sample.is3")
    for platform, unique_id in expected_entities(export, ENTRY):
        assert unique_id.startswith(f"{ENTRY}_")
        assert platform in {
            "button",
            "light",
            "switch",
            "number",
            "binary_sensor",
            "sensor",
            "cover",
        }


def test_a_blind_is_expected_as_a_cover_not_a_switch() -> None:
    """Cover addresses are claimed, so they are not also expected as switches."""
    export = _load("covers.is3")
    expected = expected_entities(export, ENTRY)

    covers = [key for key in expected if key[0] == "cover"]
    assert covers, "the fixture has blinds"

    # This site drives blinds through program bits, so the bits a blind uses
    # (0x02030000 up, 0x02030001 down of ZALUZIE_pokoj) must not also be
    # switches, even though a system bit would otherwise be one.
    switch_ids = {uid for platform, uid in expected if platform == "switch"}
    assert f"{ENTRY}_0x02030000" not in switch_ids
    assert f"{ENTRY}_0x02030001" not in switch_ids


def test_ignored_addresses_produce_nothing() -> None:
    """Plans and groups are not expected as any entity."""
    export = _load("other_install.is3")
    ids = {uid for _, uid in expected_entities(export, ENTRY)}
    for ignored in (0x05010001, 0x02040002, 0x02090002):
        assert f"{ENTRY}_0x{ignored:08x}" not in ids
