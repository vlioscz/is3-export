"""Event platform for the IS3 Export integration.

A wall switch button reports a short ``press`` and a ``long_press``; an RF
remote's button reports only ``press``.

Telling short from long needs the length of the hold -- the time between the
unit's press (``=1``) and release (``=0``) events.  On a **wired** switch, with
no Connection Server polling the unit (its periodic read freezes the ASCII
output for seconds and smears the timing), that gap is clean and consistent:
measured taps land under ~100 ms and deliberate holds over ~1.5 s, with a wide
empty band between.  So a wired button starts a timer on the press; if the
release comes first it is a short ``press``, and if the timer elapses while the
button is still held it is a ``long_press``.  A lost release cannot wedge the
button -- a safety timeout clears the held state.

An **RF** remote drops or delays its release, so a hold length is not reliable
there; those buttons keep the single-press behaviour.  It fires on every "on"
event the unit pushes -- the coordinator delivers those un-deduped for buttons,
so a lost release leaving the value on cannot make the next press look like no
change -- and a short debounce swallows only the unit's immediate re-send.
"""

from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .coordinator import Is3ConfigEntry, Is3Coordinator
from .entity import Is3Entity
from .export import Is3Entry, is_press_button, is_rf_button

PRESS = "press"
LONG_PRESS = "long_press"

# How long a wired button must be held before the press counts as long.  Well
# above real taps (~0.1 s) and well below deliberate holds (~1.5-2 s), so the two
# never cross.  A long press fires the moment this elapses, while still held.
LONG_PRESS_SECONDS = 0.6

# A held button whose release was lost would otherwise stay "pressed" for good;
# after this long with no release, give up waiting so the next press registers.
# Longer than any real hold, so it never cuts a genuine one short.
MAX_HOLD_SECONDS = 6.0

# RF only: a press fires on every "on" event (see module docstring); this short
# window swallows the unit's immediate re-send so one physical press is one event.
DEBOUNCE_SECONDS = 0.5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Is3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a button event for every wall switch and RF remote button."""
    coordinator = entry.runtime_data
    async_add_entities(
        Is3ButtonEvent(coordinator, item)
        for item in coordinator.data.export.entries
        if is_press_button(item)
    )


class Is3ButtonEvent(Is3Entity, EventEntity):
    """A wall switch (short/long) or RF remote (single press) button."""

    _attr_device_class = EventDeviceClass.BUTTON

    def __init__(self, coordinator: Is3Coordinator, entry: Is3Entry) -> None:
        """Bind to the button's input on its own switch."""
        super().__init__(coordinator, entry)
        self._address = entry.address
        self._attr_unique_id = f"{self._attr_unique_id}_event"
        self._attr_name = entry.name.replace("_", " ")
        self._long_capable = not is_rf_button(entry)
        self._attr_event_types = (
            [PRESS, LONG_PRESS] if self._long_capable else [PRESS]
        )
        # RF single-press state.
        self._active = False
        self._debounce: object | None = None
        # Wired short/long state.
        self._pressed = False
        self._long_fired = False
        self._long_timer: object | None = None
        self._max_timer: object | None = None

    async def async_added_to_hass(self) -> None:
        """Watch the input and classify each interaction."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_address_listener(
                self._address, self._handle_change
            )
        )
        self.async_on_remove(self._cancel_all)

    @callback
    def _handle_change(self) -> None:
        """Route each event by whether the button is held on (=1) or off (=0)."""
        on = bool(self.coordinator.values.get(self._address))
        if self._long_capable:
            self._handle_wired(on)
        else:
            self._handle_rf(on)

    # --- Wired switch: short press vs long press by hold length ---------------

    @callback
    def _handle_wired(self, on: bool) -> None:
        """Start the hold on press; classify on release or when the timer fires."""
        if on:
            if self._pressed:
                return  # a re-broadcast of the same hold: ignore
            self._pressed = True
            self._long_fired = False
            self._long_timer = async_call_later(
                self.hass, LONG_PRESS_SECONDS, self._on_long
            )
            self._max_timer = async_call_later(
                self.hass, MAX_HOLD_SECONDS, self._on_max
            )
        else:
            if not self._pressed:
                return  # a repeated release: already handled
            self._pressed = False
            self._cancel_hold_timers()
            if not self._long_fired:
                self._fire(PRESS)  # released before the threshold: a short tap

    @callback
    def _on_long(self, _now) -> None:
        """The hold reached the threshold while still down: a long press."""
        self._long_timer = None
        if self._pressed and not self._long_fired:
            self._long_fired = True
            self._fire(LONG_PRESS)

    @callback
    def _on_max(self, _now) -> None:
        """Release was never seen; stop waiting so the next press is not lost."""
        self._max_timer = None
        self._pressed = False
        self._cancel_hold_timers()

    @callback
    def _cancel_hold_timers(self) -> None:
        """Cancel the long and safety timers, if pending."""
        for name in ("_long_timer", "_max_timer"):
            timer = getattr(self, name)
            if timer is not None:
                timer()
                setattr(self, name, None)

    # --- RF remote: one press per "on" event ---------------------------------

    @callback
    def _handle_rf(self, on: bool) -> None:
        """Fire on every "on" event; swallow the immediate re-send."""
        if not on or self._active:
            return
        self._active = True
        self._fire(PRESS)
        self._debounce = async_call_later(self.hass, DEBOUNCE_SECONDS, self._end)

    @callback
    def _end(self, _now) -> None:
        """Close the debounce window; the next on event is a new press."""
        self._debounce = None
        self._active = False

    # --- Shared ---------------------------------------------------------------

    @callback
    def _fire(self, event_type: str) -> None:
        """Emit one button event."""
        self._trigger_event(event_type)
        self.async_write_ha_state()

    @callback
    def _cancel_all(self) -> None:
        """Cancel every pending timer on removal."""
        self._cancel_hold_timers()
        if self._debounce is not None:
            self._debounce()
            self._debounce = None
