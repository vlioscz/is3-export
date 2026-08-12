"""Reconfiguring a unit whose export has been republished.

The identity of a unit is read out of its export header -- and the export is
exactly what someone is replacing when they reconfigure after republishing a
project from IDM3. Treating a changed identity as "that is a different unit"
refused the one operation the form exists for.

What is still worth refusing is landing on an identity another entry already
owns, which really would point two entries at one unit.
"""

from __future__ import annotations

from custom_components.is3_export.config_flow import (
    FALLBACK_PROBES,
    PROBE_COUNT,
    Is3ConfigFlow,
    _probe_addresses,
    unit_identity,
)
from custom_components.is3_export.export import Is3Entry, Is3Export, Is3Header


class _Entry:
    def __init__(self, entry_id: str, unique_id: str) -> None:
        self.entry_id = entry_id
        self.unique_id = unique_id


def _flow(entries: list[_Entry]) -> Is3ConfigFlow:
    flow = Is3ConfigFlow.__new__(Is3ConfigFlow)
    flow._async_current_entries = lambda: entries  # type: ignore[method-assign]
    return flow


def test_a_republished_project_is_not_a_different_unit() -> None:
    """The header id changed because the project did; the unit is the same."""
    flow = _flow([_Entry("this", "OLD-PROJECT-ID")])

    assert not flow._owned_by_another_entry("NEW-PROJECT-ID", "this")


def test_an_identity_another_entry_owns_is_refused() -> None:
    """That would leave two entries driving one unit."""
    flow = _flow([_Entry("this", "OLD"), _Entry("other", "THE-OTHER-UNIT")])

    assert flow._owned_by_another_entry("THE-OTHER-UNIT", "this")


def test_keeping_your_own_identity_is_fine() -> None:
    """Reconfiguring without touching the export must not trip over itself."""
    flow = _flow([_Entry("this", "SAME")])

    assert not flow._owned_by_another_entry("SAME", "this")


def test_the_form_probes_several_addresses() -> None:
    """One address was a single point of failure for the whole dialog.

    A unit that answered the handshake but not that one address was reported as
    unreachable -- while the running integration was talking to it perfectly.
    """
    export = Is3Export(
        entries=[
            Is3Entry(name="Sv_a", address=0x0102000A, value=0),
            Is3Entry(name="Sv_b", address=0x0102000B, value=0),
            Is3Entry(name="Teplota", address=0x01050001, value=0, unit="°C"),
        ]
    )

    probes = _probe_addresses(export)

    assert len(probes) == 3, probes
    assert "0x0102000A" in probes


def test_the_probe_batch_stays_small_enough_for_one_datagram() -> None:
    """It is one request; asking for hundreds would defeat the point."""
    export = Is3Export(
        entries=[
            Is3Entry(name=f"Sv_{i}", address=0x01020000 + i, value=0)
            for i in range(200)
        ]
    )

    assert len(_probe_addresses(export)) == PROBE_COUNT


def test_there_is_always_something_to_probe() -> None:
    """Re-authentication has no export loaded, and an export may hold nothing
    readable at all."""
    assert _probe_addresses(None) == list(FALLBACK_PROBES)
    assert _probe_addresses(Is3Export(entries=[])) == list(FALLBACK_PROBES)


def test_identity_falls_back_to_the_host() -> None:
    """An export with no header id still has to identify something."""
    headerless = Is3Export(header=None)

    assert unit_identity(headerless, "192.168.1.10")[0] == "192.168.1.10"


def test_identity_comes_from_the_header_when_there_is_one() -> None:
    export = Is3Export(header=Is3Header(unit_id="ABCDEF", name="House"))

    assert unit_identity(export, "192.168.1.10") == ("ABCDEF", "House")
