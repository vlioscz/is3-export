"""Event platform for the IS3 Export integration.

A wall switch button, and an RF remote's, carries a second function on a long
press that iNELS acts on but cannot put in the export, since it is not a
physical channel.  The unit does report the input going on and off, though, so
the press is timed here and classified when the button is let go: short if it
was held briefly, long past the threshold.

The unit reports a release with a variable delay -- around two seconds even on a
quick tap, seen live -- so classifying on release and timing the two events
apart is steadier than firing at a fixed moment during the hold, and the
threshold is set above that delay.  A release that never arrives (an RF fob's
can be lost) reports nothing rather than guessing.
"""

from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .coordinator import Is3ConfigEntry, Is3Coordinator
from .entity import Is3Entity
from .export import Is3Entry, is_press_button

SHORT_PRESS = "short_press"
LONG_PRESS = "long_press"

# Held this long or more is a long press.  Above iNELS's own threshold on
# purpose: the unit reports a release with a variable delay -- around two seconds
# was seen on a quick tap -- so a lower bar would read those taps as long.
LONG_PRESS_SECONDS = 3.0

# If no release arrives within this long, the off event was lost -- an RF fob's
# especially -- so the input is forced back off, longer than any real hold.
MAX_HOLD_SECONDS = 10.0


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
    """The press of a wall switch or RF remote button, short or long.

    A button is momentary, so it is an event rather than a binary sensor: no
    on/off state to be left stuck when a release is lost.  The press is
    classified when the button is released, by how long it was held: the unit's
    release event can arrive well after the press, so waiting for it and timing
    the two apart is steadier than firing at a fixed moment during the hold.

    A release that never arrives -- lost, as an RF fob's can be -- reports
    nothing rather than guessing, since short and long cannot be told apart then.
    """

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = [SHORT_PRESS, LONG_PRESS]

    def __init__(self, coordinator: Is3Coordinator, entry: Is3Entry) -> None:
        """Bind to the button's input on its own switch."""
        super().__init__(coordinator, entry)
        self._address = entry.address
        self._attr_unique_id = f"{self._attr_unique_id}_event"
        self._attr_name = f"{entry.name.replace('_', ' ')} press"
        self._pressed = False
        self._press_time = 0.0
        self._cancel = None

    async def async_added_to_hass(self) -> None:
        """Watch the input to measure how long the button is held."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_address_listener(
                self._address, self._handle_change
            )
        )
        self.async_on_remove(self._stop)

    @callback
    def _handle_change(self) -> None:
        """React to the input going on or off."""
        pressed = bool(self.coordinator.values.get(self._address))
        if pressed and not self._pressed:
            # Button went down: note when, and arm the lost-release safety net.
            self._pressed = True
            self._press_time = self.hass.loop.time()
            self._stop()
            self._cancel = async_call_later(
                self.hass, MAX_HOLD_SECONDS, self._on_timeout
            )
        elif not pressed and self._pressed:
            # Button released: short or long by how long it was held.
            self._pressed = False
            self._stop()
            held = self.hass.loop.time() - self._press_time
            self._fire(LONG_PRESS if held >= LONG_PRESS_SECONDS else SHORT_PRESS)

    @callback
    def _on_timeout(self, _now) -> None:
        """No release in a plausible time: the off event was lost."""
        # Clear the input without reporting a press -- short and long cannot be
        # told apart now -- so it is not stuck on and the next press is seen.
        self._cancel = None
        self._pressed = False
        self.coordinator.async_reset(self._address)

    @callback
    def _fire(self, event_type: str) -> None:
        """Emit one press event."""
        self._trigger_event(event_type)
        self.async_write_ha_state()

    @callback
    def _stop(self) -> None:
        """Cancel the pending safety timer, if any."""
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
