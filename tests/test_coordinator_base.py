"""The coordinator must not shadow anything its Home Assistant base defines.

This is a scar test.  In 0.1.6 a helper was named ``_async_refresh``, which is
the base DataUpdateCoordinator's own method; Home Assistant calls it during
setup with ``log_failures=...``, so every config entry failed with a TypeError
that no local test could see -- the suite builds coordinators with
``__new__`` and never goes through the base class.  Comparing against the base
by identity is the one check that would have caught it.
"""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.is3_export.coordinator import Is3Coordinator

# Overrides the integration makes on purpose: the constructor, the update hook
# the base exists to call, and the listener wake it deliberately narrows.
# Anything else appearing on both classes is an accident.
_DELIBERATE_OVERRIDES = {"__init__", "async_update_listeners", "_async_update_data"}


def test_no_method_accidentally_shadows_the_base() -> None:
    """Anything defined on both classes is either the base's or a known override."""
    shadowed = [
        name
        for name, attribute in vars(Is3Coordinator).items()
        if callable(attribute)
        and hasattr(DataUpdateCoordinator, name)
        and name not in _DELIBERATE_OVERRIDES
    ]
    assert not shadowed, f"these shadow DataUpdateCoordinator: {shadowed}"


def test_the_base_refresh_is_untouched() -> None:
    """The specific collision that broke 0.1.6, named so the scar is readable."""
    assert Is3Coordinator._async_refresh is DataUpdateCoordinator._async_refresh
