#!/usr/bin/env python3
"""Probe an iNELS central unit to settle what its ASCII port supports.

Two independent real installations disagree about the wire format:

* the scripts from this installation write ``SET 0x0102000A 1 \\r\\n``
  (space separated, trailing space)
* ``abetka/InelsHA`` writes ``SET;0x0102000A;1\\r\\n`` and reads with
  ``GET;<address>\\r\\n`` (semicolon separated)

Rather than guess, this script tries both and reports what the unit actually
does. It is read-only by default: it never sends SET unless you pass --write.

Usage::

    python probe_is3.py 192.168.1.10 1111 0x0102000A
    python probe_is3.py 192.168.1.10 1111 0x0102000A --write

Run it from a machine on the same network as the unit and paste the output back.
"""

from __future__ import annotations

import argparse
import socket
import sys

TIMEOUT = 5.0
READ_SIZE = 4096


def _banner(text: str) -> None:
    """Print a section heading."""
    print(f"\n{'=' * 60}\n{text}\n{'=' * 60}")


def _exchange(host: str, port: int, payload: bytes, wait: float = TIMEOUT) -> bytes:
    """Send one payload on a fresh connection and return whatever comes back."""
    with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
        sock.settimeout(wait)
        sock.sendall(payload)
        try:
            return sock.recv(READ_SIZE)
        except socket.timeout:
            return b""


def probe_reads(host: str, port: int, address: str) -> None:
    """Try every plausible spelling of a read command."""
    _banner("1. READ -- which GET syntax does the unit answer?")

    candidates = {
        "GET;<addr>        (abetka/InelsHA)": f"GET;{address}\r\n",
        "GET <addr>        (space, like the local SET scripts)": f"GET {address} \r\n",
        "GET <addr>        (space, no trailing space)": f"GET {address}\r\n",
        "GET;<addr>;       (trailing delimiter)": f"GET;{address};\r\n",
    }

    for label, command in candidates.items():
        payload = command.encode("ascii")
        try:
            reply = _exchange(host, port, payload)
        except OSError as err:
            print(f"  [error]   {label}\n            {err}")
            continue

        if reply:
            print(f"  [ANSWER]  {label}")
            print(f"            sent:  {payload!r}")
            print(f"            reply: {reply!r}")
        else:
            print(f"  [silence] {label}")


def probe_events(host: str, port: int, seconds: float) -> None:
    """Hold the socket open and see whether the unit pushes state changes."""
    _banner(f"2. PUSH -- does the unit send EVENT lines? (listening {seconds:.0f}s)")
    print("  Toggle a light on the wall now, so there is something to report.\n")

    try:
        with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
            sock.settimeout(seconds)
            try:
                data = sock.recv(READ_SIZE)
            except socket.timeout:
                print("  [silence] nothing arrived -- polling with GET is required")
                return
    except OSError as err:
        print(f"  [error]   {err}")
        return

    print(f"  [PUSH]    unit sends unsolicited data: {data!r}")


def probe_write(host: str, port: int, address: str) -> None:
    """Send the SET command that is already known to work on this unit."""
    _banner("3. WRITE -- the form the local scripts use")

    command = f"SET {address} 1 \r\n".encode("ascii")
    print(f"  sending: {command!r}")
    try:
        reply = _exchange(host, port, command, wait=2.0)
    except OSError as err:
        print(f"  [error]   {err}")
        return

    print(f"  reply:   {reply!r}" if reply else "  reply:   (none, as expected)")
    print("  --> check whether the output actually switched")


def probe_http_export(host: str) -> None:
    """Check the documented export URL, which would remove the manual copy step."""
    _banner("4. EXPORT over HTTP -- can the file be fetched instead of copied?")

    import urllib.error
    import urllib.request

    paths = [
        "/immfiles/export.imm",
        "/immfiles/export.is3",
        "/export.imm",
        "/export.is3",
    ]
    for http_port in (80, 8080):
        for path in paths:
            url = f"http://{host}:{http_port}{path}"
            try:
                with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
                    body = response.read(200)
                print(f"  [FOUND]   {url}")
                print(f"            first bytes: {body[:120]!r}")
            except urllib.error.HTTPError as err:
                print(f"  [{err.code}]     {url}")
            except OSError:
                print(f"  [no]      {url}")


def main() -> int:
    """Run the probes selected on the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="IP address of the central unit")
    parser.add_argument("port", type=int, help="ASCII port, e.g. 1111 or 22272")
    parser.add_argument("address", help="an address to read, e.g. 0x0102000A")
    parser.add_argument(
        "--write",
        action="store_true",
        help="also send SET <address> 1, which will switch that output on",
    )
    parser.add_argument(
        "--listen",
        type=float,
        default=15.0,
        help="seconds to wait for pushed events (default: 15)",
    )
    args = parser.parse_args()

    print(f"Probing {args.host}:{args.port}, address {args.address}")

    probe_reads(args.host, args.port, args.address)
    probe_events(args.host, args.port, args.listen)
    if args.write:
        probe_write(args.host, args.port, args.address)
    probe_http_export(args.host)

    _banner("Done -- paste this whole output back")
    return 0


if __name__ == "__main__":
    sys.exit(main())
