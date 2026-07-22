"""Event platform for the IS3 Export integration.

A wall switch button, and an RF remote's, reports one reliable ``press`` event.

It cannot report short vs long. Telling them apart needs the length of the hold,
which is the time between the unit's press and release events -- and the unit
sends those with a variable delay of up to a couple of seconds, sends the
release late or (over RF) not at all, and offers no signal that separates "held
two seconds" from "release delayed two seconds". The measured gap is the true
hold plus a random delay as large as the hold itself, so a tap and a long press
give the same reading. This was checked exhaustively; duration is simply not
carried on the wire.

So a press is reported on every "on" event the unit pushes.  The coordinator
delivers those un-deduped for buttons (it normally wakes an entity only on a
value change), because the release event can be delayed or lost -- leaving the
value on -- and then the next press's on event, being "no change", would be
dropped and the press would go missing.  Firing on the raw on event instead
means every press registers, however the release behaves.

A short debounce swallows only the unit's immediate re-send of the same press,
so one physical press is one event; it is kept short so genuine quick taps each
get through.  The trade-off is that physically holding a button, which the unit
re-broadcasts every second or two, fires a press per re-broadcast -- fine for
the taps these buttons are used for.
"""

from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .coordinator import Is3ConfigEntry, Is3Coordinator
from .entity import Is3Entity
from .export import Is3Entry, is_press_button

PRESS = "press"

# A press fires on every "on" event the unit pushes -- the coordinator delivers
# them un-deduped for buttons -- so a lost release leaving the value on cannot
# make the next press look like no change.  This short window only swallows the
# unit's immediate re-send of the same press (seen ~0.1s later), so one physical
# press is one event; kept short so genuine quick taps each get through.
DEBOUNCE_SECONDS = 0.5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Is3ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a press event for every wall switch and RF remote button."""
    coordinator = entry.runtime_data
    async_add_entities(
        Is3ButtonEvent(coordinator, item)
        for item in coordinator.data.export.entries
        if is_press_button(item)
    )


class Is3ButtonEvent(Is3Entity, EventEntity):
    """A wall switch or RF remote button, reported as a single reliable press."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = [PRESS]

    def __init__(self, coordinator: Is3Coordinator, entry: Is3Entry) -> None:
        """Bind to the button's input on its own switch."""
        super().__init__(coordinator, entry)
        self._address = entry.address
        self._attr_unique_id = f"{self._attr_unique_id}_event"
        self._attr_name = entry.name.replace("_", " ")
        self._active = False
        self._cancel = None

    async def async_added_to_hass(self) -> None:
        """Watch the input to fire one press per interaction."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_address_listener(
                self._address, self._handle_change
            )
        )
        self.async_on_remove(self._stop)

    @callback
    def _handle_change(self) -> None:
        """Fire on every "on" event; swallow the immediate re-send."""
        if not self.coordinator.values.get(self._address):
            # A release (=0): the press is the on event, not the off.
            return
        if self._active:
            # The unit's immediate re-send of this same press: swallow it.
            return
        self._active = True
        self._fire(PRESS)
        self._cancel = async_call_later(self.hass, DEBOUNCE_SECONDS, self._end)

    @callback
    def _end(self, _now) -> None:
        """Close the debounce window; the next on event is a new press."""
        self._cancel = None
        self._active = False

    @callback
    def _fire(self, event_type: str) -> None:
        """Emit one press event."""
        self._trigger_event(event_type)
        self.async_write_ha_state()

    @callback
    def _stop(self) -> None:
        """Cancel the pending debounce timer, if any."""
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
