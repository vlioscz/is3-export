#!/usr/bin/env python3
"""Record what this integration depends on in a unit, so a firmware update can
be checked against it.

The protocol here was recovered by observation, not from a specification. That
means a firmware update can change it, and nothing warns you in advance. This
takes a fingerprint of every assumption the integration actually makes -- the
packet header, the checksum, the shape of each reply, the value encodings --
and can compare two fingerprints and say which of them moved.

Run it **before** updating a unit's firmware, keep the file, and run it again
afterwards:

    python tools/compat_check.py 192.168.1.10 --save before.json
    # ... update the unit ...
    python tools/compat_check.py 192.168.1.10 --compare before.json

It writes nothing but addresses, byte shapes and version strings -- no device
names, no project name, nothing identifying the installation. It only reads.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import struct
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.is3_export import checksum, protocol as proto  # noqa: E402
from custom_components.is3_export.client import Is3Client  # noqa: E402
from custom_components.is3_export.errors import Is3Error  # noqa: E402
from custom_components.is3_export.export import (  # noqa: E402
    is_readable,
    parse_export,
    platform_of,
)

# What each check means if it changes, in plain terms.  The point of the tool is
# not the bytes -- it is knowing which change costs you what.
MEANING = {
    "protocol_version": "the 8 bytes every packet opens with. If this moves, "
    "nothing works at all until the integration is updated.",
    "protocol_info": "the unit's own table of protocol versions. A change here "
    "is the clearest early warning that the wire format was touched.",
    "unit_info": "the handshake reply. A change may be harmless, but it is the "
    "same conversation the connection depends on.",
    "state_len": "the length of the run-state reply. The unit's clock is read "
    "out of it, so a change moves the clock fields.",
    "checksum_ok": "whether the unit's own replies still validate against the "
    "checksum model. If this fails, every packet is rejected.",
    "header_len": "where a packet's data starts. Everything is read relative "
    "to this.",
    "read_shape": "a value read comes back as count + one 32-bit value per "
    "address asked for. Entity states are decoded from exactly that.",
    "no_value_marker": "how the unit says an address has no value. If this "
    "changes, blank readings could appear as huge numbers.",
    "export_version": "the export file format the device list is parsed from.",
    "idm3_version": "the configuration software version that wrote the export. "
    "Not a protocol fact, but the best single label for 'what this unit runs'.",
    "address_types": "how many addresses of each type the export holds. The "
    "type byte is how every entity is classified, so a type appearing or "
    "vanishing changes what you get in Home Assistant.",
    "entries": "how many addresses the export lists at all.",
    "readable": "how many of them the integration would read.",
    "classified": "how many become an entity. A drop here means entities "
    "disappeared -- either the project changed, or the classifier no longer "
    "recognises something it used to.",
    "state_decodes": "whether the unit's clock could still be read out of the "
    "run-state reply.",
    "host_port": "the port this fingerprint was taken on.",
}


async def snapshot(host: str, port: int, password: str) -> dict:
    """Read everything the integration's assumptions rest on."""
    out: dict[str, object] = {"host_port": port}

    client = Is3Client(host, port, password)
    await client.async_connect()
    try:
        out["protocol_version"] = proto.PROTOCOL_VERSION.hex()
        out["header_len"] = proto.HEADER_LEN
        out["no_value_marker"] = f"0x{proto.NO_VALUE_THRESHOLD:08X}"

        reply = await client._send_once(proto.T_STARTSTOP, 0x05, 0x00, auth=False)
        out["protocol_info"] = reply.data.hex() if reply else None
        out["checksum_ok"] = bool(reply and reply.crc_ok)

        reply = await client._send_once(
            proto.T_UNITINFO, 0x03, 0x02, proto.UNIT_INFO_DATA, auth=False
        )
        out["unit_info"] = reply.data.hex() if reply else None

        reply = await client._send_once(proto.T_STARTSTOP, 0x03, 0x00, auth=False)
        out["state_len"] = len(reply.data) if reply else None
        # The run-mode byte: 0x20/0x10/0x00 are known; anything else (a unit
        # seen half-started reported 0x30) is a firmware/state difference worth
        # catching in a diff.
        out["run_state_byte"] = (
            f"0x{reply.data[0]:02X}" if reply and reply.data else None
        )
        state = proto.parse_unit_state(reply) if reply else None
        out["state_decodes"] = bool(state and state.clock)

        # A read of three addresses must answer with three values.
        probes = [0x01020001, 0x01050001, 0x01080001]
        reply = await client._data_request(
            proto.T_GET, 0x01, 0x00, proto.get_values_data(probes)
        )
        if reply is None:
            out["read_shape"] = "no reply"
        else:
            body = reply.data
            out["read_shape"] = (
                f"count={body[0] if body else '?'} bytes={len(body)} "
                f"expected={1 + 4 * len(probes)}"
            )
    finally:
        await client.async_close()

    out.update(await _export_snapshot(host))
    return out


async def _export_snapshot(host: str) -> dict:
    """The export's format and what the classifier makes of it."""
    url = f"http://{host}/immfiles/export.is3"
    try:
        raw = await asyncio.get_running_loop().run_in_executor(
            None, lambda: urllib.request.urlopen(url, timeout=10).read()
        )
    except (urllib.error.URLError, OSError) as err:
        return {"export_version": f"not served ({err})", "idm3_version": None}

    export = parse_export(raw.decode("utf-8-sig", "replace"))
    header = export.header
    counts: dict[str, int] = {}
    for entry in export.entries:
        key = f"0x{(entry.address >> 16) & 0xFF:02X}"
        counts[key] = counts.get(key, 0) + 1

    return {
        "export_version": header.version if header else None,
        "idm3_version": header.idm3 if header else None,
        "address_types": dict(sorted(counts.items())),
        "entries": len(export.entries),
        "readable": sum(1 for e in export.entries if is_readable(e)),
        "classified": sum(1 for e in export.entries if platform_of(e) is not None),
    }


def compare(before: dict, after: dict) -> int:
    """Report what moved.  Returns the number of differences."""
    keys = sorted(set(before) | set(after))
    differences = 0

    for key in keys:
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        differences += 1
        print(f"\nCHANGED  {key}")
        print(f"   was : {old}")
        print(f"   now : {new}")
        if key in MEANING:
            print(f"   what it means: {MEANING[key]}")

    if not differences:
        print("\nNothing this integration relies on has changed.")
    else:
        print(
            f"\n{differences} difference(s). Anything under 'protocol_version', "
            "'checksum_ok', 'header_len' or 'read_shape' is serious -- please "
            "open an issue with this output. The rest may be harmless."
        )
    return differences


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--password", default="")
    parser.add_argument("--save", metavar="FILE", help="write the fingerprint here")
    parser.add_argument(
        "--compare", metavar="FILE", help="compare against a saved fingerprint"
    )
    args = parser.parse_args(argv)

    try:
        taken = asyncio.run(snapshot(args.host, args.port, args.password))
    except Is3Error as err:
        print(f"Could not read the unit: {err}")
        return 1

    print(json.dumps(taken, indent=2, sort_keys=True))

    if args.save:
        Path(args.save).write_text(json.dumps(taken, indent=2, sort_keys=True))
        print(f"\nsaved to {args.save}")

    if args.compare:
        previous = json.loads(Path(args.compare).read_text())
        print(f"\n--- against {args.compare} ---")
        return 1 if compare(previous, taken) else 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
