"""The diagnostics snapshot lists every entry and leaks no identity.

It is meant to be attached to a bug report, so the host, credentials and the
unit's own name/id are masked -- while the entry names and hardware ids, which
are what a classification question is about, are kept.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from custom_components.is3_export.diagnostics import REDACTED, build_diagnostics
from custom_components.is3_export.export import parse_export

FIXTURES = Path(__file__).parent / "fixtures"


def _export(name: str):
    return parse_export((FIXTURES / name).read_text(encoding="utf-8-sig"))


def _coordinator(export, values):
    return SimpleNamespace(
        data=SimpleNamespace(export=export, values=values),
        reads_supported=True,
        client=SimpleNamespace(connected=True, delimiter=" "),
    )


def _entry(data):
    return SimpleNamespace(data=data)


_CONFIG = {
    "host": "192.168.1.10",
    "port": 1111,
    "export_file": "",
    "delimiter": " ",
    "number_base": "hex",
}


def test_lists_every_entry_and_reports_a_live_value() -> None:
    export = _export("sample.is3")
    first = export.entries[0]
    coordinator = _coordinator(export, {first.address: 1})

    diag = build_diagnostics(_entry(dict(_CONFIG)), coordinator)

    assert diag["summary"]["entry_count"] == len(export.entries)
    assert len(diag["entries"]) == len(export.entries)

    reported = {e["address"]: e for e in diag["entries"]}
    assert reported[first.address_hex]["value"] == 1
    # every entry carries a platform key and a flags list
    assert all("platform" in e and isinstance(e["flags"], list) for e in diag["entries"])


def test_redacts_host_and_installation_identity() -> None:
    export = _export("sample.is3")  # header: ID_44444444 NAME_Test-house
    coordinator = _coordinator(export, {})

    diag = build_diagnostics(_entry(dict(_CONFIG)), coordinator)

    assert diag["config"]["host"] == REDACTED
    assert diag["header"]["name"] == REDACTED
    assert diag["header"]["unit_id"] == REDACTED
    # nothing identifying survives anywhere in the payload
    blob = json.dumps(diag)
    assert "192.168.1.10" not in blob
    assert "Test-house" not in blob
    assert "44444444" not in blob


def test_keeps_the_functional_payload_for_support() -> None:
    export = _export("sample.is3")
    coordinator = _coordinator(export, {})

    diag = build_diagnostics(_entry(dict(_CONFIG)), coordinator)

    # names and hardware ids are the whole point of a classification report
    first = export.entries[0]
    entry = next(e for e in diag["entries"] if e["address"] == first.address_hex)
    assert entry["name"] == first.name
    assert entry["hw_id"] == first.hw_id
    # capabilities and the version header are preserved
    assert diag["capabilities"]["reads_supported"] is True
    assert diag["header"]["idm3"] == "03-03-34"
