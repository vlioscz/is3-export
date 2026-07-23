"""Repair issues surfaced to the user in Home Assistant's UI.

Most misconfigurations are only a line in the log, where nobody sees them. The
one that actually strands the integration is a delimiter or number base that
does not match the unit: reads then go unanswered and every entity shows an
assumed state instead of the live value. That is turned into a repair card that
points at the fix, rather than a warning buried in the log.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DELIMITERS, DOMAIN, NUMBER_BASES

READS_UNSUPPORTED = "reads_unsupported"

# The README section that explains the delimiter and number base.
_LEARN_MORE_URL = "https://github.com/vlioscz/is3-export#configuration"


def _issue_id(entry_id: str) -> str:
    """A per-entry id, so two units never share one repair card."""
    return f"{READS_UNSUPPORTED}_{entry_id}"


def async_update_reads_issue(
    hass: HomeAssistant,
    entry_id: str,
    *,
    reads_supported: bool,
    delimiter: str,
    number_base: str,
) -> None:
    """Raise or clear the "reads not answered" repair for one unit."""
    issue_id = _issue_id(entry_id)

    if reads_supported:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=READS_UNSUPPORTED,
        translation_placeholders={
            "delimiter": DELIMITERS.get(delimiter, delimiter),
            "number_base": NUMBER_BASES.get(number_base, number_base),
        },
        learn_more_url=_LEARN_MORE_URL,
    )


def async_clear_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Remove this unit's repair cards, e.g. when the entry is removed."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry_id))
