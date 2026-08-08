"""Repair cards: raised per unit, and the retired one taken down on upgrade.

A refused password is not here -- Home Assistant's own re-authentication dialog
owns that.  What is left is the failure with no credential to collect: the unit
will not serve its export, and the answer is to upload one.
"""

from __future__ import annotations

import custom_components.is3_export.issues as issues


class _Registry:
    """Records the issue-registry calls the helper makes."""

    def __init__(self) -> None:
        self.created: list[tuple[str, dict]] = []
        self.deleted: list[str] = []

    def async_create_issue(self, hass, domain, issue_id, **kwargs) -> None:
        self.created.append((issue_id, kwargs))

    def async_delete_issue(self, hass, domain, issue_id) -> None:
        self.deleted.append(issue_id)


def _patch(monkeypatch) -> _Registry:
    registry = _Registry()
    monkeypatch.setattr(issues.ir, "async_create_issue", registry.async_create_issue)
    monkeypatch.setattr(issues.ir, "async_delete_issue", registry.async_delete_issue)
    return registry


def test_raises_a_card_when_the_export_is_blocked(monkeypatch) -> None:
    registry = _patch(monkeypatch)

    issues.async_update_export_issue(None, "abc", blocked=True)

    assert registry.deleted == []
    assert len(registry.created) == 1
    issue_id, kwargs = registry.created[0]
    assert issue_id == "export_protected_abc"
    assert kwargs["is_fixable"] is False
    assert kwargs["translation_key"] == "export_protected"


def test_clears_the_card_once_the_export_arrives(monkeypatch) -> None:
    registry = _patch(monkeypatch)

    issues.async_update_export_issue(None, "abc", blocked=False)

    assert registry.created == []
    assert registry.deleted == ["export_protected_abc"]


def test_each_unit_gets_its_own_card(monkeypatch) -> None:
    registry = _patch(monkeypatch)

    issues.async_clear_issues(None, "unit-one")
    issues.async_clear_issues(None, "unit-two")

    assert "export_protected_unit-one" in registry.deleted
    assert "export_protected_unit-two" in registry.deleted


def test_the_retired_card_is_taken_down(monkeypatch) -> None:
    """0.1.x raised a card about settings that no longer exist.

    The issue registry keeps an issue after the integration stops raising it,
    so upgrading would otherwise leave every existing user with a permanent
    card telling them to fix a delimiter the integration no longer has.
    """
    registry = _patch(monkeypatch)

    issues.async_clear_issues(None, "abc")

    assert "reads_unsupported_abc" in registry.deleted
