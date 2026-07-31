#!/usr/bin/env python3
"""Fail if a file contains a token that leaked once and must never return.

The tests use anonymised fixtures.  A real address, a real hardware serial and
an owner name slipped into a fixture once (removed since) -- this guard stops
them coming back, e.g. from an out-of-date clone that still holds the old
history and gets pushed.

Only the **SHA-256 of each forbidden token** is stored here, so the tokens
themselves are never committed; a match is reported by file and by a hash
prefix, never by value, so CI logs cannot echo the sensitive text either.

Usage::

    python tools/check_pii.py            # the whole tracked tree
    python tools/check_pii.py FILE ...   # only these files
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

# SHA-256 of each lowercased forbidden token -- a residential address, real
# module serials, owner/place names and the real unit's IP.  Deliberately
# opaque: the plaintext is intentionally not in this file.
FORBIDDEN_HASHES = frozenset(
    {
        "deb791a3105fde5e9a81b75892f218ea6631473a30d362f146846d94656a9379",
        "43ff2e29e9dfd7fce36382e7307f258699a5bbea5aa062824a8a46743b373702",
        "37c4a68969ebd77c691a9467363d109e7ecbbcfa7cbe8c600892c20241ab1c73",
        "428741ff862440b5f2ac0f82393466d8284df9679db7cb002d8c4850fb0a25b5",
        "096d083fdc1c2da65a4b90470630c6b8bb17e1a48518ccae0e9314cd08bb6ffc",
        "2e51c0382b066a2b36e51599a9e3e4be691968dcc4487ff43bf678bc21aa6621",
        "79ee6347ff0e71845355429ff13862d6939ac7f8385ec4e7712211c767ced1d2",
        "98458761932ba56f9f7e61728244e9dd2b7e24057c5eae4355ea357cf123da8d",
        "be1d1922f0856588dc3f29bc6cba27fc05568efb95cb064048c7e3511021ac92",
        "5999072cd7f0482abfd61ad9c35780b487296a101decacf6e4950cfb74cc2690",
        "d18e28044b4871ae3df241a9baeb64191283c07fbd876e4db3e0b94bc9721639",
        "0e80afcb67cbe99ab947ed04078027be02ebf720cf87e8a4fd35607f74906626",
    }
)

# Non-text files would tokenise into noise, so they are skipped.
_BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".gz", ".pdf"}

# A whole IPv4 address, or any run of letters/digits (hardware ids split on the
# `-`/`_`/`.` between their parts, so a serial like 0A0001 is one token).
_TOKEN = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}|[0-9A-Za-z]+")


def forbidden_in(text: str, forbidden: frozenset[str] = FORBIDDEN_HASHES) -> set[str]:
    """Return the forbidden hashes whose token appears in ``text``."""
    hits: set[str] = set()
    for token in _TOKEN.findall(text):
        digest = hashlib.sha256(token.lower().encode()).hexdigest()
        if digest in forbidden:
            hits.add(digest)
    return hits


def _tracked_files() -> list[Path]:
    """Every file git tracks, so the guard matches what would be pushed."""
    output = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return [Path(line) for line in output.splitlines() if line]


def main(argv: list[str]) -> int:
    """Scan the given files (or the whole tracked tree) and report offenders."""
    files = [Path(a) for a in argv] if argv else _tracked_files()
    offenders: list[str] = []

    for path in files:
        if path.suffix.lower() in _BINARY_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if hits := forbidden_in(text):
            # File and hash prefix only -- never the token itself.
            offenders.append(f"{path}: {', '.join(sorted(h[:12] for h in hits))}")

    if offenders:
        print("PII guard: forbidden token(s) present -- remove them, do not commit:")
        for line in offenders:
            print(f"  {line}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
