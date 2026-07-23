"""A mismatched delimiter/number base becomes a repair card, not a log line.

When the unit does not answer reads the integration falls back to assumed
states, which is easy to miss.  The coordinator's ``reads_supported`` drives a
repair issue: raised while reads fail, cleared once they work.
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


def test_raises_a_card_when_reads_are_unsupported(monkeypatch) -> None:
    registry = _patch(monkeypatch)

    issues.async_update_reads_issue(
        None, "abc", reads_supported=False, delimiter=" ", number_base="hex"
    )

    assert registry.deleted == []
    assert len(registry.created) == 1
    issue_id, kwargs = registry.created[0]
    assert issue_id == "reads_unsupported_abc"
    assert kwargs["is_fixable"] is False
    assert kwargs["translation_key"] == "reads_unsupported"
    # the delimiter and base are shown as their friendly labels
    assert kwargs["translation_placeholders"]["delimiter"] == "Space [32]"
    assert kwargs["translation_placeholders"]["number_base"] == "Hexadecimal"


def test_clears_the_card_once_reads_work(monkeypatch) -> None:
    registry = _patch(monkeypatch)

    issues.async_update_reads_issue(
        None, "abc", reads_supported=True, delimiter=" ", number_base="hex"
    )

    assert registry.created == []
    assert registry.deleted == ["reads_unsupported_abc"]


def test_each_unit_gets_its_own_card(monkeypatch) -> None:
    registry = _patch(monkeypatch)

    issues.async_clear_issues(None, "unit-one")
    issues.async_clear_issues(None, "unit-two")

    assert registry.deleted == ["reads_unsupported_unit-one", "reads_unsupported_unit-two"]
