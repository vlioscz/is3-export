"""The PII guard catches a leaked token by its hash, and stays quiet otherwise.

No real forbidden token appears here -- the mechanics are exercised with made-up
ones, so this test file is itself clean under the shipped denylist.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "check_pii", Path(__file__).resolve().parent.parent / "tools" / "check_pii.py"
)
assert _spec and _spec.loader
check_pii = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_pii)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def test_detects_a_forbidden_token_by_hash() -> None:
    forbidden = frozenset({_hash("swordfish")})
    assert check_pii.forbidden_in("open swordfish now", forbidden)
    assert check_pii.forbidden_in("nothing to see here", forbidden) == set()


def test_matches_a_whole_serial_case_insensitively() -> None:
    # a serial sits inside a hardware id, split on the non-alphanumeric parts
    forbidden = frozenset({_hash("ab12cd")})
    assert check_pii.forbidden_in("SA3-012M_RE1_AB12CD 0x01020006", forbidden)
    assert check_pii.forbidden_in("SA3-012M_RE1_0C0001 0x01020006", forbidden) == set()


def test_detects_a_whole_ip_address() -> None:
    forbidden = frozenset({_hash("203.0.113.5")})  # a documentation IP, not real
    assert check_pii.forbidden_in("host = 203.0.113.5", forbidden)
    assert check_pii.forbidden_in("host = 192.168.1.10", forbidden) == set()


def test_the_tracked_tree_is_clean_under_the_shipped_denylist() -> None:
    """The real denylist must pass on the repo, or it would block every commit."""
    assert check_pii.main([]) == 0
