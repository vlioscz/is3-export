#!/usr/bin/env python3
"""Probe an iNELS central unit on UDP 9999, to settle what it actually does.

Run this from a machine on the same network as the unit and paste the output
into a bug report.  It answers the questions that otherwise take a round of
guessing: does the unit answer at all, does it want a password, does its data
plane open, and does it push events.

Read-only by default -- it never writes unless you pass ``--write``, and then
only to the one address you name.

Usage::

    python probe_is3.py 192.168.1.10
    python probe_is3.py 192.168.1.10 --password secret
    python probe_is3.py 192.168.1.10 --read 0x01050001
    python probe_is3.py 192.168.1.10 --write 0x0102000A 1

It prints addresses and values, never device names, so the output is safe to
share.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import socket
import struct
import sys
import time

PROTOCOL_VERSION = bytes.fromhex("81631F55DB182AAB")
HEADER_LEN = 82
NO_VALUE = 0x7FFFFFF0

# The checksum model, same as the integration's (see checksum.py).
_T = (418, 580, 1160, 2064, 4145, 8290, 16580, 32904,
      17, 51, 102, 204, 152, 48, 96, 209)
_K = {82: 56415, 90: 17892, 245: 8734}
_REV8 = [int(f"{i:08b}"[::-1], 2) for i in range(256)]


def _apply_t(x: int) -> int:
    out = 0
    for r in range(16):
        if bin(_T[r] & x).count("1") & 1:
            out |= 1 << r
    return out


def _k_of_len(length: int) -> int:
    known = max(k for k in _K if k <= length) if any(k <= length for k in _K) else min(_K)
    reg = _K[known]
    for _ in range(length - known):
        reg = _apply_t(reg)
    return reg


def crc(body: bytes) -> int:
    reg = 0
    for b in body:
        reg = _apply_t(reg) ^ ((_REV8[b] << 8) & 0xFFFF)
    return _k_of_len(len(body)) ^ reg


def build(typ, i1, i2, data=b"", token=b"\x00" * 8, pid=0) -> bytes:
    body = (
        PROTOCOL_VERSION + b"\x00" * 56 + token
        + struct.pack(">H", HEADER_LEN + len(data) + 2)
        + struct.pack(">I", pid)
        + bytes((0x01, typ, i1, i2)) + data
    )
    return body + struct.pack("<H", crc(body))


class Probe:
    """One UDP conversation with the unit."""

    def __init__(
        self, host: str, port: int, password: str, timeout: float, retries: int = 4
    ) -> None:
        self.host, self.port, self.password, self.timeout = host, port, password, timeout
        self.retries = retries
        self.attempts = 0
        self.token = b"\x00" * 8
        self.pid = 0
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        self.sock = socket.socket(family, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)

    def ask(self, typ, i1, i2, data=b"", auth=False, retries=None):
        """Send one request and wait for its reply, resending if none comes.

        Resent as many times as the integration does, and for the same reason:
        this is UDP, a datagram can simply be dropped, and a unit that answers
        the second time is a different animal from one that never answers at
        all.  Asking once and reporting silence made those two look identical,
        which cost an evening.  ``self.attempts`` records how many it took.
        """
        for attempt in range(1, (retries or self.retries) + 1):
            self.attempts = attempt
            self.pid += 2
            token = self.token if auth else b"\x00" * 8
            self.sock.sendto(
                build(typ, i1, i2, data, token, self.pid), (self.host, self.port)
            )
            deadline = time.time() + self.timeout
            while time.time() < deadline:
                try:
                    raw, _ = self.sock.recvfrom(4096)
                except socket.timeout:
                    break
                if len(raw) < HEADER_LEN + 2 or raw[:8] != PROTOCOL_VERSION:
                    continue
                if (raw[79], raw[80], raw[81]) != (typ, i1, i2):
                    continue  # an event or a straggler; keep waiting
                return raw[78], raw[82:-2]
        return None

    def close(self) -> None:
        self.sock.close()


def scan(new_probe, password: str) -> None:
    """Ask every request this protocol uses, each on a socket of its own.

    Run when the handshake will not complete, and the point is the *pattern*:
    a unit answering some requests and not others is refusing to hold the
    conversation, which is a different fault from being unreachable or from
    rejecting a password -- the two things the integration can otherwise
    report.  Each gets its own socket so nothing carries over from the last.

    Two attempts rather than four: with several requests to get through and a
    silent one costing the timeout every time, this is a survey, not a verdict.
    """
    body = b"\x01" + hashlib.sha1(password.encode()).digest()
    requests = (
        ("run state", 0x40, 0x03, 0x00, b""),
        ("protocol info", 0x40, 0x05, 0x00, b""),
        ("unit info", 0x70, 0x03, 0x02, bytes.fromhex("000000000000000002")),
        ("is a password set", 0x40, 0x06, 0x00, b""),
        ("authorize", 0x40, 0x06, 0x01, body),
        ("project digest", 0x01, 0x03, 0x00, b""),
        # Deliberately unauthorized: on a working unit this is ignored, and
        # what matters is whether it is ignored the same way as the rest.
        ("read one address, unauthorized", 0x01, 0x01, 0x00,
         b"\x01" + struct.pack(">I", 0x01020001)),
    )

    for label, typ, i1, i2, data in requests:
        attempt = new_probe()
        reply = attempt.ask(typ, i1, i2, data, retries=2)
        if reply is None:
            answer = f"silent ({attempt.attempts} tries)"
        elif reply[0] & 0x80:
            answer = "refused (NACK)"
        else:
            answer = f"answered, {len(reply[1])} bytes"
        print(f"     {typ:#04x}/{i1:#04x}/{i2:#04x}  {label:<32} {answer}")
        attempt.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--password", default="")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument(
        "--retries", type=int, default=4,
        help="resends per request, matching what the integration does",
    )
    parser.add_argument("--read", metavar="ADDRESS", help="also read this address")
    parser.add_argument("--listen", type=float, default=5.0, metavar="SECONDS")
    parser.add_argument(
        "--write", nargs=2, metavar=("ADDRESS", "VALUE"),
        help="write a value, then read it back (the only thing that changes anything)",
    )
    args = parser.parse_args(argv)

    def new_probe():
        return Probe(
            args.host, args.port, args.password, args.timeout, args.retries
        )

    probe = new_probe()
    print(f"probing {args.host}:{args.port} (UDP), up to {args.retries} tries per request")

    reply = probe.ask(0x40, 0x03, 0x00)
    if reply is None:
        print("  no answer at all -- wrong address, a firewall, or the unit is down")
        probe.close()
        return 1
    _, data = reply
    state = {0x20: "FastRun", 0x10: "Run", 0x00: "Stop"}.get(data[0], hex(data[0]))
    print(f"  unit answers.  run state: {state}")

    probe.close()

    # The handshake, run twice with one difference: whether anything is asked
    # in between its steps.  On one installation authorizing began to fail the
    # moment a harmless extra question was put in front of it, and it works
    # again with that question removed -- which would mean the unit tracks
    # where it is in the handshake and anything unexpected loses it.  Each
    # attempt gets a socket of its own, so neither can be blamed on the other.
    probe = None
    for label, interrupt in (("uninterrupted", False), ("with a question first", True)):
        attempt = new_probe()
        attempt.ask(0x40, 0x03, 0x00)
        if interrupt:
            asked = attempt.ask(0x40, 0x06, 0x00)
            answer = (
                "no answer" if asked is None
                else f"password set: {'yes' if asked[1] and asked[1][0] else 'no'}"
            )
            print(f"  asking whether a password is set -> {answer}")
        attempt.ask(0x70, 0x03, 0x02, bytes.fromhex("000000000000000002"))
        attempt.ask(0x40, 0x05, 0x00)
        body = b"\x01" + hashlib.sha1(args.password.encode()).digest()
        reply = attempt.ask(0x40, 0x06, 0x01, body)
        tries = f" (after {attempt.attempts} tries)" if attempt.attempts > 1 else ""

        if reply is None:
            print(f"  handshake {label}: authorization NOT ANSWERED after {attempt.attempts} tries")
        elif reply[0] & 0x80:
            print(f"  handshake {label}: authorization REFUSED (NACK){tries}")
        elif len(reply[1]) < 8:
            print(f"  handshake {label}: authorization answered without a token")
        else:
            print(f"  handshake {label}: AUTHORIZED{tries}")
            if probe is None:
                attempt.token = reply[1][:8]
                probe = attempt
                continue
        attempt.close()

    if probe is None:
        print("  no handshake got through; asking each request on its own:")
        scan(new_probe, args.password)
        print("\n  A unit that answers some requests and not others is not")
        print("  unreachable and has not lost its password -- it is refusing to")
        print("  hold the conversation. What it will and will not answer is the")
        print("  thing to compare against a unit that works.")
        return 1

    # The data plane is the real test: a unit issues a token and then ignores
    # requests for values when the password was not the one it wanted.
    address = int(args.read, 16) if args.read else 0x01020001
    reply = probe.ask(0x01, 0x01, 0x00, b"\x01" + struct.pack(">I", address), auth=True)
    if reply is None:
        print("  data plane SILENT -- authorized, but the unit answers no reads.")
        print("  That is what a wrong password looks like on this protocol.")
        probe.close()
        return 1
    _, data = reply
    if len(data) < 5:
        # A reply with nothing in it is not an open data plane.  The unit hands
        # out a session token whatever password it was given and only then
        # decides whether to answer, so this is what a password it did not want
        # looks like from here -- and printing "OPEN" for it said the opposite
        # of the truth on a unit that does have one set.
        print(f"  data plane answered with no value in it ({len(data)} bytes).")
        print("  The unit issues a token for any password and only afterwards")
        print("  decides whether to answer, so this is what the wrong one looks")
        print("  like. Pass --password to try the one set on the unit.")
        probe.close()
        return 1
    raw = struct.unpack(">i", data[1:5])[0]
    shown = "no value" if (raw & 0xFFFFFFFF) >= NO_VALUE else raw
    print(f"  read {address:#010x} -> {shown}")
    print("  data plane OPEN")

    # Whether the unit turns its push stream on, and if not, which way it
    # declined.  Newer firmware does not, and saying nothing is a different
    # fault from saying no: silence suggests the request is not understood at
    # all, a refusal means it was and the answer was still no.  This is the one
    # thing that cannot be worked out from Home Assistant's side, and the whole
    # difference between values arriving as they happen and waiting for a sweep.
    reply = probe.ask(0x04, 0x02, 0x00, auth=True)
    if reply is None:
        print("  event stream: NO ANSWER (asked and never replied)")
    elif reply[0] & 0x80:
        print(f"  event stream: REFUSED (address byte {reply[0]:#04x}, "
              f"body {reply[1][:16].hex() or 'empty'})")
    else:
        print("  event stream: started")

    print(f"  listening {args.listen:.0f}s for pushed events...")
    seen: dict[int, int] = {}
    deadline = time.time() + args.listen
    probe.sock.settimeout(0.5)
    while time.time() < deadline:
        try:
            raw, _ = probe.sock.recvfrom(4096)
        except socket.timeout:
            continue
        if len(raw) < HEADER_LEN + 2 or raw[79] != 0x04 or raw[80] != 0x01:
            continue
        payload = raw[82:-2]
        for i in range(payload[0] if payload else 0):
            off = 1 + 8 * i
            if off + 8 > len(payload):
                break
            addr = struct.unpack(">I", payload[off : off + 4])[0]
            seen[addr] = seen.get(addr, 0) + 1
    print(f"  {sum(seen.values())} events from {len(seen)} addresses")
    for addr, count in sorted(seen.items(), key=lambda kv: -kv[1])[:5]:
        print(f"     {addr:#010x} x{count}")
    if not seen:
        print("     (none -- normal on a quiet installation, but if this stays")
        print("      empty while something is changing, events are not arriving)")

    if args.write:
        probe.sock.settimeout(args.timeout)
        target, value = int(args.write[0], 16), int(args.write[1])
        print(f"  writing {value} to {target:#010x}")
        reply = probe.ask(
            0x02, 0x01, 0x00,
            b"\x01" + struct.pack(">I", target) + struct.pack(">i", value),
            auth=True,
        )
        ok = reply is not None and not (reply[0] & 0x80)
        print(f"     {'acknowledged' if ok else 'REFUSED'}")
        time.sleep(1.5)
        reply = probe.ask(0x01, 0x01, 0x00, b"\x01" + struct.pack(">I", target), auth=True)
        if reply is not None and len(reply[1]) >= 5:
            back = struct.unpack(">i", reply[1][1:5])[0]
            print(f"     reads back as {back} -> {'took' if back == value else 'DID NOT take'}")

    probe.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
