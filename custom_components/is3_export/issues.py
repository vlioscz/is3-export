"""Repair issues surfaced to the user in Home Assistant's UI.

Only one thing belongs here now.  A refused password is not a repair -- Home
Assistant has a dialog for that, and it asks for the credential directly.  What
is left is the case with no credential to collect: the unit's web server will
not hand over the export, and the way out is to point at a local copy or drop
one into the form, which no password would fix.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

EXPORT_PROTECTED = "export_protected"

# Retired in 0.2.0 along with the settings it described.  The issue registry
# does not clear an issue just because the integration stopped raising it, so
# the id is kept here to take the old card down on upgrade.
_RETIRED = ("reads_unsupported",)

_LEARN_MORE_URL = "https://github.com/vlioscz/is3-export#configuration"


def _issue_id(kind: str, entry_id: str) -> str:
    """A per-entry id, so two units never share one repair card."""
    return f"{kind}_{entry_id}"


def async_update_export_issue(
    hass: HomeAssistant, entry_id: str, *, blocked: bool
) -> None:
    """Raise or clear the "cannot download the export" repair for one unit."""
    issue_id = _issue_id(EXPORT_PROTECTED, entry_id)

    if not blocked:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=EXPORT_PROTECTED,
        learn_more_url=_LEARN_MORE_URL,
    )


def async_clear_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Remove this unit's repair cards, including ones no longer raised."""
    for kind in (EXPORT_PROTECTED, *_RETIRED):
        ir.async_delete_issue(hass, DOMAIN, _issue_id(kind, entry_id))
