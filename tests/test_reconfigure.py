"""Reconfiguring a unit whose export has been republished.

The identity of a unit is read out of its export header -- and the export is
exactly what someone is replacing when they reconfigure after republishing a
project from IDM3. Treating a changed identity as "that is a different unit"
refused the one operation the form exists for.

What is still worth refusing is landing on an identity another entry already
owns, which really would point two entries at one unit.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from custom_components.is3_export.config_flow import (
    FALLBACK_PROBES,
    PROBE_COUNT,
    Is3ConfigFlow,
    _probe_addresses,
    unit_identity,
)
from custom_components.is3_export.export import Is3Entry, Is3Export, Is3Header
from custom_components.is3_export.source import Is3ExportError


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


class _Hass:
    """Just enough Home Assistant for the parts of the flow that touch files."""

    def __init__(self, folder=None) -> None:
        self.config = _Config(folder)

    async def async_add_executor_job(self, func, *args):
        return func(*args)


class _Config:
    def __init__(self, folder) -> None:
        self._folder = folder

    def path(self, *parts: str) -> str:
        return str(Path(self._folder).joinpath(*parts))


def _upload_flow(files: dict[str, str]) -> Is3ConfigFlow:
    """A flow whose file picker holds ``files``, deleting each as it is read.

    Home Assistant really does delete an uploaded file the moment the flow
    reads it, which is the whole reason the text has to be held on to.
    """
    flow = Is3ConfigFlow.__new__(Is3ConfigFlow)
    flow._uploaded_text = None
    flow.hass = _Hass()  # type: ignore[assignment]
    flow._read_uploaded_file = files.pop  # type: ignore[method-assign]
    return flow


EXPORT = "VERSION_1_ID_ABC_NAME_House\nSv_kuchyne 0x0102000A 0\n"
SECOND = "VERSION_1_ID_ABC_NAME_House\nSv_loznice 0x0102000B 0\n"


def test_an_upload_survives_a_form_that_came_back_with_an_error() -> None:
    """Otherwise correcting a password quietly reconfigures onto the old export.

    The file picker is empty by then -- Home Assistant deleted the file when it
    was read -- and the path field still holds the previous export, so the
    dialog would close on the wrong file and look like an upload that did
    nothing at all.
    """
    flow = _upload_flow({"upload-1": EXPORT})

    first = asyncio.run(flow._async_load_export({"export_upload": "upload-1"}))
    assert [e.name for e in first.entries] == ["Sv_kuchyne"]

    # Second submit: password corrected, file picker empty, old path still there.
    again = asyncio.run(
        flow._async_load_export({"export_file": "C:/nowhere/previous.is3"})
    )
    assert [e.name for e in again.entries] == ["Sv_kuchyne"]


def test_a_new_upload_replaces_the_one_being_held() -> None:
    flow = _upload_flow({"upload-1": EXPORT, "upload-2": SECOND})

    asyncio.run(flow._async_load_export({"export_upload": "upload-1"}))
    second = asyncio.run(flow._async_load_export({"export_upload": "upload-2"}))

    assert [e.name for e in second.entries] == ["Sv_loznice"]


def test_an_unreadable_new_upload_does_not_fall_back_to_the_old_one() -> None:
    """The form has just said that file is no good; accepting the previous one
    on the next submit would be accepting a file it rejected."""
    flow = _upload_flow({"upload-1": EXPORT, "upload-2": "nothing parseable here"})

    asyncio.run(flow._async_load_export({"export_upload": "upload-1"}))
    try:
        asyncio.run(flow._async_load_export({"export_upload": "upload-2"}))
    except Is3ExportError:
        pass
    else:
        raise AssertionError("an export with no entries must be rejected")

    assert flow._uploaded_text is None


def test_a_republished_export_overwrites_the_file_the_entry_already_reads(
    tmp_path,
) -> None:
    """The name is made from the installation id, and republishing is exactly
    what changes that id -- so a new file per republish, with nothing to say
    which one is live."""
    flow = Is3ConfigFlow.__new__(Is3ConfigFlow)
    flow.hass = _Hass(tmp_path)  # type: ignore[assignment]

    first = flow._write_saved_export("ORIGINAL-ID", EXPORT, "")
    second = flow._write_saved_export("REPUBLISHED-ID", SECOND, first)

    assert second == first
    assert Path(first).read_text(encoding="utf-8") == SECOND
    assert sorted(p.name for p in Path(tmp_path, "is3_export").iterdir()) == [
        "original_id.is3"
    ]


def test_a_path_outside_our_own_folder_is_never_written_over(tmp_path) -> None:
    """Only the folder this integration keeps its own copies in is fair game."""
    flow = Is3ConfigFlow.__new__(Is3ConfigFlow)
    flow.hass = _Hass(tmp_path)  # type: ignore[assignment]
    elsewhere = tmp_path / "installer" / "backup.is3"

    written = flow._write_saved_export("UNIT-ID", EXPORT, str(elsewhere))

    assert Path(written).name == "unit_id.is3"
    assert not elsewhere.exists()
