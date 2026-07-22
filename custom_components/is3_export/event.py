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

So a press is reported the moment the input goes on, the unit's immediate
re-send of the on state is swallowed for a short window so one press is one
event, and the input is then forced back off locally -- so a delayed or lost
release cannot leave the value on and make the next press look like no change,
which was making taps go missing. Quick taps therefore each register.

The window is kept short deliberately, so it does not swallow genuine quick
taps. The trade-off is that physically holding a button, which the unit
re-broadcasts every second or two, fires a press for each re-broadcast -- fine
for the taps these buttons are used for.
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

# A short window after a press, just long enough to swallow the unit's immediate
# re-send of the on state (a "double tap" of the same press, seen ~0.1s later)
# so one physical press is one event.  Kept short on purpose: a longer window
# would also swallow genuine quick taps.  At its end the input is forced back
# off, so a delayed or lost release cannot leave the value on and make the next
# press look like no change -- which was making taps go missing.
REFRACTORY_SECONDS = 0.5


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
        """Fire once on the leading edge; swallow the rest of the interaction."""
        if not self.coordinator.values.get(self._address):
            # A release, or the end-of-interaction reset: never fires.
            return
        if self._active:
            # A re-broadcast of the on state while still held: swallow it.
            return
        self._active = True
        self._fire(PRESS)
        self._cancel = async_call_later(
            self.hass, REFRACTORY_SECONDS, self._end_interaction
        )

    @callback
    def _end_interaction(self, _now) -> None:
        """Close the window and force the input off, so the next press is fresh."""
        self._cancel = None
        self._active = False
        # Clear the stored value: a lost release could leave it on, and then the
        # next press's on-event would be deduped as no change and never seen.
        self.coordinator.async_reset(self._address)

    @callback
    def _fire(self, event_type: str) -> None:
        """Emit one press event."""
        self._trigger_event(event_type)
        self.async_write_ha_state()

    @callback
    def _stop(self) -> None:
        """Cancel the pending refractory timer, if any."""
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
