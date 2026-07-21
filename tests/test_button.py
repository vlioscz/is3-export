"""Pressing an impulse button pulses the bit: 1, then straight back to 0.

The button needs a coordinator and client to construct, so a minimal stand-in
supplies the value store and captures the writes. The press logic is what is
under test; the pulse gap is zeroed so the test does not wait.
"""

from __future__ import annotations

import asyncio

import custom_components.is3_export.button as button_module
from custom_components.is3_export.button import Is3Button
from custom_components.is3_export.export import Is3Entry

ADDR = 0x0203000B
ADDR_HEX = "0x0203000B"


class _Client:
    """Records the values written, and never touches a socket."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, int]] = []

    async def async_set(self, address: str, value: int) -> None:
        """Capture a write."""
        self.writes.append((address, value))


class _Coordinator:
    """Just the value store and note_write the button uses."""

    def __init__(self) -> None:
        self.client = _Client()
        self.values: dict[int, int] = {}

    def async_note_write(self, address: int, value: int) -> None:
        """Reflect a write the way the real coordinator does."""
        self.values[address] = value


def _button() -> Is3Button:
    """A button bound to one impulse address, base class bypassed."""
    button = Is3Button.__new__(Is3Button)
    button.coordinator = _Coordinator()
    button.entry = Is3Entry(name="imp_wled_pc", address=ADDR, value=0)
    return button


def _press(button: Is3Button, monkeypatch) -> None:
    """Run one press to completion, without the real pulse delay."""
    monkeypatch.setattr(button_module, "PULSE_GAP", 0)
    asyncio.run(button.async_press())


def test_a_press_is_a_pulse(monkeypatch) -> None:
    """One press writes 1 then 0, so the bit ends at rest."""
    button = _button()
    _press(button, monkeypatch)
    assert button.coordinator.client.writes == [(ADDR_HEX, 1), (ADDR_HEX, 0)]


def test_every_press_starts_from_zero(monkeypatch) -> None:
    """Repeated presses each send a fresh 1 then 0, not a drifting toggle."""
    button = _button()
    _press(button, monkeypatch)
    _press(button, monkeypatch)
    _press(button, monkeypatch)

    sent = [value for _, value in button.coordinator.client.writes]
    assert sent == [1, 0, 1, 0, 1, 0]


def test_the_bit_rests_at_zero(monkeypatch) -> None:
    """After a press the tracked value is 0, ready for the next rising edge."""
    button = _button()
    _press(button, monkeypatch)
    assert button.coordinator.values[ADDR] == 0
