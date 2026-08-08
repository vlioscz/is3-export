"""Every module in the integration must import.

This suite runs without a Home Assistant harness, so nothing else here ever
imports the entity platforms -- a stale ``from .api import ...`` in switch.py
would sail through every other test and then fail at startup on a real install,
which is the worst place to find out.  Importing each module is cheap and
closes that gap.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import custom_components.is3_export as package

MODULES = sorted(
    module.name for module in pkgutil.iter_modules(package.__path__)
)


def test_the_package_has_modules_to_check() -> None:
    """Guard against the list silently coming back empty."""
    assert "coordinator" in MODULES
    assert "client" in MODULES


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    importlib.import_module(f"{package.__name__}.{name}")
